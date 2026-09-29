from datetime import datetime, timedelta, timezone
import json

import numpy as np
import pandas as pd
import pytest

from scripts.data_sources import Collector, isolated_call
from scripts.finance_metrics import (DataQualityError, dated, history, metric,
    statement, trend_metrics, valuation_metrics, etf_premium)
from scripts.collect_finance import resolve_stock, resolve_index, index_collect
from search_news import SearchService, SearchResponse, SearchResult, _parse_published_date


def bars(n=650):
    dates = pd.bdate_range(end="2026-09-28", periods=n)
    low = 100 + np.arange(n)*.03 + np.sin(np.arange(n)/10)
    high = low + 2 + .5*np.sin(np.arange(n)/17)
    return pd.DataFrame(dict(date=dates, low=low, high=high, open=low+1,
                             close=low+1, amount=np.full(n, 1e8)))


def test_empty_and_broken_schema_fall_back_with_audit(tmp_path):
    def caller(provider, endpoint, params, timeout):
        if endpoint == "empty":
            return pd.DataFrame()
        if endpoint == "broken":
            return pd.DataFrame({"other": [1]})
        return bars(31)
    c = Collector(tmp_path, caller=caller)
    frame = c.fetch("prices", [("akshare", s, {}) for s in ["empty", "broken", "good"]],
                    normalize=lambda f: history(f, "2026-09-28"))
    assert len(frame) == 31
    assert [a["status"] for a in c.datasets["prices"]["attempts"]] == ["failed", "failed", "ok"]
    assert (tmp_path / "prices_2_raw.csv").exists()


def test_deadline_is_bounded():
    with pytest.raises(TimeoutError):
        isolated_call("akshare", "stock_zh_index_daily", {"symbol": "sh000300"}, .001)


def test_stale_primary_falls_back(tmp_path):
    def caller(provider, endpoint, params, timeout):
        out = bars(31)
        if endpoint == "old":
            out.date -= pd.DateOffset(years=1)
        return out
    c = Collector(tmp_path, caller=caller)
    c.fetch("prices", [("akshare", s, {}) for s in ["old", "new"]], normalize=lambda f: history(f, "2026-09-28"))
    assert c.datasets["prices"]["source"] == "akshare.new"


def test_conflicting_duplicates_and_nan_ohlc_rejected():
    frame = bars(31)
    with pytest.raises(DataQualityError):
        history(pd.concat([frame, frame.iloc[[-1]].assign(close=999)]), "2026-09-28")
    frame.loc[5, "low"] = np.nan
    with pytest.raises(DataQualityError):
        history(frame, "2026-09-28")


def test_csindex_units_and_future_filter():
    frame = bars(31)
    original = frame.amount.iloc[-1]
    frame.loc[len(frame)] = frame.iloc[-1]
    frame.loc[len(frame)-1, "date"] = pd.Timestamp("2026-09-29")
    clean = history(frame, "2026-09-28", csindex=True)
    assert len(clean) == 31
    assert clean.amount.iloc[-1] == original * 1e8


def test_rsrs_minimum_and_constant_windows():
    assert trend_metrics(bars(616), "fixture")["rsrs_score"]["value"] is None
    assert trend_metrics(bars(617), "fixture")["rsrs_score"]["status"] == "ok"
    frame = bars(617).assign(low=100., high=102.)
    assert trend_metrics(frame, "fixture")["rsrs_score"]["value"] is None
    json.dumps(trend_metrics(frame, "fixture"), allow_nan=False)


def test_crowding_zero_turnover_falls_back_to_amount():
    metrics = trend_metrics(bars(100).assign(turn=0.), "fixture")
    assert metrics["crowding_ratio"]["value"] == 1
    assert metrics["crowding_ratio"]["method"] == "成交额代理"


def test_valuation_requires_five_years_not_just_many_rows():
    frame = pd.DataFrame({"date": pd.bdate_range("2020-09-01", "2026-09-28")})
    frame["pe"] = 10.
    current, rank = valuation_metrics(frame, "pe", "fixture", "2026-09-28")
    assert current["value"] == 10 and rank["value"] == 100
    assert valuation_metrics(frame.tail(1100), "pe", "fixture", "2026-09-28")[1]["value"] is None
    hole = frame[~frame.date.between("2024-01-01", "2024-04-01")]
    assert valuation_metrics(hole, "pe", "fixture", "2026-09-28")[1]["value"] is None
    frame.loc[frame.index[-1], "pe"] = np.nan
    assert valuation_metrics(frame, "pe", "fixture", "2026-09-28")[0]["value"] is None


def test_cashflow_missing_capex_is_not_zero_and_period_not_annualized():
    raw = pd.DataFrame({"REPORT_DATE": ["2026-06-30", "2025-12-31"],
                        "NETCASH_OPERATE": [100, 500], "CONSTRUCT_LONG_ASSET": [None, 80]})
    out = statement(raw, "cashflow", "2026-09-28", "2020-01-01")
    assert out.fcf.iloc[0] == 420
    assert pd.isna(out.fcf.iloc[1])
    assert out.cfo.iloc[1] == 100


def test_stock_reports_ps_history_and_key_balance_lines(tmp_path):
    from scripts.collect_finance import stock_collect
    prices = bars(1454).assign(peTTM=20., pbMRQ=2., psTTM=1.5)
    statements = {
        "stock_profit_sheet_by_report_em": pd.DataFrame({
            "REPORT_DATE": ["2026-06-30"], "TOTAL_OPERATE_INCOME": [300.],
            "PARENT_NETPROFIT": [20.], "NETPROFIT": [21.]}),
        "stock_balance_sheet_by_report_em": pd.DataFrame({
            "REPORT_DATE": ["2026-06-30"], "TOTAL_ASSETS": [600.],
            "TOTAL_LIABILITIES": [240.], "TOTAL_PARENT_EQUITY": [350.],
            "MONETARYFUNDS": [100.], "ACCOUNTS_RECE": [60.],
            "INVENTORY": [30.], "TOTAL_CURRENT_ASSETS": [300.],
            "TOTAL_CURRENT_LIAB": [180.], "SHORT_LOAN": [12.],
            "LONG_LOAN": [8.], "BOND_PAYABLE": [4.], "LEASE_LIAB": [2.]}),
        "stock_cash_flow_sheet_by_report_em": pd.DataFrame({
            "REPORT_DATE": ["2026-06-30"], "NETCASH_OPERATE": [15.],
            "CONSTRUCT_LONG_ASSET": [5.]}),
    }
    def caller(provider, endpoint, params, timeout):
        if endpoint == "query_history_k_data_plus":
            return prices
        return statements.get(endpoint, pd.DataFrame())
    c = Collector(tmp_path, caller=caller)
    report = stock_collect(c, "600522", "2020-09-28", "2026-09-28")
    metrics = report["metrics"]
    assert metrics["ps_ttm"]["value"] == 1.5
    assert metrics["ps_ttm_pct_5y"]["value"] == 100.
    assert metrics["pe_ttm_pct_5y"]["value"] == 100.
    assert metrics["pb_pct_5y"]["value"] == 100.
    assert metrics["latest_accounts_receivable"]["value"] == 60.
    assert metrics["latest_short_loan"]["value"] == 12.
    assert metrics["latest_lease_liability"]["value"] == 2.
    assert metrics["ev_ebitda"]["status"] == "missing"
    assert metrics["industry_pe"]["status"] == "missing"
    assert "accounts_receivable" in pd.read_csv(tmp_path / "balance.csv")


def test_sina_dates_currency_and_consolidation():
    raw = pd.DataFrame({"报告日": ["20251231", "20251231"], "类型": ["母公司期末", "合并期末"],
                        "币种": ["CNY", "CNY"], "资产总计": [5, 10], "负债合计": [1, 3]})
    out = statement(raw, "balance", "2026-09-28", "2020-01-01")
    assert len(out) == 1 and out.assets.iloc[0] == 10
    assert out.date.iloc[0] == pd.Timestamp("2025-12-31")


def test_etf_premium_must_match_latest_dates():
    prices = bars(31)
    nav = pd.DataFrame({"date": [pd.Timestamp("2026-09-25")], "nav": [100.]})
    assert etf_premium(prices, nav, "fixture")["value"] is None
    nav["date"] = prices.date.iloc[-1]
    assert etf_premium(prices, nav, "fixture")["status"] == "ok"


def test_market_identity():
    assert resolve_stock("920001") == "bj"
    with pytest.raises(ValueError):
        resolve_stock("510300")
    assert resolve_index("创业板指")[0] == "399006"
    assert resolve_index("000300")[1][1] == "sh"
    with pytest.raises(ValueError):
        resolve_index("510300")


def test_shenzhen_index_does_not_query_csi_or_north_snapshot(tmp_path):
    called = []
    def caller(provider, endpoint, params, timeout):
        called.append(endpoint)
        if endpoint == "query_history_k_data_plus":
            return bars(650)
        if endpoint == "bond_zh_us_rate":
            return pd.DataFrame({"日期": ["2026-09-28"], "中国国债收益率10年": [2.]})
        raise AssertionError(endpoint)
    c = Collector(tmp_path, caller=caller)
    result = index_collect(c, "399006", "2020-09-28", "2026-09-28")
    assert called == ["query_history_k_data_plus", "bond_zh_us_rate"]
    assert result["metrics"]["north_10d"]["value"] is None
    assert result["metrics"]["current_pe"]["value"] is None


def test_tushare_pb_missing_token_is_reported(tmp_path, monkeypatch):
    from scripts.data_sources import provider_call
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    with pytest.raises(ValueError, match="TUSHARE_TOKEN"):
        provider_call("tushare", "index_dailybasic", {})


def test_news_no_key_and_dates():
    service = SearchService()
    assert service.search_news("query").success is False
    assert _parse_published_date("2026-09-28T09:00:00+08:00").hour == 1
    assert _parse_published_date("Mon, 28 Sep 2026 01:00:00 GMT").tzinfo == timezone.utc
    assert _parse_published_date("not a date") is None
    item = SearchResult("标题", "正文", "https://example.com/news", "来源")
    assert "https://example.com/news" in item.to_text()
    assert "发布日期待核验" in item.to_text()


def test_news_strict_window_and_timezone(monkeypatch):
    service = SearchService(["test-key"])
    now = datetime.now(timezone.utc)
    body = "这是一条有日期有来源的公司新闻。" * 15
    entries = [SearchResult(str(n), body, f"https://example.com/{n}", "fixture",
                           (now-timedelta(days=n)).isoformat(), body) for n in (1, 8, -1)]
    monkeypatch.setattr(service, "search_news", lambda *a, **k: SearchResponse("q", entries, "fixture"))
    response = service.search_stock_news("600519", "贵州茅台", days=7)
    assert [r.title for r in response.results] == ["1"]


def test_news_cache_isolation_and_empty_key():
    service = SearchService([" "])
    assert not service.is_available
    response = SearchResponse("q", [SearchResult("title", "body", "https://example.com", "fixture")], "fixture")
    service._put_cache("q", response)
    response.results[0].snippet = "changed"
    cached = service._get_cached("q")
    assert cached.results[0].snippet == "body"
    cached.results.clear()
    assert len(service._get_cached("q").results) == 1


def test_news_falls_back_to_eastmoney(monkeypatch):
    import scripts.data_sources
    body = "公司发布新的经营情况说明，这是可以核查的新闻正文。" * 8
    # Shanghai local timestamp is explicitly attached by the adapter.
    now = (datetime.now(timezone.utc)+timedelta(hours=8)-timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    monkeypatch.setattr(scripts.data_sources, "isolated_call", lambda *a: pd.DataFrame({
        "新闻标题": ["公司新闻"], "新闻内容": [body], "新闻链接": ["https://example.com/news"], "发布时间": [now]}))
    result = SearchService().search_stock_news("600519", "贵州茅台")
    assert result.success and result.provider.startswith("东方财富")


def test_tushare_growth_relative_uses_ttm_not_static_pe(tmp_path):
    def caller(provider, endpoint, params, timeout):
        if endpoint == "query_history_k_data_plus":
            return bars(650)
        if endpoint == "bond_zh_us_rate":
            return pd.DataFrame({"日期": ["2026-09-28"], "中国国债收益率10年": [2.]})
        assert endpoint == "index_dailybasic"
        value = 30. if params["ts_code"] == "399006.SZ" else 10.
        return pd.DataFrame({"trade_date": ["20260928"], "pe": [999.], "pe_ttm": [value], "pb": [2.]})
    c = Collector(tmp_path, caller=caller)
    result = index_collect(c, "399006", "2020-09-28", "2026-09-28", use_tushare=True)
    assert result["metrics"]["current_pe"]["value"] == 30
    assert result["metrics"]["relative_pe_ratio"]["value"] == 3
    assert result["metrics"]["relative_pe_pct_5y"]["value"] is None


def test_all_sources_unavailable_preserves_missing_stock_and_etf_fields(tmp_path):
    from scripts.collect_finance import stock_collect, etf_collect
    for kind, func, code, expected in [("stock", stock_collect, "920001", "latest_fcf"),
                                       ("etf", etf_collect, "510300", "aum")]:
        c = Collector(tmp_path/kind, caller=lambda *a: pd.DataFrame())
        report = func(c, code, "2020-09-28", "2026-09-28")
        assert report["metrics"][expected]["status"] == "missing"
        assert all(d["status"] == "missing" for d in c.datasets.values())
        json.dumps(report, allow_nan=False)


def test_reported_aum_is_assets_not_shares_and_dates_are_explicit():
    from scripts.finance_metrics import reported_fund_metrics
    overview = pd.DataFrame({"净资产规模": ["948.72亿元（截止至：2026年06月30日）"],
                             "份额规模": ["189.1489亿份（截止至：2026年06月30日）"],
                             "管理费率": ["0.15%（每年）"], "托管费率": ["0.05%（每年）"]})
    result = reported_fund_metrics(overview, "fixture", "2026-09-28")
    assert result["reported_aum"]["value"] == 948.72e8
    assert result["reported_aum"]["asof"] == "2026-06-30"
    assert result["total_fee"]["value"] == .2
    assert reported_fund_metrics(overview, "fixture", "2026-01-01")["reported_aum"]["value"] is None
    overview["净资产规模"] = "189.1489亿份（截止至：2026年06月30日）"
    assert reported_fund_metrics(overview, "fixture", "2026-09-28")["reported_aum"]["value"] is None
