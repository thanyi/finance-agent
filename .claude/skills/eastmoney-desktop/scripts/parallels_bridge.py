"""Local Parallels bridge. No account secrets and no arbitrary broker commands."""
from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import os
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path, PureWindowsPath
import subprocess
import time
import uuid
from zoneinfo import ZoneInfo

from broker_core import BrokerError, now

ROOT = Path(__file__).resolve().parents[4]
SCRIPT = Path(__file__).resolve().with_name("desktop.ps1")


def save_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    temp.replace(path)


@contextmanager
def desktop_lock(output: Path):
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    output.chmod(0o700)
    with (output / "desktop.lock").open("a+") as stream:
        os.chmod(stream.name, 0o600)
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BrokerError("desktop_busy", "Another eastmoney-desktop process owns the desktop") from None
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def ps_quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


class Bridge:
    def __init__(self, output: Path, vm: str | None = None, guest_root: str | None = None, client_pid: int | None = None):
        self.output = output.resolve()
        self.output.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.output.chmod(0o700)
        self.config_path = self.output / "config.json"
        self.config = json.loads(self.config_path.read_text()) if self.config_path.exists() else {}
        self.client_pid = client_pid
        self.vm = vm or self.config.get("vm") or self._discover_vm()
        self.guest_root = guest_root or self.config.get("guest_root") or self._discover_share()

    def _run(self, arguments: list[str], timeout=60) -> str:
        try:
            proc = subprocess.run(["prlctl", *arguments], capture_output=True, timeout=timeout)
        except FileNotFoundError:
            raise BrokerError("parallels_missing", "prlctl was not found") from None
        except subprocess.TimeoutExpired:
            raise BrokerError("desktop_timeout", "Desktop operation exceeded its deadline; inspect the current UI before retrying") from None
        if proc.returncode:
            raise BrokerError("parallels_error", proc.stderr.decode("utf-8", errors="replace")[:800])
        return proc.stdout.decode("utf-8-sig", errors="replace").strip()

    def _discover_vm(self) -> str:
        rows = self._run(["list", "-a", "-o", "uuid,name,status"]).splitlines()[1:]
        candidates = [line.split()[0] for line in rows if line.rstrip().endswith("running") and "Windows" in line]
        if len(candidates) != 1:
            raise BrokerError("vm_selection_required", "Use --vm to select exactly one running Windows VM")
        return candidates[0]

    def short_ps(self, source: str) -> str:
        prefix = "$ProgressPreference='SilentlyContinue';$ErrorActionPreference='Stop';$OutputEncoding=[Console]::OutputEncoding=[Text.UTF8Encoding]::new();"
        encoded = base64.b64encode((prefix + source).encode("utf-16le")).decode("ascii")
        if len(encoded) > 3500:
            raise BrokerError("bridge_payload_too_long", "Use the shared script and JSON request for longer operations")
        return self._run(["exec", self.vm, "--current-user", "powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-EncodedCommand", encoded])

    def _discover_share(self) -> str:
        try:
            relative = ROOT.relative_to(Path.home())
        except ValueError:
            raise BrokerError("share_selection_required", "Project is outside the shared home; specify --guest-root") from None
        suffix = str(PureWindowsPath(*relative.parts))
        for candidate in ("\\\\Mac\\Home\\" + suffix, "C:\\Mac\\Home\\" + suffix):
            marker = candidate + "\\README.md"
            if self.short_ps("[IO.File]::Exists(" + ps_quote(marker) + ")") == "True":
                return candidate
        raise BrokerError("share_unavailable", "Project share is not accessible from Windows; specify --guest-root")

    def guest_path(self, path: Path) -> str:
        try:
            relative = path.resolve().relative_to(ROOT)
        except ValueError:
            raise BrokerError("outside_share", "Output must be inside this project shared directory") from None
        return self.guest_root + "\\" + str(PureWindowsPath(*relative.parts))

    def run(self, action: str, run_dir: Path, **parameters) -> dict:
        request = {"action": action, "profile": self.config.get("profile"),
                   "client_pid": self.client_pid,
                   "auth_required": self.config.get("auth_required", False), **parameters}
        request_path = run_dir / ("request-" + uuid.uuid4().hex + ".json")
        save_json(request_path, request)
        source = "& ([scriptblock]::Create([IO.File]::ReadAllText(" + ps_quote(self.guest_path(SCRIPT)) + "))) -RequestJson ([IO.File]::ReadAllText(" + ps_quote(self.guest_path(request_path)) + "))"
        try:
            raw = self.short_ps(source)
            result = json.loads(raw)
        except json.JSONDecodeError:
            raise BrokerError("invalid_driver_response", "Desktop driver did not return valid JSON") from None
        finally:
            request_path.unlink(missing_ok=True)
        return result

    def capture(self, path: Path) -> str:
        self._run(["capture", self.vm, "--file", str(path)])
        path.chmod(0o600)
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def save_config(self):
        self.config.update(vm=self.vm, guest_root=self.guest_root)
        save_json(self.config_path, self.config)


def new_run(output: Path, command: str) -> Path:
    name = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:10] + "-" + command
    directory = output / "runs" / name
    directory.mkdir(parents=True, mode=0o700)
    return directory
