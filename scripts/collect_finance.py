#!/usr/bin/env python3
"""Collect stock/index/ETF evidence; run from any directory with --help."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

import pandas as pd

try:
    from .data_sources import Collector, utc_now
    from .finance_metrics import (dated, history, metric, numeric, require_fresh,
                                  statement, trend_metrics, valuation_metrics, etf_premium, reported_fund_metrics)
except ImportError:
    from data_sources import Collector, utc_now
    from finance_metrics import (dated, history, metric, numeric, require_fresh,
                                 statement, trend_metrics, valuation_metrics, etf_premium, reported_fund_metrics)

# Explicit identity mapping; never guess an index exchange from stock-code rules.
INDEXES = {
    "000001": ("上证指数", "sh", "broad", True),
    "000300": ("沪深300", "sh", "broad", True),
    "000016": ("上证50", "sh", "large", True),
    "000905": ("中证500", "sh", "mid", True),
    "000852": ("中证1000", "sh", "growth", True),
    "000688": ("科创50", "sh", "growth", True),
    "000922": ("中证红利", "sh", "dividend", True),
    "399001": ("深证成指", "sz", "broad", False),
    "399006": ("创业板指", "sz", "growth", False),
    "399005": ("中小100", "sz", "mid", False),
}
NORTH_REASON = "2024-08-19起公开北向日买卖额披露调整；单日快照/成交总额不能推算近10/20日净流入"


def ak(endpoint, **params):
    return ("akshare", endpoint, params)


def resolve_stock(code):
    if not re.fullmatch(r"\d{6}", code):
        raise ValueError("股票代码必须是六位数字")
    if code.startswith(("60", "68")):
        return "sh"
    if code.startswith(("00", "30")):
        return "sz"
    if code.startswith(("4", "8", "92")):
        return "bj"
    raise ValueError("未识别的A股代码；ETF和指数须选择对应子命令")


def resolve_index(target):
    code = next((k for k, v in INDEXES.items() if v[0] == target), target)
    if code not in INDEXES:
        raise ValueError("未知指数：先核对编制机构、代码、交易所，再补入 INDEXES 映射")
    return code, INDEXES[code]


def source(c, key):
    return c.datasets.get(key, {}).get("source")


def stock_collect(c, code, start, end):
    market = resolve_stock(code)
    result = {}
    for field in ("revenue", "net_profit_parent", "net_profit", "assets", "liabilities",
                  "equity_parent", "cash", "accounts_receivable", "inventory",
                  "current_assets", "current_liabilities", "short_loan", "long_loan",
                  "bond_payable", "lease_liability", "cfo", "capex", "fcf"):
        result[f"latest_{field}"] = metric(reason="财报项目尚未取得")
    tables = {}
    for kind, endpoint, label in (
        ("profit", "stock_profit_sheet_by_report_em", "利润表"),
        ("balance", "stock_balance_sheet_by_report_em", "资产负债表"),
        ("cashflow", "stock_cash_flow_sheet_by_report_em", "现金流量表"),
    ):
        candidates = [ak(endpoint, symbol=market.upper()+code)]
        if market != "bj":
            candidates.append(ak("stock_financial_report_sina", stock=market+code, symbol=label))
        tables[kind] = c.fetch(kind, candidates,
            normalize=lambda f, kind=kind: statement(f, kind, end, start),
            units="人民币元；利润/现金流为年初至报告期累计，资产负债表为期末值")
        frame = tables[kind]
        if not frame.empty:
            asof = frame.date.iloc[-1]
            for field in frame.columns:
                if field not in ("date", "NOTICE_DATE", "UPDATE_DATE", "更新日期"):
                    value = frame[field].iloc[-1]
                    result[f"latest_{field}"] = metric(float(value), source=source(c, kind), asof=asof, unit="元")
            annual = frame[frame.date.dt.month.eq(12) & frame.date.dt.day.eq(31)].tail(5)
            annual.to_csv(c.output / f"{kind}_annual.csv", index=False)
            c.datasets[kind]["annual_file"] = f"{kind}_annual.csv"
            c.datasets[kind]["annual_periods"] = annual.date.dt.strftime("%Y-%m-%d").tolist()
            years = annual.date.dt.year.tolist()
            c.datasets[kind]["coverage_5y"] = len(years) == 5 and years == list(range(years[-1]-4, years[-1]+1))
            if (pd.Timestamp(end) - asof).days > 200:
                c.datasets[kind]["warning"] = "财报报告期落后超过200天，须核对最新披露"
    # Independent same-period check of balance-sheet totals, not ambiguous ratio scales.
    if market != "bj" and source(c, "balance") == "akshare.stock_balance_sheet_by_report_em":
        check = c.fetch("balance_crosscheck", [ak("stock_financial_report_sina", stock=market+code, symbol="资产负债表")],
                        normalize=lambda f: statement(f, "balance", end, start), units="元")
        matched = tables["balance"].merge(check, on="date", suffixes=("_em", "_sina")) if not check.empty else pd.DataFrame()
        conflicts = []
        checked_periods = set()
        for field in ("assets", "liabilities"):
            if not matched.empty:
                valid = matched.dropna(subset=[field+"_em", field+"_sina"])
                checked_periods.update(valid.date.dt.strftime("%Y-%m-%d"))
                different = (valid[field+"_em"] - valid[field+"_sina"]).abs() > valid[field+"_em"].abs().clip(lower=1) * .01
                conflicts.extend(valid.loc[different, "date"].dt.strftime("%Y-%m-%d").tolist())
        result["balance_crosscheck"] = metric(
            "发现差异，回查公告" if conflicts else ("同报告期已核对" if checked_periods else None),
            source="东方财富/新浪合并财报", checked_periods=sorted(checked_periods),
            periods_with_conflict=sorted(set(conflicts)))
    candidates = []
    if market != "bj":
        candidates.append(("baostock", "query_history_k_data_plus", {
            "code": market+"."+code, "fields": "date,open,high,low,close,volume,amount,turn,peTTM,pbMRQ,psTTM",
            "start_date": start, "end_date": end, "frequency": "d", "adjustflag": "3"}))
    candidates += [ak("stock_zh_a_hist", symbol=code, start_date=start.replace("-", ""),
                      end_date=end.replace("-", ""), adjust="", timeout=15)]
    if market != "bj":
        candidates.append(ak("stock_zh_a_daily", symbol=market+code, start_date=start.replace("-", ""),
                             end_date=end.replace("-", ""), adjust=""))
    prices = c.fetch("prices", candidates, normalize=lambda f: history(f, end, start), units="不复权；价格元，amount元；volume保留原源单位")
    result["last_close"] = (metric(float(prices.close.iloc[-1]), source=source(c, "prices"), asof=prices.date.iloc[-1], unit="元")
                            if not prices.empty else metric(reason="历史行情全部失败"))
    for name, column, indicator in (("pe_ttm", "peTTM", "市盈率(TTM)"),
                                    ("pb", "pbMRQ", "市净率"),
                                    ("ps_ttm", "psTTM", None)):
        series = prices
        if series.empty or column not in series or pd.isna(series[column].iloc[-1]):
            if indicator is None:
                series = pd.DataFrame()
                field, origin = column, None
            else:
                series = c.fetch(name, [ak("stock_zh_valuation_baidu", symbol=code, indicator=indicator, period="近一年")],
                                 normalize=lambda f: require_fresh(dated(f, "date", end), end), units="倍")
                field, origin = "value", source(c, name)
        else:
            field, origin = column, source(c, "prices")
        result[name], result[name + "_pct_5y"] = valuation_metrics(series, field, origin, end)
    supplements = [
        ("financial_indicators", "stock_financial_analysis_indicator_em", {"symbol": code+"."+market.upper(), "indicator": "按报告期"}),
        ("dividends", "stock_history_dividend_detail", {"symbol": code, "indicator": "分红"}),
        ("holder_count", "stock_zh_a_gdhs_detail_em", {"symbol": code}),
        ("holders", "stock_circulate_stock_holder", {"symbol": code}),
        ("business", "stock_zygc_em", {"symbol": market.upper()+code}),
        ("announcements", "stock_zh_a_disclosure_report_cninfo", {"symbol": code, "market": "沪深京",
            "start_date": start.replace("-", ""), "end_date": end.replace("-", "")}),
    ]
    for key, endpoint, params in supplements:
        supplement = c.fetch(key, [ak(endpoint, **params)], normalize=(
            lambda f: dated(f, "REPORT_DATE", end, start)) if key == "financial_indicators" else None)
        if key == "financial_indicators":
            for name, column, unit in (("reported_roe", "ROEJQ", "%"),
                                       ("reported_deducted_roe", "ROEKCJQ", "%"),
                                       ("reported_deducted_profit", "KCFJCXSYJLR", "元"),
                                       ("reported_debt_asset_ratio", "ZCFZL", "%")):
                value = numeric(supplement[column]).iloc[-1] if not supplement.empty and column in supplement else None
                result[name] = metric(float(value) if value is not None else None,
                    source=source(c, key), asof=supplement.date.iloc[-1] if not supplement.empty else None,
                    unit=unit, verification="平台披露字段，需与年报原文核对")
    for name, reason in {
        "annual_report_verification": "需从原始年报核对扣非加权ROE及关键项目，并记录原文页码",
        "management_pay_holdings": "需核对年报管理层薪酬及持股原文和页码",
        "peer_valuation": "需先核对行业与至少3家可比公司，再分别采集同口径数据",
        "industry_pe": "未确认同业务结构的行业样本和统一估值日期，不能使用宽泛行业PE均值",
        "ev_ebitda": "缺少经同日核验的总市值、完整有息债务与TTM EBITDA口径，不能用净利润代替",
        "interest_bearing_debt": "原始资产负债表已保存；需逐项核对借款、债券和租赁负债口径",
        "north_10d": NORTH_REASON,
    }.items():
        result[name] = metric(reason=reason)
    return {"market": market, "metrics": result,
            "limitations": ["最新修订财报，不是历史时点可得数据，不能直接用于无前视偏差回测",
                            "附加表保存原始字段，须核对报告期、单位与空值后使用",
                            "FCF=CFO-购建长期资产支付现金，非FCFF/FCFE；金融企业须单独建模",
                            "股票不复权价格供报价核对，不能直接计算跨除权日收益率"]}


def index_collect(c, target, start, end, use_tushare=False):
    code, (name, market, style, cs_supported) = resolve_index(target)
    candidates = [("baostock", "query_history_k_data_plus", {
        "code": market+"."+code, "fields": "date,open,high,low,close,volume,amount",
        "start_date": start, "end_date": end, "frequency": "d", "adjustflag": "3"}),
        ak("stock_zh_index_daily_em", symbol=market+code, start_date=start.replace("-", ""), end_date=end.replace("-", "")),
        ak("stock_zh_index_daily", symbol=market+code)]
    prices = c.fetch("prices", candidates, normalize=lambda f: history(f, end, start), units="价格指数点位；amount元，volume保留原源单位")
    result = trend_metrics(prices, source(c, "prices"))
    def csi_pe(key, symbol):
        return c.fetch(key, [ak("stock_zh_index_hist_csindex", symbol=symbol,
                                start_date=start.replace("-", ""), end_date=end.replace("-", ""))],
                       normalize=lambda f: require_fresh(dated(f.rename(columns={"滚动市盈率": "pe"}), "日期", end, start), end), units="滚动PE：倍")
    pe = csi_pe("valuation", code) if cs_supported else pd.DataFrame()
    origin = source(c, "valuation")
    result["current_pe"], result["pe_pct_5y"] = valuation_metrics(pe, "pe", origin, end)
    if result["current_pe"]["value"] is None and cs_supported:
        recent = c.fetch("valuation_recent", [ak("stock_zh_index_value_csindex", symbol=code)],
                         normalize=lambda f: require_fresh(dated(f.rename(columns={"市盈率1": "pe"}), "日期", end), end), units="市盈率1：倍，不与滚动PE拼接")
        result["current_pe"] = valuation_metrics(recent, "pe", source(c, "valuation_recent"), end)[0]
    result["current_pb"] = metric(reason="中证免费接口不含PB；可选 --tushare（需权限及指数覆盖）")
    result["pb_pct_5y"] = metric(reason="无同口径PB历史")
    ts_allowed = {"000001", "000016", "000300", "000905", "399001", "399005", "399006"}
    ts = pd.DataFrame()
    if use_tushare and code in ts_allowed:
        ts = c.fetch("tushare_valuation", [("tushare", "index_dailybasic", {
            "ts_code": code+"."+market.upper(), "start_date": start.replace("-", ""), "end_date": end.replace("-", "")})],
            normalize=lambda f: require_fresh(dated(f, "trade_date", end, start), end), units="PE/PB倍；社区计算口径")
        result["current_pb"], result["pb_pct_5y"] = valuation_metrics(ts, "pb", source(c, "tushare_valuation"), end)
        if result["current_pe"]["value"] is None:
            result["current_pe"], result["pe_pct_5y"] = valuation_metrics(ts, "pe_ttm", source(c, "tushare_valuation"), end)
    elif use_tushare:
        result["current_pb"] = metric(reason="该指数不在已核对的Tushare index_dailybasic覆盖范围")
    result["relative_pe_ratio"] = metric(reason="仅成长指数要求；需目标与沪深300同源同口径历史")
    result["relative_pe_pct_5y"] = metric(reason="无共同5年同口径历史")
    if style == "growth":
        relative_source = origin
        relative_target = pe
        benchmark = pd.DataFrame()
        if not pe.empty and "pe" in pe:
            benchmark = csi_pe("benchmark_valuation", "000300")
        elif not ts.empty and "pe_ttm" in ts:
            relative_target = ts[["date", "pe_ttm"]].rename(columns={"pe_ttm": "pe"})
            relative_source = source(c, "tushare_valuation")
            benchmark = c.fetch("benchmark_valuation", [("tushare", "index_dailybasic", {
                "ts_code": "000300.SH", "start_date": start.replace("-", ""), "end_date": end.replace("-", "")})],
                normalize=lambda f: require_fresh(dated(f[["trade_date", "pe_ttm"]].rename(columns={"pe_ttm": "pe"}), "trade_date", end, start), end), units="PE TTM：倍；Tushare社区口径")
        if not benchmark.empty and "pe" in benchmark:
            rel = relative_target[["date", "pe"]].merge(benchmark[["date", "pe"]], on="date", suffixes=("_index", "_benchmark"))
            rel["ratio"] = numeric(rel.pe_index).where(numeric(rel.pe_index)>0) / numeric(rel.pe_benchmark).where(numeric(rel.pe_benchmark)>0)
            result["relative_pe_ratio"], result["relative_pe_pct_5y"] = valuation_metrics(rel, "ratio", relative_source+" / 沪深300", end)
            rel.to_csv(c.output / "relative_pe.csv", index=False)
    def bond_normalize(frame):
        out = dated(frame, "日期", end)
        col = "中国国债收益率10年"
        if col not in out:
            raise ValueError(f"缺少 {col}")
        out["yield_10y"] = numeric(out[col])
        return require_fresh(out.dropna(subset=["yield_10y"]), end)
    bonds = c.fetch("bond", [ak("bond_zh_us_rate", start_date=start.replace("-", ""))], normalize=bond_normalize, units="%")
    result["bond_10y"] = (metric(float(bonds.yield_10y.iloc[-1]), source=source(c, "bond"), asof=bonds.date.iloc[-1], unit="%")
                          if not bonds.empty else metric(reason="10Y国债收益率缺失或过期"))
    result["earnings_yield"] = metric(reason="缺少正PE")
    result["equity_bond_ratio"] = metric(reason="缺少同日正PE和10Y收益率")
    pv, bv = result["current_pe"], result["bond_10y"]
    if pv["value"] is not None and pv["value"] > 0:
        result["earnings_yield"] = metric(100/pv["value"], source=pv["source"], asof=pv["asof"], unit="%")
        if bv["value"] is not None and bv["value"] > 0 and pv["asof"] == bv["asof"]:
            result["equity_bond_ratio"] = metric(100/pv["value"]/bv["value"], source=f"{pv['source']} + {bv['source']}", asof=pv["asof"], unit="倍")
    result.update(north_10d=metric(reason=NORTH_REASON), north_20d=metric(reason=NORTH_REASON))
    return {"index_name": name, "index_code": code, "index_style": style, "metrics": result,
            "limitations": ["中证历史接口可能仅有收盘价或估值空值；不用于补造OHLC",
                             "Tushare与中证估值口径不同，各序列独立计算，不跨源拼接",
                             "日线14自然日的新鲜度门槛仅为粗筛；使用前核对最新交易日"]}


def etf_collect(c, code, start, end):
    if not re.fullmatch(r"(?:5\d{5}|1[568]\d{4})", code):
        raise ValueError("请输入沪深场内基金代码；需根据基金基本信息再次确认ETF身份")
    market = "sh" if code.startswith("5") else "sz"
    prices = c.fetch("prices", [ak("fund_etf_hist_em", symbol=code, start_date=start.replace("-", ""), end_date=end.replace("-", ""), adjust=""),
                                ak("fund_etf_hist_sina", symbol=market+code)],
                     normalize=lambda f: history(f, end, start), units="不复权价格元；amount元")
    def nav_normalize(frame):
        out = dated(frame.rename(columns={"单位净值": "nav"}), "净值日期", end, start)
        out["nav"] = numeric(out["nav"])
        return require_fresh(out, end)
    nav = c.fetch("nav", [ak("fund_etf_fund_info_em", fund=code, start_date=start.replace("-", ""), end_date=end.replace("-", ""))],
                  normalize=nav_normalize, units="单位净值元；累计净值不等于分红再投资复权净值")
    c.fetch("profile", [ak("fund_info_ths", symbol=code), ak("fund_individual_basic_info_xq", symbol=code, timeout=15)])
    overview = c.fetch("overview", [ak("fund_overview_em", symbol=code)])
    c.fetch("fees", [ak("fund_fee_em", symbol=code, indicator="运作费用")])
    def scale_normalize(frame):
        selected = frame[frame["基金代码"].astype(str).str.zfill(6).eq(code)].copy()
        if market == "sh":
            selected = require_fresh(dated(selected, "统计日期", end), end)
        return selected
    scale_date = prices.date.iloc[-1].strftime("%Y%m%d") if not prices.empty else end.replace("-", "")
    scale = c.fetch("shares", [ak("fund_etf_scale_sse", date=scale_date) if market == "sh" else ak("fund_etf_scale_szse")],
                    normalize=scale_normalize, units="基金份额：份（不是AUM）；深交所接口未附规模统计日期")
    for year in (pd.Timestamp(end).year, pd.Timestamp(end).year-1):
        c.fetch(f"holdings_{year}", [ak("fund_portfolio_hold_em", symbol=code, date=str(year))])
    result = {"close_premium": etf_premium(prices, nav, "prices.csv + nav.csv"),
              "avg_amount_3m": metric(reason="缺少完整近3月成交额"),
              "aum": metric(reason="缺少同日可核验份额及净值；不以成立规模或份额数代替AUM")}
    result.update(reported_fund_metrics(overview, source(c, "overview"), end))
    if not scale.empty and "date" in scale and not nav.empty:
        shares_nav = scale.merge(nav[["date", "nav"]], on="date")
        if not shares_nav.empty:
            row = shares_nav.iloc[-1]
            shares = pd.to_numeric(row["基金份额"], errors="coerce")
            if pd.notna(shares) and shares > 0 and pd.notna(row.nav) and row.nav > 0:
                result["aum"] = metric(float(shares * row.nav), source="shares.csv × nav.csv",
                    asof=row.date, unit="元", method="交易所同日份额×单位净值")
    if not prices.empty and "amount" in prices:
        boundary = prices.date.iloc[-1] - pd.DateOffset(months=3)
        sample = prices[prices.date > boundary]
        if prices.date.iloc[0] <= boundary and len(sample) >= 40 and sample.amount.notna().all() and (sample.amount >= 0).all():
            result["avg_amount_3m"] = metric(float(sample.amount.mean()), source=source(c, "prices"), asof=prices.date.iloc[-1], unit="元", samples=len(sample))
    for name, reason in {
        "tracking_error": "需官方披露TE，或分红再投资净值与同币种同口径基准日收益；不以ETF成交价代替净值",
        "tracking_difference": "需完整一年同口径复权净值及基准序列",
        "aum_6m_change": "需带日期的规模历史，份额数量不可当作AUM",
        "benchmark_verified": "需基金合同确认跟踪指数代码、币种、收益类型；不能仅凭名称",
        "replication": "需核对招募说明书的复制方式",
        "realtime_premium": "无同一时间戳的价格和IOPV，只有同日收盘溢价",
        "creation_redemption": "净值页申购状态不等于场内一级市场申赎状态，需基金公司PCF/公告",
    }.items():
        result[name] = metric(reason=reason)
    return {"metrics": result, "limitations": ["profile/fees/holdings为原始证据，须核对日期、费用生效日和跟踪基准后比较",
                                                    "缺少规模/费用/基准核验时，不得输出完整ETF优劣排名"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=["stock", "index", "etf"])
    parser.add_argument("target")
    parser.add_argument("--output", type=Path, help="新建的输出目录；已有非空目录将拒绝覆盖")
    parser.add_argument("--timeout", type=float, default=35, help="每个接口调用总时限（秒）")
    parser.add_argument("--retries", type=int, default=1, choices=[0, 1, 2])
    parser.add_argument("--tushare", action="store_true", help="启用已有TUSHARE_TOKEN权限的指数估值补充")
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("timeout必须为正数")
    if args.kind == "stock":
        resolve_stock(args.target)
    elif args.kind == "index":
        resolve_index(args.target)
    elif not re.fullmatch(r"(?:5\d{5}|1[568]\d{4})", args.target):
        parser.error("无效ETF代码")
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    # Conservative EOD policy: always exclude today's potentially unfinished bar.
    end = (now.date() - timedelta(days=1)).isoformat()
    start = (pd.Timestamp(end) - pd.DateOffset(years=6)).date().isoformat()
    folder = args.output or Path("data") / f"{args.kind}_{args.target}_{now:%Y%m%dT%H%M%S%f}"
    if folder.exists() and any(folder.iterdir()):
        parser.error("输出目录非空，请选择新目录以保留既有证据")
    c = Collector(folder, timeout=args.timeout, retries=args.retries)
    if args.kind == "stock":
        result = stock_collect(c, args.target, start, end)
    elif args.kind == "index":
        result = index_collect(c, args.target, start, end, args.tushare)
    else:
        result = etf_collect(c, args.target, start, end)
    payload = {"schema_version": 1, "kind": args.kind, "target": args.target,
               "queried_at": utc_now(), "requested_start": start, "requested_end": end,
               "date_policy": "Asia/Shanghai；只取昨日及之前日线，不输出实时行情",
               "versions": c.versions, "datasets": c.datasets, **result}
    payload["status"] = "partial" if (any(d["status"] != "ok" for d in c.datasets.values()) or
        any(m["status"] != "ok" for m in result["metrics"].values())) else "ok"
    path = folder / "report.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(f"{payload['status']}: {path.resolve()}")
    for key, data in c.datasets.items():
        print(f"  {key}: {data['status']} ({data.get('rows', 0)} rows)")
    # A partial report is an expected usable result, not a process error.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
