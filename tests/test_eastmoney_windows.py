"""Native skill regression tests. Broker data and drivers are synthetic."""
from argparse import Namespace
import base64
from datetime import datetime, timedelta
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import time
from types import SimpleNamespace
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / ".claude/skills/eastmoney-windows/scripts"
sys.path.insert(0, str(SCRIPTS))
import windows_bridge as bridge
import windows_core as core
spec = importlib.util.spec_from_file_location("native_eastmoney_cli", SCRIPTS / "eastmoney.py")
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)
sys.path.remove(str(SCRIPTS))


def export(path, *, empty=False, reverse=False, code="1234", held="100", available="100", missing=False):
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    root = ET.Element("worksheet", xmlns=ns)
    data = ET.SubElement(root, "sheetData")
    headers = ["证券代码", "证券名称", "持仓数量", "可用数量", "最新价", "最新市值", "股东账号"]
    values = [code, "合成ETF", held, available, "0.10", "10.00", "SYNTHETIC-ACCOUNT"]
    if reverse:
        headers, values = headers[::-1], values[::-1]
    rows = [["总资产", "可用资金", "证券市值", "导出时间"],
            ["20.01", "10.00", "0" if empty else "10.00", "2026/10/16 12:00"],
            ["unknown"] if missing else headers] + ([] if empty else [values])
    for i, cells in enumerate(rows, 1):
        row = ET.SubElement(data, "row", r=str(i))
        for j, value in enumerate(cells):
            cell = ET.SubElement(row, "c", r=chr(65 + j) + str(i), t="inlineStr")
            ET.SubElement(ET.SubElement(cell, "is"), "t").text = value
    with ZipFile(path, "w") as z:
        z.writestr("xl/worksheets/sheet1.xml", ET.tostring(root))
    return path


@pytest.mark.parametrize("reverse", [False, True])
def test_native_parser_preserves_codes_precision_and_redacts_accounts(tmp_path, reverse):
    data = core.parse_export(export(tmp_path / "持仓.xlsx", reverse=reverse), run_id="synthetic")
    assert data["positions"][0]["证券代码"] == "001234"
    assert data["funds"]["总资产"] == "20.01"
    assert data["reconciliation"]["status"] == "partial"
    assert data["reconciliation"]["assets_minus_market_minus_available_cash"] == "0.01"
    assert "SYNTHETIC-ACCOUNT" not in json.dumps(data)
    assert data["source"]["quote_time"] is None


def test_native_empty_account_requires_a_header_and_old_export_is_rejected(tmp_path):
    data = core.parse_export(export(tmp_path / "empty.xlsx", empty=True), run_id="synthetic")
    assert data["positions"] == [] and data["empty_positions"]
    with pytest.raises(core.BrokerError):
        core.parse_export(export(tmp_path / "missing.xlsx", empty=True, missing=True), run_id="synthetic")
    old = export(tmp_path / "old.xlsx")
    os.utime(old, (1, 1))
    with pytest.raises(core.BrokerError, match="predates"):
        core.parse_export(old, run_id="synthetic", started_at=time.time())


@pytest.mark.parametrize("held,available", [("-1", "0"), ("1.5", "1"), ("100", "101"), ("100", "NaN")])
def test_native_invalid_quantities_cannot_become_success(tmp_path, held, available):
    with pytest.raises(core.BrokerError):
        core.parse_export(export(tmp_path / "x.xlsx", held=held, available=available), run_id="synthetic")


def test_native_timestamps_need_no_iana_database_and_json_is_utf8(tmp_path):
    assert datetime.fromisoformat(core.now()).utcoffset() == timedelta(hours=8)
    target = tmp_path / "中文 空格" / "账户.json"
    bridge.save_json(target, {"message": "证券持仓", "code": "001234"})
    assert json.loads(target.read_bytes().decode("utf-8"))["message"] == "证券持仓"
    with pytest.raises(ValueError):
        bridge.save_json(target, {"value": float("nan")})
    assert json.loads(target.read_text(encoding="utf-8"))["code"] == "001234"
    assert not list(target.parent.glob("*.tmp"))


class Kernel:
    def __init__(self, outcome):
        self.outcome, self.releases, self.closes = outcome, 0, 0
    def CreateMutexW(self, *_): return 987
    def WaitForSingleObject(self, *_): return self.outcome
    def ReleaseMutex(self, *_): self.releases += 1
    def CloseHandle(self, *_): self.closes += 1


@pytest.mark.parametrize("outcome,code,release", [(0x102, "desktop_busy", 0),
                                                (0x80, "desktop_recovery_required", 1),
                                                (0xFFFFFFFF, "lock_failed", 0)])
def test_mutex_errors_never_enter_the_desktop_operation(monkeypatch, outcome, code, release):
    kernel = Kernel(outcome)
    monkeypatch.setattr(bridge, "_kernel", lambda: kernel)
    with pytest.raises(core.BrokerError) as caught:
        with bridge.named_mutex("synthetic"):
            pytest.fail("Desktop operation must not run")
    assert caught.value.code == code and kernel.releases == release and kernel.closes == 1


def test_mutex_is_released_after_operation_failure(monkeypatch):
    kernel = Kernel(0)
    monkeypatch.setattr(bridge, "_kernel", lambda: kernel)
    with pytest.raises(RuntimeError):
        with bridge.desktop_lock(Path("unused")):
            raise RuntimeError("synthetic operation failed")
    assert kernel.releases == kernel.closes == 1


def transport(tmp_path):
    instance = object.__new__(bridge.Bridge)
    instance.output = tmp_path
    instance.powershell = Path("powershell.exe")
    return instance


def test_native_transport_roundtrips_unicode_without_shell_or_shared_paths(tmp_path, monkeypatch):
    instance = transport(tmp_path)
    def run(arguments, **kwargs):
        assert arguments[0] == "powershell.exe" and "prlctl" not in arguments
        assert kwargs["timeout"] == 60 and not kwargs.get("shell")
        bootstrap = base64.b64decode(arguments[-1]).decode("utf-16le")
        assert "ReadAllText" in bootstrap and "-RequestPath" in bootstrap
        request = json.loads(next(tmp_path.glob("request-*.json")).read_text(encoding="utf-8"))
        assert request["echo"] == "证券持仓"
        return SimpleNamespace(returncode=0, stdout=json.dumps(request, ensure_ascii=False).encode("utf-8"))
    monkeypatch.setattr(bridge.subprocess, "run", run)
    assert instance._invoke({"echo": "证券持仓"}, tmp_path)["echo"] == "证券持仓"
    assert not list(tmp_path.glob("request-*.json"))


@pytest.mark.parametrize("raw", [b"not json", b"[]", b"\xff\xfe"])
def test_invalid_powershell_responses_are_driver_errors(tmp_path, monkeypatch, raw):
    monkeypatch.setattr(bridge.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=raw))
    with pytest.raises(core.BrokerError) as caught:
        transport(tmp_path)._invoke({}, tmp_path)
    assert caught.value.code == "invalid_driver_response"
    assert not list(tmp_path.glob("request-*.json"))


def test_native_timeout_removes_request_and_does_not_retry(tmp_path, monkeypatch):
    calls = []
    def timeout(*args, **kwargs):
        calls.append(1)
        raise subprocess.TimeoutExpired("synthetic", 60)
    monkeypatch.setattr(bridge.subprocess, "run", timeout)
    with pytest.raises(core.BrokerError) as caught:
        transport(tmp_path)._invoke({}, tmp_path)
    assert caught.value.code == "desktop_timeout" and len(calls) == 1
    assert not list(tmp_path.glob("request-*.json"))


def test_native_paths_cannot_escape_the_runtime_folder(tmp_path):
    instance = transport(tmp_path / "allowed")
    with pytest.raises(ValueError):
        instance.local_path(tmp_path / "outside.xlsx")


def test_native_cli_simulation_is_offline_and_has_no_vm_options(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "DATA_ROOT", tmp_path)
    assert cli.main(["test", "--scenario", "simulation"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["evidence_kind"] == "simulation" and result["live_submission"] == "not_tested"
    with pytest.raises(SystemExit):
        cli.parser().parse_args(["--vm", "synthetic", "doctor"])


def test_native_auth_loss_marks_snapshot_stale_and_invalidates_calibration(tmp_path):
    class Fake:
        output = tmp_path
        config = {"profile": {"reviewed": True}}
        def run(self, *a, **k): return {"status": "error", "code": "auth_required", "message": "synthetic"}
        def local_path(self, path): return str(path)
        def save_config(self): pass
    instance = Fake()
    bridge.save_json(tmp_path / "latest.json", {"status": "ok", "captured_at": "original"})
    directory = bridge.new_run(tmp_path, "synthetic")
    with pytest.raises(core.BrokerError): cli.snapshot(instance, directory)
    latest = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))
    assert latest["status"] == "stale" and latest["captured_at"] == "original"
    assert instance.config["auth_required"] and not instance.config["profile"]["reviewed"]


def test_native_recovery_uses_same_account_and_a_new_process(tmp_path, monkeypatch):
    class Fake:
        output = tmp_path
        config = {"profile": {"account_marker": "合成****", "reviewed": True}}
        pid = 2
        def run(self, *a, **k): return {"status": "ready", "client": {"pid": self.pid}}
    bridge.save_json(tmp_path / "recovery.json", {"status": "pending", "scenario": "restart", "previous_pid": 1,
                    "transition_confirmed": True, "account_marker": "合成****", "evidence": "synthetic"})
    monkeypatch.setattr(cli, "snapshot", lambda *a: {"positions": []})
    instance = Fake()
    instance.pid = 1
    with pytest.raises(core.BrokerError) as caught:
        cli.test_desktop(instance, Namespace(scenario="resume"), tmp_path)
    assert caught.value.code == "restart_not_confirmed"
    instance.pid = 2
    result = cli.test_desktop(instance, Namespace(scenario="resume"), tmp_path)
    assert result["status"] == "passed" and result["preceding_evidence"] == "synthetic"


def test_standalone_zip_has_complete_resources_and_verified_hashes(tmp_path):
    import hashlib
    package_spec = importlib.util.spec_from_file_location("windows_skill_package", SCRIPTS / "package_skill.py")
    package = importlib.util.module_from_spec(package_spec)
    package_spec.loader.exec_module(package)
    source = tmp_path / "source"
    shutil.copytree(SCRIPTS.parent, source, ignore=shutil.ignore_patterns("__pycache__"))
    (source / "data").mkdir()
    (source / "data/private.py").write_text("SYNTHETIC_SECRET", encoding="utf-8")
    archive_path = tmp_path / "中文 包.zip"
    package.build(archive_path, source)
    assert archive_path.name in archive_path.with_suffix(".zip.sha256").read_text(encoding="utf-8")
    with ZipFile(archive_path) as archive:
        assert archive.testzip() is None
        manifest = json.loads(archive.read("eastmoney-windows/MANIFEST.json"))
        assert "data/private.py" not in manifest["files"]
        for name in package.REQUIRED:
            assert name in manifest["files"]
        for name, meta in manifest["files"].items():
            assert hashlib.sha256(archive.read("eastmoney-windows/" + name)).hexdigest() == meta["sha256"]
    (source / "references/recovery.md").unlink()
    with pytest.raises(ValueError, match="Missing required"):
        package.build(tmp_path / "bad.zip", source)
