"""Synthetic exports and isolated drivers; never connects to a securities account."""
from argparse import Namespace
import json
import os
from pathlib import Path
import sys
import time
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest

SKILL_SCRIPTS = Path(__file__).resolve().parents[1] / ".claude/skills/eastmoney-desktop/scripts"
sys.path.insert(0, str(SKILL_SCRIPTS))
from broker_core import BrokerError, SimulatedOrders, parse_export, reconcile, simulation_report
from parallels_bridge import desktop_lock, save_json
import eastmoney


def export_fixture(path, *, code="001234", held="100", available="100", market="10.00",
                   total="20.10", cash="10.00", empty=False, missing=False, formula=False, reverse=False):
    # Synthetic amounts are test inputs, not real market or account observations.
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    root = ET.Element("worksheet", xmlns=ns)
    data = ET.SubElement(root, "sheetData")
    headers = ["证券代码", "证券名称", "持仓数量", "可用数量", "最新价", "最新市值", "股东账号", "币种"]
    values = [code, "合成测试ETF", held, available, "0.10", market, "SYNTHETIC-ACCOUNT", "人民币"]
    if reverse:
        headers, values = list(reversed(headers)), list(reversed(values))
    rows = [["证券市值", "可用资金", "总资产", "导出时间"],
            ["0" if empty else market, cash, total, "2026/10/15 12:00"],
            ["unrecognized"] if missing else headers]
    if not empty:
        rows.append(values)
    for i, cells in enumerate(rows, 1):
        row = ET.SubElement(data, "row", r=str(i))
        for j, value in enumerate(cells):
            cell = ET.SubElement(row, "c", r=chr(65 + j) + str(i), t="inlineStr")
            ET.SubElement(ET.SubElement(cell, "is"), "t").text = value
            if formula and i == 4 and j == 0:
                ET.SubElement(cell, "f").text = "1+1"
    with ZipFile(path, "w") as archive:
        archive.writestr("xl/worksheets/sheet1.xml", ET.tostring(root))
    return path


def test_columns_can_reorder_and_account_identifiers_are_removed(tmp_path):
    result = parse_export(export_fixture(tmp_path / "a.xlsx", reverse=True), run_id="synthetic")
    assert result["positions"][0]["证券代码"] == "001234"
    assert "SYNTHETIC-ACCOUNT" not in json.dumps(result)
    assert result["source"]["quote_time"] is None
    assert result["reconciliation"]["status"] == "partial"


def test_numeric_security_code_is_padded_without_losing_zeroes(tmp_path):
    result = parse_export(export_fixture(tmp_path / "a.xlsx", code="1234"), run_id="synthetic")
    assert result["positions"][0]["证券代码"] == "001234"


def test_missing_header_is_not_reported_as_empty(tmp_path):
    with pytest.raises(BrokerError, match="Recognizable holdings header"):
        parse_export(export_fixture(tmp_path / "a.xlsx", empty=True, missing=True), run_id="synthetic")


def test_empty_holdings_requires_a_valid_table_header(tmp_path):
    result = parse_export(export_fixture(tmp_path / "a.xlsx", empty=True, market="0"), run_id="synthetic")
    assert result["empty_positions"] and result["positions"] == []
    assert result["reconciliation"]["checks"][0]["status"] == "passed"


@pytest.mark.parametrize("held,available", [("-1", "0"), ("100", "101"), ("1.5", "1"), ("100", "NaN"), ("100", "")])
def test_quantity_errors_fail_the_capture(tmp_path, held, available):
    with pytest.raises(BrokerError):
        parse_export(export_fixture(tmp_path / "a.xlsx", held=held, available=available), run_id="synthetic")


def test_money_uses_exact_decimal_and_available_cash_is_not_balance(tmp_path):
    result = parse_export(export_fixture(tmp_path / "a.xlsx", market="0.10", cash="0.20", total="0.31"), run_id="synthetic")
    check = result["reconciliation"]
    assert check["assets_minus_market_minus_available_cash"] == "0.01"
    assert check["status"] == "partial"  # A nonzero residual must not be fabricated away.
    assert result["funds"]["总资产"] == "0.31"


def test_bad_zip_and_formulas_are_not_evidence(tmp_path):
    bad = tmp_path / "bad.xlsx"
    bad.write_bytes(b"not a workbook")
    with pytest.raises(BrokerError):
        parse_export(bad, run_id="synthetic")
    with pytest.raises(BrokerError, match="Formula"):
        parse_export(export_fixture(tmp_path / "formula.xlsx", formula=True), run_id="synthetic")


def test_old_export_cannot_be_used_for_a_new_capture(tmp_path):
    path = export_fixture(tmp_path / "a.xlsx")
    os.utime(path, (1, 1))
    with pytest.raises(BrokerError, match="predates"):
        parse_export(path, run_id="synthetic", started_at=time.time())


def test_full_reconciliation_requires_same_snapshot_and_all_components(tmp_path):
    data = parse_export(export_fixture(tmp_path / "a.xlsx"), run_id="synthetic")
    evidence = dict(source_sha256="synthetic", snapshot_sha256=data["source"]["sha256"], currency="CNY",
                    cash_balance="10.10", other_assets="0", liabilities="0", frozen_cash="0.10",
                    order_frozen_cash="0.10", cash_includes_frozen=True, accounting_identity_verified=True)
    assert reconcile(data, evidence)["status"] == "passed"
    evidence["snapshot_sha256"] = "other-snapshot"
    with pytest.raises(BrokerError):
        reconcile(data, evidence)
    evidence.pop("cash_balance")
    with pytest.raises(BrokerError):
        reconcile(data, evidence)


def test_simulation_is_isolated_and_exercises_cancel_race():
    report = simulation_report()
    assert report["evidence_kind"] == "simulation"
    assert report["live_cancellation"] == "not_tested"
    broker = SimulatedOrders()
    broker.submit("one", 100)
    broker.event("one", "cancel")
    assert broker.event("one", "fill", 100)["status"] == "filled"
    with pytest.raises(BrokerError):
        broker.event("one", "cancel_ack")


def test_unknown_submission_is_not_reissued_or_cancelled():
    broker = SimulatedOrders()
    broker.submit("one", 100)
    broker.event("one", "timeout")
    assert broker.submit("one", 100)["status"] == "unknown"
    assert len(broker.orders) == 1
    with pytest.raises(BrokerError):
        broker.event("one", "cancel")
    with pytest.raises(BrokerError):
        broker.submit("one", 200)


def test_two_desktop_owners_cannot_overlap(tmp_path):
    with desktop_lock(tmp_path):
        with pytest.raises(BrokerError, match="owns the desktop"):
            with desktop_lock(tmp_path):
                raise AssertionError("Second desktop owner was allowed")


def test_failed_capture_marks_previous_snapshot_stale_without_changing_capture_time(tmp_path):
    save_json(tmp_path / "latest.json", {"status": "ok", "captured_at": "original", "positions": ["synthetic"]})
    eastmoney.mark_stale(tmp_path, "auth_required")
    result = json.loads((tmp_path / "latest.json").read_text())
    assert result["status"] == "stale" and result["captured_at"] == "original"
    assert result["positions"] == ["synthetic"]


def test_natural_auth_timeout_injection_pauses_watch_and_preserves_progress(tmp_path, monkeypatch):
    class FakeBridge:
        output = tmp_path
    directory = tmp_path / "watch"
    directory.mkdir()
    calls = []
    def capture(bridge, run):
        calls.append(run)
        if len(calls) == 1:
            return {"status": "ok", "positions": []}
        raise BrokerError("auth_required", "synthetic expired session")
    monkeypatch.setattr(eastmoney, "snapshot", capture)
    monkeypatch.setattr(eastmoney, "capture_safely", lambda *args: None)
    monkeypatch.setattr(eastmoney.time, "sleep", lambda seconds: None)
    args = Namespace(resume=None, interval=30, count=10)
    result = eastmoney.watch(FakeBridge(), args, directory)
    assert result["status"] == "waiting_for_login" and result["completed"] == 1
    state = json.loads((directory / "watch.json").read_text())
    assert state["events"][-1]["status"] == "auth_required"
    assert len(calls) == 2
    monkeypatch.setattr(eastmoney, "snapshot", lambda *args: {"status": "ok", "positions": []})
    args.resume = directory
    result = eastmoney.watch(FakeBridge(), args, directory)
    assert result["completed"] == 10 and result["status"] == "passed"


def test_layout_failure_does_not_retry_desktop_input(tmp_path, monkeypatch):
    class FakeBridge:
        output = tmp_path
    directory = tmp_path / "watch"
    directory.mkdir()
    calls = []
    def capture(*args):
        calls.append(1)
        raise BrokerError("layout_changed", "synthetic resized window")
    monkeypatch.setattr(eastmoney, "snapshot", capture)
    monkeypatch.setattr(eastmoney, "capture_safely", lambda *args: None)
    result = eastmoney.watch(FakeBridge(), Namespace(resume=None, interval=30, count=10), directory)
    assert result["status"] == "stopped" and result["completed"] == 0 and len(calls) == 1


def test_identical_new_exports_are_valid_independent_captures(tmp_path):
    one = parse_export(export_fixture(tmp_path / "one.xlsx"), run_id="one", started_at=time.time() - 1)
    two = parse_export(export_fixture(tmp_path / "two.xlsx"), run_id="two", started_at=time.time() - 1)
    assert one["positions"] == two["positions"]
    assert one["run_id"] != two["run_id"] and two["status"] == "ok"


def test_driver_success_without_a_readable_export_preserves_previous_capture_as_stale(tmp_path):
    class FakeBridge:
        output = tmp_path
        def guest_path(self, path): return str(path)
        def run(self, *args, **kwargs): return {"status": "ok"}
    save_json(tmp_path / "latest.json", {"status": "ok", "captured_at": "original", "positions": []})
    directory = tmp_path / "run"
    directory.mkdir()
    with pytest.raises(BrokerError):
        eastmoney.snapshot(FakeBridge(), directory)
    assert json.loads((tmp_path / "latest.json").read_text())["status"] == "stale"


def test_resume_without_a_preceding_recovery_test_cannot_claim_recovery_passed(tmp_path):
    class FakeBridge:
        output = tmp_path
        config = {}
        def run(self, *args, **kwargs):
            raise AssertionError("No desktop action should occur without a pending recovery")
    directory = tmp_path / "run"
    directory.mkdir()
    with pytest.raises(BrokerError):
        eastmoney.test_desktop(FakeBridge(), Namespace(scenario="resume"), directory)


def form_bridge(tmp_path, monkeypatch, *, existing=False, cleanup_error=False, capture_error=False):
    class FakeBridge:
        output = tmp_path
        config = {"profile": {"account_marker": "SYNTHETIC****"}}
        calls = []
        def run(self, action, *args, **kwargs):
            self.calls.append(action)
            if action == "form_fill":
                if existing:
                    return {"status": "error", "code": "existing_draft", "message": "synthetic existing draft", "draft_written": False}
                return {"status": "ok", "readback": {"code": "515880", "price": "0.10", "quantity": "100"}}
            if action == "form_clear":
                if cleanup_error:
                    raise BrokerError("desktop_timeout", "synthetic cleanup timeout")
                return {"status": "ok", "values": {"code": "", "price": "", "quantity": ""}}
            raise AssertionError(action)
        def capture(self, path):
            if capture_error and path.name == "filled.png":
                raise BrokerError("capture_failed", "synthetic screenshot failure")
            path.write_bytes(b"synthetic-image-placeholder")
            return "synthetic-sha"
    monkeypatch.setattr(eastmoney, "snapshot", lambda *a: {"positions": [{"证券代码": "515880", "最新价": "0.10", "可用数量": "100"}]})
    return FakeBridge()


def test_existing_draft_is_not_overwritten_or_cleared(tmp_path, monkeypatch):
    bridge = form_bridge(tmp_path, monkeypatch, existing=True)
    result = eastmoney.form_check(bridge, Namespace(symbol="515880", side="buy", quantity=100), tmp_path)
    assert result["code"] == "existing_draft"
    assert bridge.calls == ["form_fill"]
    assert result["cleanup"]["status"] == "not_needed"


def test_draft_is_cleared_even_when_filled_screenshot_fails(tmp_path, monkeypatch):
    bridge = form_bridge(tmp_path, monkeypatch, capture_error=True)
    result = eastmoney.form_check(bridge, Namespace(symbol="515880", side="buy", quantity=100), tmp_path)
    assert result["status"] == "failed" and result["submitted"] is False
    assert bridge.calls == ["form_fill", "form_clear"]
    assert result["cleanup"]["status"] == "ok"


def test_cleanup_transport_failure_is_saved_not_hidden(tmp_path, monkeypatch):
    bridge = form_bridge(tmp_path, monkeypatch, cleanup_error=True)
    result = eastmoney.form_check(bridge, Namespace(symbol="515880", side="buy", quantity=100), tmp_path)
    assert result["status"] == "failed" and result["code"] == "draft_cleanup_failed"
    assert json.loads((tmp_path / "form-check.json").read_text())["cleanup"]["code"] == "desktop_timeout"


def test_supplemental_evidence_requires_actual_source_file_and_matching_hash(tmp_path):
    source = tmp_path / "synthetic-evidence.txt"
    source.write_text("synthetic source")
    evidence = tmp_path / "evidence.json"
    evidence.write_text(json.dumps({"source_file": source.name, "source_sha256": "wrong"}))
    with pytest.raises(BrokerError):
        eastmoney.load_funds_evidence(evidence, tmp_path)


def test_current_client_selection_can_be_explicit_for_every_command():
    args = eastmoney.parser().parse_args(["--client-pid", "123", "snapshot"])
    assert args.client_pid == 123
    args = eastmoney.parser().parse_args(["doctor", "--client-pid", "456"])
    assert args.client_pid == 456


def test_runtime_output_cannot_be_redirected_into_tracked_directories(tmp_path):
    target = tmp_path / "unexpected-output"
    with pytest.raises(BrokerError):
        eastmoney.main(["--output", str(target), "test", "--scenario", "simulation"])
    assert not target.exists()


def test_observed_auth_loss_invalidates_calibration_and_preserves_old_snapshot(tmp_path):
    class FakeBridge:
        output = tmp_path
        config = {"profile": {"reviewed": True}}
        saves = 0
        def guest_path(self, path): return str(path)
        def run(self, *args, **kwargs):
            return {"status": "error", "code": "auth_required", "message": "synthetic auth loss"}
        def save_config(self): self.saves += 1
    bridge = FakeBridge()
    save_json(tmp_path / "latest.json", {"status": "ok", "captured_at": "original"})
    run = tmp_path / "run"
    run.mkdir()
    with pytest.raises(BrokerError):
        eastmoney.snapshot(bridge, run)
    assert bridge.config["auth_required"] is True
    assert bridge.config["profile"]["reviewed"] is False and bridge.saves == 1
    assert json.loads((tmp_path / "latest.json").read_text())["captured_at"] == "original"


def test_restart_that_only_opens_a_confirmation_is_not_reported_passed(tmp_path, monkeypatch):
    class FakeBridge:
        output = tmp_path
        config = {"profile": {"account_marker": "SYNTHETIC****"}}
        def run(self, action, *args, **kwargs):
            if action == "doctor": return {"status": "ready", "client": {"pid": 123}}
            return {"status": "error", "code": "restart_pending", "message": "synthetic confirmation"}
    monkeypatch.setattr(eastmoney, "capture_safely", lambda *args: None)
    result = eastmoney.test_desktop(FakeBridge(), Namespace(scenario="restart", reviewed_screenshot=None), tmp_path)
    assert result["status"] == "pending_manual_review"
    assert not (tmp_path / "recovery.json").exists()


def test_resumed_capture_is_linked_to_the_actual_recovery_event(tmp_path, monkeypatch):
    class FakeBridge:
        output = tmp_path
        config = {"profile": {"account_marker": "SYNTHETIC****", "reviewed": True}}
        def run(self, *args, **kwargs): return {"status": "ready", "client": {"pid": 456}}
    save_json(tmp_path / "recovery.json", {"status": "pending", "scenario": "restart", "previous_pid": 123,
                                           "account_marker": "SYNTHETIC****", "transition_confirmed": True,
                                           "evidence": "synthetic-previous-run"})
    monkeypatch.setattr(eastmoney, "snapshot", lambda *args: {"positions": []})
    result = eastmoney.test_desktop(FakeBridge(), Namespace(scenario="resume"), tmp_path)
    assert result["status"] == "passed" and result["preceding_evidence"] == "synthetic-previous-run"
    assert json.loads((tmp_path / "recovery.json").read_text())["status"] == "passed"


def test_restart_review_binds_only_one_current_promotion_dialog(tmp_path, monkeypatch):
    class FakeBridge:
        output = tmp_path
        config = {"profile": {"account_marker": "SYNTHETIC****"}}
        def run(self, action, *args, **kwargs):
            if action == "doctor": return {"status": "unexpected_dialog", "client": {"pid": 123}}
            review = kwargs["confirmation_review"]
            assert action == "restart" and review["handle"] == 321 and review["pid"] == 123
            assert review["class"] == "#32770" and review["title"] == "提示"
            assert review["screenshot_sha256"]
            return {"status": "error", "code": "restart_pending", "message": "synthetic"}
    monkeypatch.setattr(eastmoney, "capture_safely", lambda *args: None)
    evidence = tmp_path / "screen.png"
    evidence.write_bytes(b"synthetic screenshot")
    window = {"visible": True, "class": "#32770", "title": "提示", "handle": 321,
              "pid": 123, "dpi": 192, "rect": {"width": 1160, "height": 780}}
    save_json(tmp_path / "driver.json", {"windows": [window]})
    args = Namespace(scenario="restart", reviewed_screenshot=evidence)
    assert eastmoney.test_desktop(FakeBridge(), args, tmp_path)["status"] == "pending_manual_review"
    save_json(tmp_path / "driver.json", {"windows": [window, window]})
    with pytest.raises(BrokerError, match="current exit confirmation"):
        eastmoney.test_desktop(FakeBridge(), args, tmp_path)


@pytest.mark.parametrize("case", ["missing", "expired", "unknown", "duplicate", "matching"])
def test_dismiss_ipo_only_dispatches_for_one_fresh_reviewed_dialog(tmp_path, monkeypatch, case):
    class FakeBridge:
        output = tmp_path
        config = {}
        calls = []
        def run(self, action, directory, **kwargs):
            self.calls.append(action)
            assert action == "dismiss_ipo"
            review = kwargs["dismiss_review"]
            assert review["handle"] == 321 and review["pid"] == 123
            assert review["version"] == "synthetic" and review["screenshot_sha256"]
            return {"status": "ready"}
        def save_config(self): pass
    bridge = FakeBridge()
    monkeypatch.setattr(eastmoney, "capture_safely", lambda *args: None)
    evidence = tmp_path / "screen.png"
    evidence.write_bytes(b"synthetic screenshot")
    window = {"visible": True, "class": "TradeIpoDuiDlg", "title": "TradeIpoDuiDlg",
              "handle": 321, "pid": 123, "dpi": 192, "rect": {"width": 1240, "height": 820}}
    if case == "unknown": window["class"] = "UnknownDialog"
    save_json(tmp_path / "driver.json", {"windows": [window] * (2 if case == "duplicate" else 1),
                                         "client": {"version": "synthetic"}})
    if case == "expired": os.utime(evidence, (time.time() - 301, time.time() - 301))
    args = Namespace(dismiss_ipo=True, reviewed_screenshot=None if case == "missing" else evidence,
                     start_client=False, calibrate=False, open_trade=False, client_exe=None, client_pid=None)
    if case == "matching":
        assert eastmoney.doctor(bridge, args, tmp_path)["status"] == "ready"
        assert bridge.calls == ["dismiss_ipo"]
    else:
        with pytest.raises(BrokerError): eastmoney.doctor(bridge, args, tmp_path)
        assert bridge.calls == []
