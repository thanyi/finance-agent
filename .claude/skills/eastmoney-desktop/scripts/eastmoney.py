#!/usr/bin/env python3
"""Project-local Eastmoney desktop diagnostics. Real order submission is absent."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys
import time

from broker_core import BrokerError, money, now, parse_export, reconcile, simulation_report
from parallels_bridge import Bridge, ROOT, desktop_lock, new_run, save_json


ANCHORS = {
    "refresh": [0.945, 0.725], "grid": [0.62, 0.84],
    "buy_nav": [0.10, 0.168], "sell_nav": [0.10, 0.225],
    "code": [0.407, 0.205], "price": [0.404, 0.358], "quantity": [0.405, 0.462],
    "funds": [0.837, 0.725], "logout": [0.785, 0.037],
}


def emit(value):
    print(json.dumps(value, ensure_ascii=False, allow_nan=False), flush=True)


def require_ok(result: dict):
    if result.get("status") == "error":
        raise BrokerError(result.get("code", "driver_error"), result.get("message", "Desktop error"))
    return result


def mark_stale(output: Path, reason: str):
    path = output / "latest.json"
    if path.exists():
        latest = json.loads(path.read_text())
        latest.update(status="stale", stale_reason=reason, attempted_at=now())
        save_json(path, latest)


def snapshot(bridge: Bridge, run_dir: Path) -> dict:
    started = time.time()
    raw_path = run_dir / "holdings.xlsx"
    try:
        result = require_ok(bridge.run("snapshot", run_dir, output_file=bridge.guest_path(raw_path)))
        raw_path.chmod(0o600)
        data = parse_export(raw_path, run_id=run_dir.name, started_at=started)
        data["source"]["client_version"] = result.get("client_version")
        data["source"]["refresh_requested"] = result.get("refreshed", False)
        data["snapshot_path"] = str((run_dir / "snapshot.json").relative_to(bridge.output))
        save_json(run_dir / "snapshot.json", data)
        save_json(bridge.output / "latest.json", data)
        save_json(run_dir / "driver.json", result)
        return data
    except BrokerError as exc:
        mark_stale(bridge.output, exc.code)
        if exc.code == "auth_required":
            bridge.config["auth_required"] = True
            invalidate_calibration(bridge)
        save_json(run_dir / "failure.json", {"status": exc.code, "message": str(exc), "at": now()})
        raise
    except OSError:
        mark_stale(bridge.output, "export_unreadable")
        save_json(run_dir / "failure.json", {"status": "export_unreadable", "at": now()})
        raise BrokerError("export_unreadable", "Driver completed but the new shared export is not readable") from None


def invalidate_calibration(bridge):
    if bridge.config.get("profile"):
        bridge.config["profile"]["reviewed"] = False
    bridge.save_config()


def capture_safely(bridge, path):
    try:
        return bridge.capture(path)
    except BrokerError:
        return None


def load_funds_evidence(path: Path, output: Path) -> dict:
    evidence = json.loads(path.read_text())
    if not isinstance(evidence, dict) or not isinstance(evidence.get("source_file"), str):
        raise BrokerError("incomplete_evidence", "Supplemental evidence needs a source_file string")
    source = (path.parent / evidence.get("source_file", "")).resolve()
    try:
        source.relative_to(output)
    except ValueError:
        raise BrokerError("incompatible_evidence", "Supplemental evidence must refer to a local runtime artifact") from None
    if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != evidence.get("source_sha256"):
        raise BrokerError("incompatible_evidence", "Supplemental source artifact or its hash is missing or mismatched")
    return evidence


def doctor(bridge, args, directory):
    restore_layout = None
    dismiss_review = None
    if getattr(args, "dismiss_ipo", False):
        if not args.reviewed_screenshot:
            raise BrokerError("screenshot_review_required", "Review the Today Subscription dialog before closing it")
        evidence = args.reviewed_screenshot.resolve()
        evidence.relative_to(bridge.output)
        reviewed = json.loads(evidence.with_name("driver.json").read_text())
        if time.time() - evidence.stat().st_mtime > 300:
            raise BrokerError("screenshot_expired", "Review a fresh diagnostic screenshot")
        dialogs = [w for w in reviewed["windows"] if w["visible"] and
                   w["class"] == "TradeIpoDuiDlg" and w["title"] == "TradeIpoDuiDlg"]
        if len(dialogs) != 1:
            raise BrokerError("unexpected_dialog", "Expected one reviewed Today Subscription dialog")
        dismiss_review = {**dialogs[0], "version": reviewed["client"]["version"],
                          "screenshot_sha256": hashlib.sha256(evidence.read_bytes()).hexdigest()}
    if args.open_trade and args.reviewed_screenshot:
        evidence = args.reviewed_screenshot.resolve()
        evidence.relative_to(bridge.output)
        reviewed = json.loads(evidence.with_name("driver.json").read_text())
        if time.time() - evidence.stat().st_mtime > 300:
            raise BrokerError("screenshot_expired", "Review a fresh diagnostic screenshot")
        if reviewed.get("status") == "trade_unavailable":
            main = [w for w in reviewed["windows"] if w["title"] == "东方财富终端"]
            if len(main) != 1:
                raise BrokerError("layout_changed", "Reviewed main window is ambiguous")
            restore_layout = {"width": main[0]["rect"]["width"], "height": main[0]["rect"]["height"],
                              "dpi": main[0]["dpi"], "anchor": [0.388, 0.913],
                              "screenshot_sha256": hashlib.sha256(evidence.read_bytes()).hexdigest()}
    if dismiss_review and (args.start_client or args.calibrate or args.open_trade):
        raise BrokerError("invalid_local_input", "Close the reviewed dialog separately, then inspect the new screenshot")
    result = bridge.run("dismiss_ipo" if dismiss_review else "start" if args.start_client else "doctor", directory,
                        client_exe=args.client_exe or bridge.config.get("client_exe"),
                        client_pid=args.client_pid, focus=True, open_trade=args.open_trade,
                        restore_layout=restore_layout, dismiss_review=dismiss_review)
    save_json(directory / "driver.json", result)
    capture_safely(bridge, directory / "screen.png")
    require_ok(result)
    if result.get("client"):
        bridge.config["client_exe"] = result["client"]["path"]
    if args.calibrate:
        if result.get("status") != "ready":
            raise BrokerError(result.get("status", "not_ready"), "Open and log in to the trading window before calibration")
        if not args.reviewed_screenshot:
            raise BrokerError("screenshot_review_required", "Inspect screen.png, then provide --reviewed-screenshot from that diagnostic run")
        evidence = args.reviewed_screenshot.resolve()
        evidence.relative_to(bridge.output)
        original = json.loads(evidence.with_name("driver.json").read_text())
        if time.time() - evidence.stat().st_mtime > 300:
            raise BrokerError("screenshot_expired", "Calibration screenshot is older than five minutes")
        for key in ("width", "height"):
            if original["trade"]["rect"][key] != result["trade"]["rect"][key]:
                raise BrokerError("layout_changed", "Geometry changed since the reviewed screenshot")
        if original["client"]["version"] != result["client"]["version"] or original["trade"]["dpi"] != result["trade"]["dpi"]:
            raise BrokerError("layout_changed", "Version or DPI changed since the reviewed screenshot")
        marker = args.account_marker
        if marker and "*" not in marker:
            raise BrokerError("invalid_account_marker", "Only a masked account marker may be stored")
        old_marker = bridge.config.get("profile", {}).get("account_marker")
        if old_marker and marker and marker != old_marker:
            raise BrokerError("account_changed", "Current account differs from the calibrated account")
        recovery_path = bridge.output / "recovery.json"
        if recovery_path.exists():
            recovery = json.loads(recovery_path.read_text())
            if recovery.get("status") == "pending" and evidence.stat().st_mtime < recovery["started_epoch"]:
                raise BrokerError("screenshot_expired", "Recovery requires a screenshot from the new session")
        bridge.config["profile"] = {"reviewed": True, "version": result["client"]["version"],
                                    "dpi": result["trade"]["dpi"],
                                    "width": result["trade"]["rect"]["width"],
                                    "height": result["trade"]["rect"]["height"],
                                    "anchors": ANCHORS, "export_menu_offset": [100, 20],
                                    "account_marker": marker,
                                    "screenshot_sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
                                    "calibrated_at": now()}
        bridge.config["auth_required"] = False
        result["calibration"] = "saved"
    bridge.save_config()
    return {"status": result["status"], "diagnostic": str(directory), "client": result.get("client"),
            "trade": result.get("trade"), "installed_clients": result.get("installed_clients"),
            "calibration": result.get("calibration")}


def watch(bridge, args, directory):
    state_path = directory / "watch.json"
    state = json.loads(state_path.read_text()) if args.resume else {
        "status": "running", "interval": args.interval, "count": args.count, "completed": 0, "events": []}
    state["status"] = "running"
    while state["completed"] < state["count"]:
        cycle_start = time.monotonic()
        cycle = new_run(bridge.output, "watch-snapshot")
        try:
            data = snapshot(bridge, cycle)
            event = {"cycle": state["completed"] + 1, "status": data["status"], "snapshot": str(cycle),
                     "positions": len(data["positions"]), "at": now()}
            state["completed"] += 1
        except BrokerError as exc:
            event = {"cycle": state["completed"] + 1, "status": exc.code, "at": now(), "evidence": str(cycle)}
            state["status"] = "waiting_for_login" if exc.code == "auth_required" else "stopped"
            state["events"].append(event)
            save_json(state_path, state)
            emit(event)
            capture_safely(bridge, cycle / "failure-screen.png")
            return {"status": state["status"], "watch": str(directory), "completed": state["completed"], "reason": exc.code}
        state["events"].append(event)
        save_json(state_path, state)
        emit(event)
        if state["completed"] < state["count"]:
            time.sleep(max(0, state["interval"] - (time.monotonic() - cycle_start)))
    state["status"] = "passed"
    save_json(state_path, state)
    return {"status": "passed", "watch": str(directory), "completed": state["completed"]}


def form_check(bridge, args, directory):
    if not bridge.config.get("profile", {}).get("account_marker"):
        raise BrokerError("account_verification_required", "Calibrate a reviewed masked account marker before a form check")
    source_dir = new_run(bridge.output, "form-source")
    data = snapshot(bridge, source_dir)
    position = next((p for p in data["positions"] if p["证券代码"] == args.symbol), None)
    if not position or "最新价" not in position:
        raise BrokerError("form_target_missing", "Requested holding and client reference price are unavailable")
    if args.quantity <= 0 or args.quantity % 100:
        raise BrokerError("invalid_quantity", "This form-only ETF check uses positive multiples of 100")
    if args.side == "sell" and money(position["可用数量"]) < args.quantity:
        raise BrokerError("insufficient_available", "Requested form quantity exceeds the reported available holding")
    price = str(money(position["最新价"]))
    result = {"status": "failed", "side": args.side, "symbol": args.symbol,
              "evidence_kind": "live_form_only", "submitted": False, "source_snapshot": str(source_dir)}
    needs_cleanup = False
    try:
        needs_cleanup = True  # A transport error may leave the write outcome unknown.
        response = bridge.run("form_fill", directory, side=args.side, symbol=args.symbol,
                              price=price, quantity=str(args.quantity))
        needs_cleanup = response.get("status") == "ok" or response.get("draft_written", True)
        fill = require_ok(response)
        result["readback"] = fill["readback"]
        result["filled_screenshot_sha256"] = bridge.capture(directory / "filled.png")
        result["status"] = "passed"
    except BrokerError as exc:
        result.update(status="failed", code=exc.code, message=str(exc))
        capture_safely(bridge, directory / "failure-screen.png")
    finally:
        if needs_cleanup:
            try:
                clear = bridge.run("form_clear", directory)
            except BrokerError as exc:
                clear = {"status": "error", "code": exc.code, "message": str(exc)}
            result["cleanup"] = clear
            if clear.get("status") != "ok":
                result.update(status="failed", code="draft_cleanup_failed")
            capture_safely(bridge, directory / "cleared.png")
        else:
            result["cleanup"] = {"status": "not_needed", "reason": "No draft was written; existing contents were preserved"}
        save_json(directory / "form-check.json", result)
    return {**result, "evidence": str(directory)}


def test_desktop(bridge, args, directory):
    scenario = args.scenario
    if scenario == "guards":
        cases = []
        for name, expected in (("resized", "layout_changed"), ("submit_region", "forbidden_target")):
            profile = copy.deepcopy(bridge.config.get("profile"))
            if not profile:
                raise BrokerError("calibration_required", "Calibrate the current layout before checking guards")
            if name == "resized":
                profile["width"] += 1
            else:
                profile["anchors"]["refresh"] = [0.45, 0.58]
            target = directory / (name + ".xlsx")
            response = bridge.run("snapshot", directory, profile=profile, output_file=bridge.guest_path(target))
            passed = response.get("code") == expected and not target.exists()
            cases.append({"case": name, "expected": expected, "actual": response.get("code"), "passed": passed})
        result = {"status": "passed" if all(c["passed"] for c in cases) else "failed", "scenario": scenario,
                  "evidence_kind": "live_driver_guard", "cases": cases}
    elif scenario == "resume":
        recovery_path = bridge.output / "recovery.json"
        if not recovery_path.exists():
            raise BrokerError("no_recovery_test", "Run an actual logout/restart test before checking recovery")
        recovery = json.loads(recovery_path.read_text())
        if recovery.get("status") != "pending" or not recovery.get("transition_confirmed"):
            raise BrokerError("no_recovery_test", "The preceding logout/restart transition has not been confirmed")
        marker = bridge.config.get("profile", {}).get("account_marker")
        if not marker or marker != recovery.get("account_marker") or not bridge.config.get("profile", {}).get("reviewed"):
            raise BrokerError("account_verification_required", "Review and recalibrate the same account after recovery")
        diagnostic = require_ok(bridge.run("doctor", directory, focus=True))
        if recovery["scenario"] == "restart" and diagnostic.get("client", {}).get("pid") == recovery["previous_pid"]:
            raise BrokerError("restart_not_confirmed", "The process was not replaced")
        data = snapshot(bridge, directory)
        result = {"status": "passed", "scenario": "resume", "evidence_kind": "live_client",
                  "positions": len(data["positions"]), "preceding_scenario": recovery["scenario"],
                  "preceding_evidence": recovery["evidence"]}
        recovery.update(status="passed", resumed_evidence=str(directory), resumed_at=now())
        save_json(recovery_path, recovery)
    elif scenario == "funds":
        response = require_ok(bridge.run("funds", directory))
        capture_safely(bridge, directory / "funds-panel.png")
        result = {"status": "partial", "scenario": scenario, "evidence_kind": "live_client",
                  "driver": response, "note": "Review the visible account components; unavailable accounting fields remain missing."}
    else:
        initial = require_ok(bridge.run("doctor", directory, focus=True))
        previous_pid = (initial.get("client") or {}).get("pid")
        marker = bridge.config.get("profile", {}).get("account_marker")
        if not marker:
            raise BrokerError("account_verification_required", "A reviewed masked account marker is required for recovery tests")
        started_epoch = time.time()
        confirmation = None
        if args.reviewed_screenshot and scenario in {"logout", "restart"}:
            evidence = args.reviewed_screenshot.resolve()
            evidence.relative_to(bridge.output)
            reviewed = json.loads(evidence.with_name("driver.json").read_text())
            dialogs = [w for w in reviewed["windows"] if w["visible"] and (
                (scenario == "logout" and w["class"] == "EMCustomTipDlg") or
                (scenario == "restart" and w["class"] == "#32770" and w["title"] == "提示"))]
            if len(dialogs) != 1 or time.time() - evidence.stat().st_mtime > 300:
                raise BrokerError("screenshot_expired", "Review the current exit confirmation screenshot")
            dialog = dialogs[0]
            confirmation = {"handle": dialog["handle"], "pid": dialog["pid"],
                            "width": dialog["rect"]["width"], "height": dialog["rect"]["height"],
                            "class": dialog["class"], "title": dialog["title"], "dpi": dialog["dpi"],
                            "screenshot_sha256": hashlib.sha256(evidence.read_bytes()).hexdigest()}
        response = bridge.run("logout_confirm" if confirmation and scenario == "logout" else scenario, directory,
                              confirmation_review=confirmation)
        capture_safely(bridge, directory / "screen.png")
        save_json(directory / "driver.json", response)
        if scenario == "logout":
            confirmed = response.get("status") == "auth_required"
            if confirmed:
                bridge.config["auth_required"] = True
                bridge.save_config()
                mark_stale(bridge.output, "auth_required")
                # A real capture attempt is required to verify the detection path.
                try:
                    snapshot(bridge, new_run(bridge.output, "logged-out-capture"))
                except BrokerError as exc:
                    confirmed = exc.code == "auth_required"
                else:
                    confirmed = False
            result = {"status": "waiting_for_login" if confirmed else "pending_manual_review",
                      "auth_detection": "passed" if confirmed else "not_verified", "scenario": scenario,
                      "evidence_kind": "live_client", "driver": response}
            transitioned = confirmed
        else:
            if response.get("restarted"):
                mark_stale(bridge.output, "client_restarted")
            restart_status = "waiting_for_login" if response.get("status") in {"auth_required", "trade_unavailable"} else "partial"
            if response.get("status") == "error":
                restart_status = "pending_manual_review" if response.get("code") == "restart_pending" else "failed"
            result = {"status": restart_status,
                      "scenario": scenario, "evidence_kind": "live_client", "driver": response,
                      "note": "A successful resumed snapshot is required before marking recovery passed."}
            transitioned = bool(response.get("restarted")) and (response.get("client") or {}).get("pid") != previous_pid
        if transitioned:
            save_json(bridge.output / "recovery.json", {"status": "pending", "scenario": scenario,
                      "started_epoch": started_epoch, "previous_pid": previous_pid, "account_marker": marker,
                      "transition_confirmed": True, "evidence": str(directory)})
            invalidate_calibration(bridge)
    result["evidence"] = str(directory)
    if "driver" in result:
        # Full window diagnostics remain in driver.json, rather than repeating
        # transient window details in the public command response.
        result["driver"] = {key: value for key, value in result["driver"].items()
                            if key in {"status", "code", "message", "operation", "restarted", "previous_pid", "client", "auth_reason", "machine_extraction"}}
    save_json(directory / "test.json", result)
    return result


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, default=ROOT / "data/eastmoney")
    p.add_argument("--vm")
    p.add_argument("--guest-root")
    p.add_argument("--client-pid", type=int, help="Select one current client process; never persisted across restart")
    commands = p.add_subparsers(dest="command", required=True)
    d = commands.add_parser("doctor")
    d.add_argument("--start-client", action="store_true")
    d.add_argument("--open-trade", action="store_true", help="Restore an identified folded trading window")
    d.add_argument("--client-exe")
    d.add_argument("--client-pid", type=int, default=argparse.SUPPRESS)
    d.add_argument("--calibrate", action="store_true")
    d.add_argument("--reviewed-screenshot", type=Path)
    d.add_argument("--account-marker")
    d.add_argument("--dismiss-ipo", action="store_true", help="Close only a screenshot-reviewed Today Subscription dialog")
    commands.add_parser("snapshot")
    w = commands.add_parser("watch")
    w.add_argument("--interval", type=float, default=30)
    w.add_argument("--count", type=int, default=10)
    w.add_argument("--resume", type=Path)
    f = commands.add_parser("form-check")
    f.add_argument("--side", choices=("buy", "sell"), required=True)
    f.add_argument("--symbol", default="515880")
    f.add_argument("--quantity", type=int, default=100)
    r = commands.add_parser("reconcile")
    r.add_argument("--snapshot", type=Path)
    r.add_argument("--funds-evidence", type=Path)
    t = commands.add_parser("test")
    t.add_argument("--scenario", choices=("simulation", "guards", "logout", "restart", "resume", "funds"), default="simulation")
    t.add_argument("--reviewed-screenshot", type=Path, help="Reviewed exact exit-trading confirmation screenshot")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    args.output = args.output.resolve()
    try:
        args.output.relative_to(ROOT / "data/eastmoney")
    except ValueError:
        raise BrokerError("outside_output", "Runtime artifacts must remain inside the ignored data/eastmoney directory") from None
    directory = new_run(args.output, args.command)
    if args.command == "test" and args.scenario == "simulation":
        result = simulation_report()
        save_json(directory / "test.json", result)
        emit({**result, "evidence": str(directory)})
        return 0
    if args.command == "reconcile":
        data = json.loads((args.snapshot or args.output / "latest.json").read_text())
        if data["status"] not in {"ok", "partial"}:
            raise BrokerError("stale_snapshot", "A stale or failed capture cannot pass reconciliation")
        supplemental = load_funds_evidence(args.funds_evidence, args.output) if args.funds_evidence else None
        result = reconcile(data, supplemental)
        save_json(directory / "reconciliation.json", result)
        emit({**result, "evidence": str(directory)})
        return 0 if result["status"] != "failed" else 1
    with desktop_lock(ROOT / "data/eastmoney"):
        bridge = Bridge(args.output, args.vm, args.guest_root, args.client_pid)
        if args.command == "doctor":
            result = doctor(bridge, args, directory)
        elif args.command == "snapshot":
            result = snapshot(bridge, directory)
            result = {"status": result["status"], "positions": len(result["positions"]), "reconciliation": result["reconciliation"]["status"], "evidence": str(directory)}
        elif args.command == "watch":
            if args.count < 1 or args.interval < 1:
                raise BrokerError("invalid_watch", "Count and interval must be positive")
            if args.resume:
                directory = args.resume.resolve()
                directory.relative_to(args.output / "runs")
            result = watch(bridge, args, directory)
        elif args.command == "form-check":
            result = form_check(bridge, args, directory)
        else:
            result = test_desktop(bridge, args, directory)
        emit(result)
        return 0 if result.get("status") in {"ok", "ready", "passed", "partial"} else 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokerError as exc:
        emit({"status": exc.code, "message": str(exc)})
        sys.exit(2)
    except KeyboardInterrupt:
        emit({"status": "interrupted", "message": "Stopped; inspect any active draft before further desktop work."})
        sys.exit(130)
    except (OSError, ValueError, KeyError) as exc:
        emit({"status": "invalid_local_input", "message": type(exc).__name__ + ": inspect local configuration and evidence files"})
        sys.exit(2)
