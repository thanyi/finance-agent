---
name: stock-data-collect
description: 在网上查询A股公司基础数据，给A股价值投资深度分析框架使用
---

# stock-data-collect

采集指定A股标的的完整基础数据，供后续六模块分析使用。

## 使用方式

```
/stock-data-collect [股票代码]
示例：/stock-data-collect 002011（深圳）或 600036（上海）
```

---

## Step 1 — 确认环境依赖

```bash
pip install akshare baostock requests pandas
```

---

## Step 2 — 确定市场前缀

BaoStock 需要区分 `sz.`（深圳）和 `sh.`（上海）。代码开头规则：
- `0xxxxx` / `3xxxxx` → 深圳 → `sz.002011`
- `6xxxxx` / `5xxxxx` → 上海 → `sh.600036`

```python
def get_bs_code(symbol: str) -> str:
    return ("sz." if symbol.startswith(("0", "3")) else "sh.") + symbol

symbol    = "002011"           # ← 替换目标代码（纯数字，不含前缀）
symbol_bs = get_bs_code(symbol)  # → "sz.002011"
```

---

## Step 3 — BaoStock 财务数据（主力数据源，无代理依赖）

BaoStock 接口在本环境稳定可用。**以下字段名均经实测验证，直接使用。**

```python
import sys
sys.stdout.reconfigure(encoding="utf-8")  # 修复 Windows GBK 终端乱码

import baostock as bs
import pandas as pd

lg = bs.login()

years = range(2019, 2026)  # 近6-7年（含2025，每年更新上限）

profit_list, balance_list, cash_list, dupont_list = [], [], [], []

for year in years:
    for q in range(1, 5):
        # ── 利润数据 ─────────────────────────────────────────────
        # 字段：code | statDate | roeAvg | npMargin | gpMargin |
        #        netProfit | epsTTM | MBRevenue | totalShare | liqaShare
        rs = bs.query_profit_data(code=symbol_bs, year=year, quarter=q)
        rows = []
        while rs.error_code == "0" and rs.next():
            rows.append(rs.get_row_data())
        if rows:
            profit_list.append(pd.DataFrame(rows, columns=rs.fields))

    # 年报数据（只取 Q4）
    # ── 偿债能力 ──────────────────────────────────────────────────
    # 字段：code | statDate | currentRatio | quickRatio | cashRatio |
    #        YOYLiability | liabilityToAsset | assetToEquity
    rs = bs.query_balance_data(code=symbol_bs, year=year, quarter=4)
    rows = []
    while rs.error_code == "0" and rs.next():
        rows.append(rs.get_row_data())
    if rows:
        balance_list.append(pd.DataFrame(rows, columns=rs.fields))

    # ── 现金流质量 ────────────────────────────────────────────────
    # 字段：code | statDate | CAToAsset | NCAToAsset | tangibleAssetToAsset |
    #        ebitToInterest | CFOToOR | CFOToNP | CFOToGr
    rs = bs.query_cash_flow_data(code=symbol_bs, year=year, quarter=4)
    rows = []
    while rs.error_code == "0" and rs.next():
        rows.append(rs.get_row_data())
    if rows:
        cash_list.append(pd.DataFrame(rows, columns=rs.fields))

    # ── 杜邦分析 ──────────────────────────────────────────────────
    # 字段：code | statDate | dupontROE | dupontAssetStoEquity |
    #        dupontAssetTurn | dupontPnitoni | dupontNitogr |
    #        dupontTaxBurden | dupontIntburden | dupontEbittogr
    rs = bs.query_dupont_data(code=symbol_bs, year=year, quarter=4)
    rows = []
    while rs.error_code == "0" and rs.next():
        rows.append(rs.get_row_data())
    if rows:
        dupont_list.append(pd.DataFrame(rows, columns=rs.fields))

profit_df  = pd.concat(profit_list,  ignore_index=True) if profit_list  else pd.DataFrame()
balance_df = pd.concat(balance_list, ignore_index=True) if balance_list else pd.DataFrame()
cash_df    = pd.concat(cash_list,    ignore_index=True) if cash_list    else pd.DataFrame()
dupont_df  = pd.concat(dupont_list,  ignore_index=True) if dupont_list  else pd.DataFrame()

# ── 利润数据展示 ──────────────────────────────────────────────
cols_p = ["statDate", "roeAvg", "npMargin", "gpMargin", "netProfit", "MBRevenue"]
if not profit_df.empty:
    # 年报部分：只展示 -12-31 的年报数据
    annual_profit = profit_df[profit_df["statDate"].str.endswith("-12-31")].copy()
    # 年报ROE说明：BaoStock的 roeAvg 为累计ROE（非扣非口径），扣非ROE见 Step 3a
    print("=== 年度利润数据（年报） ===")
    print(annual_profit[cols_p].to_string(index=False))

    # 最新季度补充：展示当年已披露的最新非年报季度行
    non_annual = profit_df[~profit_df["statDate"].str.endswith("-12-31")].copy()
    if not non_annual.empty:
        latest_quarter = non_annual.sort_values("statDate").iloc[[-1]]
        print("\n=== 最新季度（累计数据，非全年）===")
        print(latest_quarter[cols_p].to_string(index=False))
        print("注：季度数据为年初至该季度末的累计值，非全年口径")
    else:
        print("\n[数据缺失：当年季度报告暂未入库]")

# ── 历史月度行情 ───────────────────────────────────────────────
rs = bs.query_history_k_data_plus(
    symbol_bs, "date,close,volume,turn",
    start_date="2020-01-01", end_date="2026-12-31",
    frequency="m", adjustflag="3"
)
hist_rows = []
while rs.error_code == "0" and rs.next():
    hist_rows.append(rs.get_row_data())
hist_df = pd.DataFrame(hist_rows, columns=rs.fields)

bs.logout()
```

---

## Step 3a — 财务指标交叉验证（新浪API，无代理问题）

> ⚠️ **BaoStock 已知问题**：`liabilityToAsset`（资产负债率）等字段偶有数据错误（如2024年年报部分股票异常显示为0.5%）。  
> 以下使用**新浪财经财务指标API**（与Step 4同域名，`ProxyHandler({})`直连无代理问题）进行交叉验证。

```python
# ── 新浪财务分析指标（直连，无代理依赖）──
# 此接口返回 80+ 财务指标，可直接与 BaoStock 对比
import urllib.request as _req
import json as _json
import akshare as ak

_sina_fin = None
try:
    _sina_fin = ak.stock_financial_analysis_indicator(symbol=symbol, start_year="2020")
    # 转数值列
    _num_cols = ["净资产收益率(%)","资产负债率(%)","流动比率","速动比率",
                 "销售毛利率(%)","销售净利率(%)","经营现金净流量与净利润的比率(%)",
                 "主营业务收入增长率(%)","净利润增长率(%)","应收账款周转率(次)",
                 "扣除非经常性损益后的净利润(元)","扣除非经常性损益后的每股收益(元)",
                 "每股净资产_调整后(元)"]
    for _c in _num_cols:
        if _c in _sina_fin.columns:
            _sina_fin[_c] = pd.to_numeric(_sina_fin[_c], errors="coerce")

    # ── 交叉验证：BaoStock vs 新浪 ──
    print("\n=== 财务指标交叉验证（新浪 vs BaoStock）===")
    
    # 日期列可能是 object(datetime.date) 或 string
    _sina_fin["_date_str"] = _sina_fin["日期"].astype(str)
    _annual = _sina_fin[_sina_fin["_date_str"].str.endswith("-12-31")].copy()
    
    if not _annual.empty:
        _latest = _annual.sort_values("_date_str").iloc[-1]
        print(f"最新年报：{_latest['日期']}")
        print(f"  资产负债率(新浪)：{_latest['资产负债率(%)']:.2f}%")
        print(f"  净资产收益率(新浪)：{_latest['净资产收益率(%)']:.2f}%")
        print(f"  流动比率(新浪)：{_latest['流动比率']:.2f}")
        print(f"  经营CFO/净利润(新浪)：{_latest['经营现金净流量与净利润的比率(%)']:.2f}")

        # ── 扣非ROE计算 ──
        if "扣除非经常性损益后的净利润(元)" in _sina_fin.columns:
            _kf_netprofit = float(_latest['扣除非经常性损益后的净利润(元)'])
            # 获取同期BaoStock净利润以计算扣非比例
            _kf_pct = None
            if not profit_df.empty:
                _bs_annual = profit_df[profit_df["statDate"].str.endswith("-12-31")].copy()
                _bs_annual["netProfit"] = pd.to_numeric(_bs_annual["netProfit"], errors="coerce")
                _latest_bs = _bs_annual.sort_values("statDate").iloc[-1]
                _bs_np = float(_latest_bs["netProfit"])
                if _bs_np > 0:
                    _kf_pct = _kf_netprofit / _bs_np * 100
            # 扣非ROE ≈ 加权ROE × 扣非占比（近似）
            _roe_col = "加权净资产收益率(%)" if "加权净资产收益率(%)" in _sina_fin.columns else "净资产收益率(%)"
            _std_roe = float(_latest[_roe_col]) if _roe_col in _sina_fin.columns else None
            if _std_roe and _kf_pct:
                _kf_roe = _std_roe * _kf_pct / 100
                print(f"  ─────────────────────────────")
                print(f"  【扣非ROE(≈)】= {_std_roe:.2f}% × ({_kf_pct:.1f}% 扣非比例) = {_kf_roe:.2f}%")
                print(f"  扣非净利润：{_kf_netprofit/1e8:.2f}亿，扣非比例：{_kf_pct:.1f}%")
                if _kf_pct > 90:
                    print(f"  ✅ 扣非比例>90%，利润质量高")
                elif _kf_pct > 70:
                    print(f"  🟡 扣非比例70-90%，利润质量中")
                else:
                    print(f"  ⚠️ 扣非比例<70%，利润严重依赖非经常性损益")
            elif _std_roe:
                _kf_roe = _std_roe
                print(f"  ⚠️ 扣非ROE无法精确计算（缺少BaoStock净利润数据）")

        # ── 交叉验证 BaoStock liabilityToAsset ──
        if not balance_df.empty:
            _bs_bal = balance_df.sort_values("statDate").iloc[-1]
            _bs_lta = float(_bs_bal["liabilityToAsset"])
            _sina_lta = float(_latest['资产负债率(%)']) / 100
            _diff = abs(_bs_lta - _sina_lta)
            if _diff > 0.10:
                print(f"  ⚠️ [数据冲突] BaoStock 资产负债率={_bs_lta*100:.2f}%")
                print(f"     → 新浪={_sina_lta*100:.2f}%，差异{_diff*100:.1f}个百分点")
                print(f"     → 建议信任新浪数据，BaoStock该字段可能存在错误")

        # ── 交叉验证 CFO/净利润 ──
        if not cash_df.empty:
            _bs_cash = cash_df.sort_values("statDate").iloc[-1]
            _bs_cfo = float(_bs_cash["CFOToNP"]) if "CFOToNP" in _bs_cash.index or "CFOToNP" in cash_df.columns else None
            if _bs_cfo is not None:
                _sina_cfo = float(_latest['经营现金净流量与净利润的比率(%)'])
                if abs(_bs_cfo - _sina_cfo) > 0.5:
                    print(f"  ⚠️ [数据冲突] BaoStock CFO/净利润={_bs_cfo:.2f}")
                    print(f"     → 新浪={_sina_cfo:.2f}，注意核实")
    
    # ── 5年扣非ROE趋势 ──
    _an5 = _annual.tail(5).copy()
    if "扣除非经常性损益后的净利润(元)" in _sina_fin.columns and not profit_df.empty:
        _bs_annual = profit_df[profit_df["statDate"].str.endswith("-12-31")].copy()
        _bs_annual["netProfit"] = pd.to_numeric(_bs_annual["netProfit"], errors="coerce")
        _roe_col = "加权净资产收益率(%)" if "加权净资产收益率(%)" in _sina_fin.columns else "净资产收益率(%)"
        
        _kf_rows = []
        for _, _r in _an5.iterrows():
            _kf_np = float(_r["扣除非经常性损益后的净利润(元)"]) if pd.notna(_r.get("扣除非经常性损益后的净利润(元)")) else None
            _std_roe = float(_r[_roe_col]) if pd.notna(_r.get(_roe_col)) else None
            # 找对应年度的BaoStock净利润
            _yr = str(_r["日期"])[:4]
            _bs_match = _bs_annual[_bs_annual["statDate"].str.contains(_yr)]
            if not _bs_match.empty and _kf_np and _std_roe:
                _bs_np_yr = float(_bs_match.iloc[0]["netProfit"])
                if _bs_np_yr > 0:
                    _kf_roe_yr = _std_roe * (_kf_np / _bs_np_yr)
                    _kf_rows.append({"日期": _r["日期"], "标准ROE(%)": f"{_std_roe:.1f}",
                                     "扣非ROE(%)": f"{_kf_roe_yr:.1f}", "扣非净利润(亿)": f"{_kf_np/1e8:.2f}"})
        
        if _kf_rows:
            _kf_df = pd.DataFrame(_kf_rows)
            print(f"\n=== 5年扣非ROE趋势 ===")
            print(_kf_df.to_string(index=False))
            print("注：扣非ROE = 加权ROE × (扣非净利润/净利润)，BaoStock净利润口径")
    
    # ── 5年关键指标一览 ──
    _show_cols = [c for c in ["日期","净资产收益率(%)","资产负债率(%)",
                               "流动比率","经营现金净流量与净利润的比率(%)",
                               "主营业务收入增长率(%)","净利润增长率(%)"]
                  if c in _an5.columns]
    print(f"\n=== 5年财务趋势（新浪，交叉验证口径）===")
    print(_an5[_show_cols].to_string(index=False))

except Exception as _e:
    print(f"[数据缺失：新浪财务指标，原因：{_e}]")
    print("[注] 该数据源为可选补充，不影响BaoStock主数据采集")
```

> 此接口无需代理，经实测可直连。

```python
import sys, urllib.request
sys.stdout.reconfigure(encoding="utf-8")  # 修复 Windows GBK 终端乱码

market = "sz" if symbol.startswith(("0", "3")) else "sh"
url = f"https://hq.sinajs.cn/list={market}{symbol}"
req = urllib.request.Request(
    url,
    headers={"Referer": "https://finance.sina.com.cn", "User-Agent": "Mozilla/5.0"}
)
# ProxyHandler({}) 强制直连，绕开 HTTP_PROXY / 系统代理（Clash/VPN 环境下必须）
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open(req, timeout=10) as r:
    raw = r.read().decode("gbk")

# 格式："名称,今开,昨收,当前价,最高,最低,买1,卖1,成交量,成交额,..."
fields = raw.split('"')[1].split(",")
current_price  = float(fields[3])
prev_close     = float(fields[2])
today_open     = float(fields[1])
today_high     = float(fields[4])
today_low      = float(fields[5])
volume         = int(fields[8])     # 手
turnover       = float(fields[9])   # 元

# 从 BaoStock 最新年报计算 PE / PB（近似值）
latest_profit = profit_df[profit_df["statDate"].str.endswith("-12-31")].sort_values("statDate").iloc[-1]
eps_annual = float(latest_profit["netProfit"]) / float(latest_profit["totalShare"])  # 元/股
pe_ttm     = current_price / eps_annual if eps_annual > 0 else None

# PB 近似值（通过 ROE 和资产倍数推算净资产/股）
latest_dupont  = dupont_df.sort_values("statDate").iloc[-1]
avg_equity     = float(latest_profit["netProfit"]) / float(latest_dupont["dupontROE"])  # 平均净资产
bps_approx     = avg_equity / float(latest_profit["totalShare"])
pb_approx      = current_price / bps_approx if bps_approx > 0 else None

print(f"当前价格：{current_price} 元")
print(f"PE（年度EPS）：{pe_ttm:.2f}x" if pe_ttm else "PE：[数据缺失]")
print(f"PB（近似）：{pb_approx:.2f}x" if pb_approx else "PB：[数据缺失]")
```

---

## Step 5 — AKShare 补充数据（可选，代理环境下不稳定）

> ⚠️ 在 macOS 全局代理（如 ClashX 127.0.0.1:7897）下，`push2.eastmoney.com` 等域名可能被拦截导致 `ProxyError`。
> 若遭遇 ProxyError，可按以下方式取消代理 env vars 后重试：
> ```python
> import os
> for k in ['http_proxy','https_proxy','HTTP_PROXY','HTTPS_PROXY']:
>     os.environ.pop(k, None)
> ```
> 另外，`stock_zygc_em`（主营构成）依赖于 eastmoney.com 域名，遭遇 ProxyError 时建议先取消系统代理环境变量再试。
> AKShare 最终失败时，数据按 Step 6 降级策略处理，**不得跳过或估算**。

```python
import akshare as ak
# 如遇 ProxyError，取消下一行注释：
# for k in ['http_proxy','https_proxy','HTTP_PROXY','HTTPS_PROXY']: os.environ.pop(k, None)

try:
    # 分红历史
    dividend = ak.stock_history_dividend_detail(symbol=symbol, indicator="分红")
    print("=== 分红历史 ===")
    print(dividend.head(12))
except Exception as e:
    print(f"[数据缺失：分红历史，原因：{e}]")

try:
    # 股东人数变化（近8季度）
    # 使用 detail_em 版本（按股票代码精确查询）
    holder_count = ak.stock_zh_a_gdhs_detail_em(symbol=symbol)
    print("=== 股东人数 ===")
    print(holder_count.tail(10))
except Exception as e:
    print(f"[数据缺失：股东人数，原因：{e}]")

try:
    # 十大流通股东
    holders = ak.stock_circulate_stock_holder(symbol=symbol)
    print("=== 十大流通股东（最新4期）===")
    # 只展示近4个季度（最新在前）
    print(holders.head(40).to_string(index=False))
except Exception as e:
    print(f"[数据缺失：十大股东，原因：{e}]")

try:
    # 主营收入构成
    market_prefix = "SZ" if symbol.startswith(("0", "3")) else "SH"
    business = ak.stock_zygc_em(symbol=f"{market_prefix}{symbol}")
    print("=== 主营构成（最新年报按产品分类） ===")
    # 筛选最新报告期的按产品分类数据
    latest_date = pd.to_datetime(business["报告日期"]).max()
    product_data = business[(business["报告日期"] == latest_date.strftime("%Y-%m-%d")) &
                            (business["分类类型"] == "按产品分类")]
    print(product_data.to_string(index=False))
except Exception as e:
    print(f"[数据缺失：主营构成，原因：{e}]")

try:
    # 行业PE估值
    # 获取证监会行业分类整体的市盈率表，然后过滤出相关行业
    from datetime import date
    today_str = date.today().strftime("%Y%m%d")
    industry_df = ak.stock_industry_pe_ratio_cninfo(symbol="证监会行业分类", date=today_str)
    # 盾安环境属于 C38 电气机械和器材制造业
    target_industry = "电气机械和器材制造业"
    match = industry_df[industry_df["行业名称"].str.contains(target_industry, na=False)]
    if not match.empty:
        print(f"=== 行业PE（{target_industry}） ===")
        print(match[["行业名称", "静态市盈率-加权平均", "静态市盈率-中位数", "公司数量"]].to_string(index=False))
    else:
        print(f"[数据缺失：行业PE，原因：未找到 {target_industry} 的行业数据]")
except Exception as e:
    print(f"[数据缺失：行业PE，原因：{e}]")
```

---

## Step 6 — 数据异常检测（采集后必须执行）

```python
import sys, numpy as np
sys.stdout.reconfigure(encoding="utf-8")  # 修复 Windows GBK 终端乱码

# 1. 资产负债率异常检测（BaoStock最新年报数据偶有错误）
#    若 Step 3a 执行了新浪交叉验证，此处会自动对比差异
if not balance_df.empty:
    latest_bal = balance_df.sort_values("statDate").iloc[-1]
    lta = float(latest_bal["liabilityToAsset"])
    if lta < 0.05 or lta > 0.98:
        print("[WARN] liabilityToAsset={:.4f}，疑似错误，建议信任新浪财务指标数据".format(lta))
        print("[提示] BaoStock 部分股票年报的 liabilityToAsset 字段存在已知 bug")
        print("[参考] 若已执行 Step 3a 新浪交叉验证，请以其显示的资产负债率为准")

# 2+3. ROE 极值 & 净利润符号一致性（annual_p 统一定义，避免跨块依赖）
if not profit_df.empty:
    annual_p = profit_df[profit_df["statDate"].str.endswith("-12-31")].copy()
    annual_p["roeAvg"]    = pd.to_numeric(annual_p["roeAvg"],    errors="coerce")
    annual_p["netProfit"] = pd.to_numeric(annual_p["netProfit"], errors="coerce")

    anomaly = annual_p[annual_p["roeAvg"].abs() > 1.0]
    if not anomaly.empty:
        print("[WARN] ROE绝对值>100%，需确认是否为减值/重组年度：")
        print(anomaly[["statDate", "roeAvg", "netProfit"]])

    losses = annual_p[annual_p["netProfit"] < 0]
    if len(losses) >= 2:
        print("[WARN] 以下年份净利润为负，请评估退市风险：{}".format(list(losses["statDate"])))

# 4. BaoStock 是否返回最新年份（检查数据时效）
if not profit_df.empty:
    from datetime import date
    latest = pd.to_datetime(profit_df["statDate"]).max().date()
    print("BaoStock 最新数据截止：{}".format(latest))
    months_lag = (date.today() - latest).days / 30
    if months_lag > 6:
        print(f"[WARN] BaoStock最新数据为{latest}，距今约{months_lag:.0f}个月，建议手动补充最新季报")
```

---

## Step 7 — 降级策略（API 彻底失败时）

| 数据项 | 当前API | 降级/替代路径 |
|--------|---------|--------------|
| 财务指标（ROE/负债率/CFO） | BaoStock + 新浪交叉验证 | `stock_financial_analysis_indicator`（新浪，无代理问题，推荐替代）|
| 利润表/资产负债表/现金流 | BaoStock | 东财：`stock_*_sheet_by_report_em`（需加SZ/SH前缀，注意代理）|
| 十大股东 / 机构持仓 | `stock_circulate_stock_holder` | 巨潮资讯 http://www.cninfo.com.cn → 搜索代码 → 股东信息 |
| 股东人数变化 | `stock_zh_a_gdhs_detail_em` | 东方财富 https://data.eastmoney.com → 股东研究 |
| 主营收入构成 | `stock_zygc_em`（需 SZ/SH 前缀） | 年报原文（巨潮资讯下载）→ 主营业务分析 |
| 行业PE均值 | `stock_industry_pe_ratio_cninfo` | 中证指数官网 https://www.csindex.com.cn → 估值数据 |
| 分红历史 | `stock_history_dividend_detail` | 上交所/深交所官网 → 公司公告 → 利润分配 |
| 管理层持股/薪酬 | 年报原文 | 巨潮资讯下载年报 PDF → 第 4 节 |
| 公司公告（增减持/质押）| 巨潮资讯 | 公告查询 → 近2年 |
| **BaoStock 数据异常** | **优先信任新浪** | 已知 bug：liabilityToAsset 极值异常(↘0.5%) |

所有无法获取的数据项必须以 `[数据缺失：原因]` 标注，不得跳过或估算。

---

## Step 8 — 采集完成确认清单

逐项核对后方可进入 M1-M6 分析模块：

```
[ ] 近5年利润表（BaoStock query_profit_data）
[ ] 近5年ROE：roeAvg（BaoStock累计ROE，非扣非） → 扣非ROE以Step 3a新浪数据为准
[ ] 近5年偿债：liabilityToAsset / currentRatio / quickRatio / ebitToInterest
[ ] 近5年现金流质量：CFOToNP / CFOToOR
[ ] 近5年杜邦分解：dupontROE / dupontAssetStoEquity / dupontAssetTurn
[ ] 当前 PE / PB（Sina hq API + BaoStock 计算）
[ ] 历史月度股价（BaoStock query_history_k_data_plus）
[ ] 分红历史（stock_history_dividend_detail）
[ ] 股东人数变化（stock_zh_a_gdhs_detail_em）
[ ] 十大流通股东（stock_circulate_stock_holder）
[ ] 主营收入构成（stock_zygc_em 需加 SZ/SH 前缀）
[ ] 行业PE均值（stock_industry_pe_ratio_cninfo，按证监行业分类筛选）
[ ] 至少3家可比同行核心财务指标
[ ] 近5年重大公告（增减持/质押/并购）
[ ] 管理层薪酬与持股（年报原文）
[ ] 数据异常检测（Step 6）已执行并注释异常项
[ ] 代理环境：ProxyError 时按 Step 5 说明取消代理env
[ ] 财务指标交叉验证（Step 3a）：新浪 vs BaoStock，异常数据已标注
[ ] ⚠️ BaoStock 已知 bug：liabilityToAsset 偶有极值异常，以新浪为准
```

采集完成后将结果汇总，再进入分析框架。
