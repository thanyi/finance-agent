"""Offline XLSX validation and explicitly simulated order scenarios. No broker I/O."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile
from zoneinfo import ZoneInfo


class BrokerError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def now() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")


def money(value: object) -> Decimal:
    try:
        result = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        raise BrokerError("invalid_number", "Non-numeric or missing numeric field") from None
    if not result.is_finite():
        raise BrokerError("invalid_number", "Non-finite numeric field")
    return result


def xlsx_rows(path: Path) -> list[dict[str, str]]:
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    try:
        with ZipFile(path) as archive:
            if sum(i.file_size for i in archive.infolist()) > 20_000_000:
                raise BrokerError("invalid_export", "Export exceeds 20 MB uncompressed")
            strings = []
            if "xl/sharedStrings.xml" in archive.namelist():
                root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
                strings = ["".join(t.text or "" for t in si.findall(".//m:t", ns))
                           for si in root.findall("m:si", ns)]
            sheets = sorted(n for n in archive.namelist()
                            if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n))
            if len(sheets) != 1:
                raise BrokerError("invalid_export", "Expected exactly one exported worksheet")
            root = ET.fromstring(archive.read(sheets[0]))
            rows = []
            for row in root.findall("m:sheetData/m:row", ns):
                values = {}
                for cell in row.findall("m:c", ns):
                    if cell.find("m:f", ns) is not None:
                        raise BrokerError("invalid_export", "Formula cells are not accepted as broker evidence")
                    column = re.sub(r"\d", "", cell.attrib["r"])
                    value = cell.find("m:v", ns)
                    text = value.text or "" if value is not None else ""
                    if cell.get("t") == "s":
                        text = strings[int(text)]
                    elif cell.get("t") == "inlineStr":
                        text = "".join(t.text or "" for t in cell.findall(".//m:t", ns))
                    values[column] = text.strip()
                rows.append(values)
            return rows
    except BrokerError:
        raise
    except (BadZipFile, ET.ParseError, KeyError, IndexError, ValueError, OSError) as exc:
        raise BrokerError("invalid_export", f"Cannot parse broker export: {type(exc).__name__}") from None


def reconcile(snapshot: dict, supplemental: dict | None = None) -> dict:
    funds = snapshot.get("funds", {})
    positions = snapshot.get("positions", [])
    checks = []
    if "证券市值" in funds and all("最新市值" in p for p in positions):
        actual = sum((money(p["最新市值"]) for p in positions), Decimal("0"))
        checks.append({"name": "position_market_value", "status": "passed" if actual == money(funds["证券市值"]) else "failed",
                       "calculated": str(actual), "reported": funds["证券市值"]})
    else:
        checks.append({"name": "position_market_value", "status": "missing"})
    # Available cash is NOT cash balance. A separate accounting identity must
    # be backed by same-snapshot evidence and an explicit account definition.
    missing = ["资金余额及现金构成", "冻结资金及对应委托", "账户资产口径"]
    if supplemental:
        required = {"source_sha256", "snapshot_sha256", "currency", "cash_balance",
                    "other_assets", "liabilities", "frozen_cash", "order_frozen_cash",
                    "cash_includes_frozen", "accounting_identity_verified"}
        if not required <= supplemental.keys():
            raise BrokerError("incomplete_evidence", "Supplemental funds evidence is missing required fields")
        if (supplemental["snapshot_sha256"] != snapshot["source"]["sha256"]
                or supplemental["currency"] != "CNY"
                or supplemental["cash_includes_frozen"] is not True
                or supplemental["accounting_identity_verified"] is not True):
            raise BrokerError("incompatible_evidence", "Funds evidence is not for this snapshot and accounting basis")
        total = money(supplemental["cash_balance"]) + money(funds["证券市值"]) + money(supplemental["other_assets"]) - money(supplemental["liabilities"])
        checks.append({"name": "total_assets", "status": "passed" if total == money(funds["总资产"]) else "failed",
                       "calculated": str(total), "reported": funds["总资产"]})
        checks.append({"name": "frozen_cash", "status": "passed" if money(supplemental["frozen_cash"]) == money(supplemental["order_frozen_cash"]) else "failed"})
        missing = []
    residual = None
    if {"总资产", "证券市值", "可用资金"} <= funds.keys():
        residual = str(money(funds["总资产"]) - money(funds["证券市值"]) - money(funds["可用资金"]))
    status = "failed" if any(c["status"] == "failed" for c in checks) else "partial" if missing or any(c["status"] == "missing" for c in checks) else "passed"
    return {"status": status, "checks": checks, "missing": missing,
            "assets_minus_market_minus_available_cash": residual,
            "note": "此差额不构成资金余额对账，不能用可用资金代替资金余额。"}


def parse_export(path: Path, *, run_id: str, started_at: float | None = None) -> dict:
    if started_at is not None and path.stat().st_mtime < started_at - 2:
        raise BrokerError("stale_export", "Export file predates the current capture")
    rows = xlsx_rows(path)
    required = {"证券代码", "证券名称", "持仓数量", "可用数量"}
    header_at = next((i for i, r in enumerate(rows) if required <= set(r.values())), None)
    if header_at is None:
        raise BrokerError("missing_columns", "Recognizable holdings header is required, including for an empty account")
    headers = rows[header_at]
    if len(headers.values()) != len(set(headers.values())):
        raise BrokerError("duplicate_columns", "Ambiguous duplicate holdings columns")
    funds_at = next((i for i, r in enumerate(rows[:header_at]) if {"总资产", "可用资金", "证券市值"} <= set(r.values())), None)
    funds = {}
    if funds_at is not None and funds_at + 1 < header_at:
        funds = {label: rows[funds_at + 1].get(col, "") for col, label in rows[funds_at].items()
                 if not re.search(r"账号|帐号|姓名|股东", label)}
        for key, value in funds.items():
            if key != "导出时间":
                money(value)
    positions = []
    for row in rows[header_at + 1:]:
        if not any(row.values()):
            continue
        item = {label: row.get(col, "") for col, label in headers.items()
                if label not in {"股东账号", "资金账号", "资金帐号", "账户姓名"}}
        code = item["证券代码"]
        if not re.fullmatch(r"\d{1,6}", code):
            raise BrokerError("invalid_code", "Invalid security code in holdings row")
        item["证券代码"] = code.zfill(6)
        held, available = money(item["持仓数量"]), money(item["可用数量"])
        if held != held.to_integral_value() or available != available.to_integral_value() or not 0 <= available <= held:
            raise BrokerError("invalid_quantity", "Position quantities must be nonnegative integers with available <= held")
        for key in ("成本价", "最新价", "最新市值"):
            if key in item:
                money(item[key])
        if item.get("币种", "人民币") not in {"人民币", "CNY"}:
            raise BrokerError("unsupported_currency", "Mixed-currency exports require a separate accounting definition")
        positions.append(item)
    codes = [p["证券代码"] for p in positions]
    if len(codes) != len(set(codes)):
        raise BrokerError("duplicate_position", "Duplicate security code in holdings")
    result = {"status": "ok" if funds else "partial", "run_id": run_id, "captured_at": now(),
              "source": {"kind": "eastmoney_client_xlsx", "file": path.name,
                         "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                         "client_export_time": funds.get("导出时间"), "quote_time": None},
              "funds": funds, "positions": positions,
              "empty_positions": not positions,
              "limitations": ["采集时间不代表行情更新时间", "认证信息未保存"]}
    result["reconciliation"] = reconcile(result)
    if result["reconciliation"]["status"] == "failed":
        raise BrokerError("reconciliation_failed", "Position market values disagree with the exported total")
    return result


class SimulatedOrders:
    """An isolated state machine; it has no desktop driver or network client."""
    def __init__(self):
        self.orders: dict[str, dict] = {}

    def submit(self, key: str, quantity: int) -> dict:
        if quantity <= 0:
            raise BrokerError("invalid_quantity", "Quantity must be positive")
        if key not in self.orders:
            self.orders[key] = {"status": "submitted", "quantity": quantity, "filled": 0}
        elif self.orders[key]["quantity"] != quantity:
            raise BrokerError("duplicate_conflict", "Idempotency key reused with different quantity")
        return self.orders[key].copy()

    def event(self, key: str, kind: str, filled: int = 0) -> dict:
        order = self.orders[key]
        if order["status"] in {"cancelled", "filled", "rejected"}:
            raise BrokerError("terminal_order", "Order is already terminal")
        if kind == "fill":
            if not order["filled"] <= filled <= order["quantity"]:
                raise BrokerError("invalid_fill", "Cumulative fills cannot decrease or exceed quantity")
            order["filled"] = filled
            order["status"] = "filled" if filled == order["quantity"] else "partial"
        elif kind == "reject" and not order["filled"]:
            order["status"] = "rejected"
        elif kind == "timeout":
            order["status"] = "unknown"
        elif kind == "cancel":
            if order["status"] == "unknown":
                raise BrokerError("unknown_order", "Reconcile unknown submission before cancellation")
            order["status"] = "cancel_pending"
        elif kind == "cancel_ack" and order["status"] == "cancel_pending":
            order["status"] = "cancelled"
        else:
            raise BrokerError("invalid_event", "Unexpected simulated order event")
        return order.copy()


def simulation_report() -> dict:
    broker = SimulatedOrders()
    cases = []
    broker.submit("duplicate", 100)
    broker.submit("duplicate", 100)
    assert len(broker.orders) == 1
    cases.append("duplicate_submission_not_repeated")
    broker.submit("reject", 100)
    assert broker.event("reject", "reject")["status"] == "rejected"
    cases.append("rejected_order")
    broker.submit("partial", 200)
    broker.event("partial", "fill", 100)
    broker.event("partial", "cancel")
    assert broker.event("partial", "cancel_ack")["filled"] == 100
    cases.append("partial_fill_and_cancel")
    broker.submit("race", 100)
    broker.event("race", "cancel")
    assert broker.event("race", "fill", 100)["status"] == "filled"
    cases.append("filled_during_cancellation")
    broker.submit("unknown", 100)
    broker.event("unknown", "timeout")
    assert broker.submit("unknown", 100)["status"] == "unknown"
    try:
        broker.event("unknown", "cancel")
    except BrokerError as exc:
        assert exc.code == "unknown_order"
    else:
        raise AssertionError("Unknown order was cancelled without reconciliation")
    cases.append("unknown_submission_not_retried")
    return {"status": "passed", "evidence_kind": "simulation", "cases": cases,
            "live_submission": "not_tested", "live_cancellation": "not_tested"}
