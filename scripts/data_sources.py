"""Bounded, auditable read-only calls to finance data providers."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from importlib.metadata import version, PackageNotFoundError

import pandas as pd


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def safe_error(exc):
    message = str(exc)
    for key in ("TAVILY_API_KEY", "TUSHARE_TOKEN"):
        secret = os.environ.get(key)
        if secret:
            message = message.replace(secret, "[REDACTED]")
    return f"{type(exc).__name__}: {message[:400]}"


def provider_call(provider, endpoint, params):
    if provider == "akshare":
        import akshare as ak
        return getattr(ak, endpoint)(**params)
    if provider == "baostock":
        import baostock as bs
        login = bs.login()
        if login.error_code != "0":
            raise RuntimeError(f"BaoStock login {login.error_code}: {login.error_msg}")
        try:
            rs = getattr(bs, endpoint)(**params)
            if rs.error_code != "0":
                raise RuntimeError(f"BaoStock query {rs.error_code}: {rs.error_msg}")
            rows = []
            while rs.next():
                rows.append(rs.get_row_data())
            if rs.error_code != "0":
                raise RuntimeError(f"BaoStock iteration {rs.error_code}: {rs.error_msg}")
            return pd.DataFrame(rows, columns=rs.fields)
        finally:
            bs.logout()
    if provider == "tushare":
        import requests
        token = os.environ.get("TUSHARE_TOKEN")
        if not token:
            raise ValueError("未配置 TUSHARE_TOKEN")
        response = requests.post("https://api.tushare.pro", json={
            "api_name": endpoint, "token": token, "params": params, "fields": ""
        }, timeout=(10, 25))
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != 0:
            raise ValueError(f"Tushare: {payload.get('msg')}")
        return pd.DataFrame(payload["data"]["items"], columns=payload["data"]["fields"])
    raise ValueError(f"Unknown provider: {provider}")


def isolated_call(provider, endpoint, params, timeout):
    # A process deadline also bounds SDK calls that omit HTTP/socket timeouts.
    with tempfile.TemporaryDirectory(prefix="finance-api-") as tmp:
        output = Path(tmp) / "result.json"
        try:
            result = subprocess.run(
                [sys.executable, str(Path(__file__).resolve()), provider, endpoint,
                 json.dumps(params, ensure_ascii=False), str(output)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=timeout, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(f"{provider}.{endpoint} exceeded {timeout}s") from exc
        if result.returncode or not output.exists():
            raise RuntimeError(f"provider process exited {result.returncode}")
        payload = json.loads(output.read_text(encoding="utf-8"))
        if "error" in payload:
            raise RuntimeError(payload["error"])
        return pd.DataFrame(payload["rows"], columns=payload["columns"])


class Collector:
    def __init__(self, output, timeout=35, retries=1, caller=isolated_call):
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=True)
        self.timeout, self.retries, self.caller = timeout, retries, caller
        self.datasets = {}
        self.versions = {}
        for package in ("akshare", "baostock", "pandas", "numpy"):
            try:
                self.versions[package] = version(package)
            except PackageNotFoundError:
                self.versions[package] = "not installed"

    def fetch(self, key, candidates, normalize=None, units=None):
        attempts = []
        for provider, endpoint, params in candidates:
            for retry in range(self.retries + 1):
                attempt = {"provider": provider, "endpoint": endpoint, "params": params,
                           "queried_at": utc_now(), "attempt": retry + 1}
                try:
                    raw = self.caller(provider, endpoint, params, self.timeout)
                    if not isinstance(raw, pd.DataFrame) or raw.empty:
                        raise ValueError("接口返回空数据")
                    raw_name = f"{key}_{len(attempts) + 1}_raw.csv"
                    raw.to_csv(self.output / raw_name, index=False)
                    attempt["raw_file"] = raw_name
                    frame = normalize(raw.copy()) if normalize else raw
                    if frame.empty:
                        raise ValueError("校验/日期过滤后无有效数据")
                    name = f"{key}.csv"
                    frame.to_csv(self.output / name, index=False)
                    attempt["status"] = "ok"
                    attempts.append(attempt)
                    self.datasets[key] = {
                        "status": "ok", "source": f"{provider}.{endpoint}",
                        "queried_at": attempt["queried_at"], "file": name,
                        "rows": len(frame), "units": units or "保留原接口单位，见原始列名/接口文档",
                        "attempts": attempts,
                    }
                    return frame
                except Exception as exc:
                    attempt.update(status="failed", error=safe_error(exc))
                    attempts.append(attempt)
                    # Empty data and schema errors do not benefit from retries.
                    transient = any(word in attempt["error"].lower() for word in
                                    ("timeout", "connection", "remote", "429", "502", "503"))
                    if not transient or retry == self.retries:
                        break
                    time.sleep(min(2 ** retry, 4))
        self.datasets[key] = {"status": "missing", "reason": "所有候选接口失败，见 attempts",
                              "attempts": attempts}
        return pd.DataFrame()


if __name__ == "__main__":
    provider, endpoint, params, output = sys.argv[1:]
    try:
        frame = provider_call(provider, endpoint, json.loads(params))
        payload = {"columns": list(frame.columns),
                   "rows": json.loads(frame.to_json(orient="values", date_format="iso", force_ascii=False))}
    except Exception as exc:
        payload = {"error": safe_error(exc)}
    Path(output).write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False), encoding="utf-8")
