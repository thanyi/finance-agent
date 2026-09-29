"""Validated data transformations. No network access or silent value filling."""
from __future__ import annotations

import numpy as np
import pandas as pd
import re


class DataQualityError(ValueError):
    pass


def numeric(series):
    return pd.to_numeric(series.astype(str).str.replace(",", "", regex=False), errors="coerce").replace([np.inf, -np.inf], np.nan)


def dated(frame, date_column, end, start=None):
    if date_column not in frame:
        raise DataQualityError(f"缺少日期列 {date_column}")
    out = frame.copy().rename(columns={date_column: "date"})
    out["date"] = pd.to_datetime(out["date"].astype(str).str.slice(0, 10), errors="coerce", format="mixed")
    out = out.dropna(subset=["date"])
    out = out[out.date <= pd.Timestamp(end)]
    if start is not None:
        out = out[out.date >= pd.Timestamp(start)]
    # Conflicting duplicates must not be silently resolved by row ordering.
    out = out.drop_duplicates()
    if out.date.duplicated().any():
        raise DataQualityError("同日重复记录且数值不一致；需核对来源版本")
    return out.sort_values("date").reset_index(drop=True)


def require_fresh(frame, end, max_age=14):
    if frame.empty:
        raise DataQualityError("有效数据为空")
    if (pd.Timestamp(end) - frame.date.max()).days > max_age:
        raise DataQualityError(f"数据过期：最新 {frame.date.max().date()}，请求截止 {end}")
    return frame


def history(frame, end, start=None, csindex=False):
    aliases = {"日期": "date", "开盘": "open", "收盘": "close", "最高": "high",
               "最低": "low", "成交额": "amount", "成交金额": "amount",
               "成交量": "volume", "换手率": "turn"}
    out = dated(frame.rename(columns=aliases), "date", end, start)
    for col in ("open", "high", "low", "close"):
        if col not in out:
            raise DataQualityError(f"缺少行情列 {col}")
        out[col] = numeric(out[col])
    # Reject a broken series rather than bridging dropped trading days.
    prices = out[["open", "high", "low", "close"]]
    if prices.isna().any().any() or (prices <= 0).any().any():
        raise DataQualityError("OHLC 缺失或非正；不能据此计算趋势")
    if ((out.high < prices.max(axis=1)) | (out.low > prices.min(axis=1))).any():
        raise DataQualityError("OHLC 高低价关系异常")
    for col in ("amount", "volume", "turn", "peTTM", "pbMRQ", "psTTM"):
        if col in out:
            out[col] = numeric(out[col])
    if csindex:
        if "amount" in out:
            out["amount"] *= 1e8  # 亿元 -> 元
        if "volume" in out:
            out["volume"] *= 1e6  # 万手 -> 股
    return require_fresh(out, end)


def metric(value=None, *, source=None, asof=None, unit=None, reason=None, **extra):
    if isinstance(value, (float, np.floating)) and not np.isfinite(value):
        value = None
    return {"value": value, "status": "ok" if value is not None else "missing",
            "source": source, "asof": str(asof)[:10] if asof is not None else None,
            "unit": unit, **({"reason": reason or "输入数据不足或无效"} if value is None else {}), **extra}


def trend_metrics(frame, source):
    names = ("last_close", "ret_30d", "ma20_now", "ma20_prev5", "rsrs_score", "crowding_ratio")
    result = {name: metric(reason="有效行情不足") for name in names}
    if frame.empty:
        return result
    asof = frame.date.iloc[-1]
    def put(name, value, unit, **kw):
        result[name] = metric(float(value), source=source, asof=asof, unit=unit, **kw)
    put("last_close", frame.close.iloc[-1], "价格/点位")
    if len(frame) >= 31:
        put("ret_30d", (frame.close.iloc[-1] / frame.close.iloc[-31] - 1) * 100, "%")
    if len(frame) >= 25:
        ma = frame.close.rolling(20).mean()
        put("ma20_now", ma.iloc[-1], "价格/点位")
        put("ma20_prev5", ma.iloc[-6], "价格/点位")
    if len(frame) >= 90:
        for col, label in (("turn", "换手率"), ("amount", "成交额代理")):
            if col not in frame:
                continue
            sample = frame[col].tail(90)
            if sample.notna().all() and (sample >= 0).all() and sample.mean() > 0:
                put("crowding_ratio", sample.tail(5).mean() / sample.mean(), "倍", method=label)
                break
    # 18-day regression + 600 slopes, including today's slope: 617 bars.
    if len(frame) >= 617:
        sample = frame.tail(617)
        betas, r2s = [], []
        for i in range(17, len(sample)):
            window = sample.iloc[i-17:i+1]
            x, y = window.low.to_numpy(float), window.high.to_numpy(float)
            x0, y0 = x - x.mean(), y - y.mean()
            denom, total = x0 @ x0, y0 @ y0
            if denom <= 0 or total <= 0:
                betas.append(np.nan)
                r2s.append(np.nan)
                continue
            beta = (x0 @ y0) / denom
            betas.append(beta)
            r2s.append(max(0., 1 - ((y0 - beta*x0) ** 2).sum() / total))
        std = np.std(betas, ddof=1)
        if np.isfinite(std) and std > 0:
            put("rsrs_score", (betas[-1] - np.mean(betas)) / std * r2s[-1], "z×R²",
                method="N=18,M=600,ddof=1,含当前斜率；无右偏乘beta")
    return result


def valuation_metrics(frame, column, source, end):
    missing = metric(reason=f"{column} 无同口径有效序列")
    if frame.empty or column not in frame:
        return missing, missing.copy()
    out = frame[["date", column]].copy()
    out[column] = numeric(out[column])
    out = out.sort_values("date")
    # A missing/negative latest observation must not resurrect an older PE/PB.
    latest = out.iloc[-1]
    if pd.isna(latest[column]) or latest[column] <= 0:
        return metric(reason=f"最新 {column} 缺失或非正"), missing.copy()
    try:
        require_fresh(out, end)
    except DataQualityError as exc:
        return metric(reason=str(exc)), missing.copy()
    now = metric(float(latest[column]), source=source, asof=latest.date, unit="倍")
    boundary = latest.date - pd.DateOffset(years=5)
    window = out[out.date >= boundary]
    valid = window[column].notna() & (window[column] > 0)
    # Daily sample floor + boundary coverage + no long internal holes.
    valid_dates = window.loc[valid, "date"]
    enough = (out.date.min() <= boundary and len(valid_dates) >= 1000
              and valid.mean() >= .95 and not valid_dates.empty
              and (valid_dates.iloc[0] - boundary).days <= 14
              and valid_dates.diff().dt.days.max() <= 20)
    if enough:
        pct = float((window.loc[valid, column] <= latest[column]).mean() * 100)
        rank = metric(pct, source=source, asof=latest.date, unit="%",
                      samples=int(valid.sum()), method="同源同口径5年日频经验分位，<=当前值")
    else:
        rank = metric(reason="5年历史覆盖/样本数/连续性不足", source=source, asof=latest.date)
    return now, rank


STATEMENT_FIELDS = {
    "profit": {
        "revenue": ("TOTAL_OPERATE_INCOME", "营业总收入"),
        "net_profit_parent": ("PARENT_NETPROFIT", "归属于母公司所有者的净利润", "归属于母公司股东的净利润"),
        "net_profit": ("NETPROFIT", "净利润"),
    },
    "balance": {
        "assets": ("TOTAL_ASSETS", "资产总计"),
        "liabilities": ("TOTAL_LIABILITIES", "负债合计"),
        "equity_parent": ("TOTAL_PARENT_EQUITY", "归属于母公司股东权益合计", "归属于母公司所有者权益合计"),
        "cash": ("MONETARYFUNDS", "货币资金"),
        "accounts_receivable": ("ACCOUNTS_RECE", "应收账款"),
        "inventory": ("INVENTORY", "存货"),
        "current_assets": ("TOTAL_CURRENT_ASSETS", "流动资产合计"),
        "current_liabilities": ("TOTAL_CURRENT_LIAB", "流动负债合计"),
        "short_loan": ("SHORT_LOAN", "短期借款"),
        "long_loan": ("LONG_LOAN", "长期借款"),
        "bond_payable": ("BOND_PAYABLE", "应付债券"),
        "lease_liability": ("LEASE_LIAB", "租赁负债"),
    },
    "cashflow": {
        "cfo": ("NETCASH_OPERATE", "经营活动产生的现金流量净额"),
        "capex": ("CONSTRUCT_LONG_ASSET", "购建固定资产、无形资产和其他长期资产支付的现金"),
    },
}


def statement(frame, kind, end, start):
    date_col = next((c for c in ("REPORT_DATE", "报告日", "报告日期") if c in frame), None)
    if date_col is None:
        raise DataQualityError("缺少财务报告期")
    if "类型" in frame:
        frame = frame[frame["类型"].astype(str).str.contains("合并", na=False)].copy()
    if "币种" in frame and not frame["币种"].dropna().isin(["CNY", "人民币"]).all():
        raise DataQualityError("财报币种并非人民币")
    out = dated(frame, date_col, end, start)
    result = out[["date"]].copy()
    for target, aliases in STATEMENT_FIELDS[kind].items():
        col = next((c for c in aliases if c in out), None)
        result[target] = numeric(out[col]) if col else np.nan
    # Financial statement fields are all retained in raw CSV even when unmapped.
    if result.drop(columns="date").notna().sum().sum() == 0:
        raise DataQualityError(f"{kind} 关键字段均无有效数据")
    if kind == "cashflow":
        result["fcf"] = result.cfo - result.capex.where(result.capex >= 0)
    for col in ("NOTICE_DATE", "UPDATE_DATE", "更新日期"):
        if col in out:
            result[col] = out[col]
    return result


def etf_premium(prices, nav, source):
    if prices.empty or nav.empty:
        return metric(reason="缺少价格或单位净值")
    left, right = prices.iloc[-1], nav.iloc[-1]
    if left.date != right.date:
        return metric(reason="最新收盘价与净值日期不同，不能计算当前溢价")
    if pd.isna(right.nav) or right.nav <= 0:
        return metric(reason="单位净值无效")
    return metric((float(left.close) / float(right.nav) - 1)*100, source=source,
                  asof=left.date, unit="%", method="同日收盘价/单位净值-1；非实时IOPV溢价")


def reported_fund_metrics(overview, source, end):
    result = {k: metric(reason="基金档案缺少可核验字段") for k in
              ("reported_aum", "management_fee", "custody_fee", "total_fee")}
    if overview.empty:
        return result
    row = overview.iloc[0]
    text = str(row.get("净资产规模", ""))
    amount = re.search(r"([\d,.]+)\s*(亿元|万元|元)", text)
    date = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", text)
    if amount and date:
        try:
            asof = pd.Timestamp(year=int(date[1]), month=int(date[2]), day=int(date[3]))
            value = float(amount[1].replace(",", "")) * {"亿元": 1e8, "万元": 1e4, "元": 1}[amount[2]]
            if value > 0 and 0 <= (pd.Timestamp(end) - asof).days <= 200:
                result["reported_aum"] = metric(value, source=source, asof=asof, unit="元",
                    method="最近披露期净资产规模，不是实时AUM")
        except ValueError:
            pass
    for key, column in (("management_fee", "管理费率"), ("custody_fee", "托管费率")):
        rate = re.fullmatch(r"\s*(\d+(?:\.\d+)?)%[（(]每年[）)]\s*", str(row.get(column, "")))
        if rate:
            result[key] = metric(float(rate[1]), source=source, unit="%/年",
                                 verification="档案当前展示费率，生效日需核对基金公告")
    if all(result[key]["value"] is not None for key in ("management_fee", "custody_fee")):
        result["total_fee"] = metric(result["management_fee"]["value"] + result["custody_fee"]["value"],
            source=source, unit="%/年", method="管理费+托管费，未含其他费用；生效日待核验")
    return result
