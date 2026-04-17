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

## Step 4 — 实时价格与估值（新浪 hq API，直连无代理）

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

> ⚠️ 在系统代理（如 127.0.0.1:7897）下，`push2.eastmoney.com` 等域名可能被拦截导致失败。
> AKShare 失败时，以下数据按 Step 6 降级策略处理，**不得跳过或估算**。

```python
import os
os.environ["HTTP_PROXY"]  = "http://127.0.0.1:7897"
os.environ["HTTPS_PROXY"] = "http://127.0.0.1:7897"
import akshare as ak

try:
    # 分红历史
    dividend = ak.stock_history_dividend_detail(symbol=symbol, indicator="分红")
    print("=== 分红历史 ===")
    print(dividend.head(12))
except Exception as e:
    print(f"[数据缺失：分红历史，原因：{e}]")

try:
    # 股东人数变化（近8季度）
    holder_count = ak.stock_zh_a_gdhs(symbol=symbol)
    print("=== 股东人数 ===")
    print(holder_count.tail(10))
except Exception as e:
    print(f"[数据缺失：股东人数，原因：{e}]")

try:
    # 十大流通股东
    holders = ak.stock_circulate_stockholder_blocker(symbol=symbol)
    print("=== 十大流通股东 ===")
    print(holders)
except Exception as e:
    print(f"[数据缺失：十大股东，原因：{e}]")

try:
    # 行业PE估值
    industry_name = "制冷空调"   # ← 根据公司行业替换
    industry_pe   = ak.stock_board_industry_pe_cninfo(symbol=industry_name)
    print("=== 行业PE Band ===")
    print(industry_pe.tail(20))
except Exception as e:
    print(f"[数据缺失：行业PE，原因：{e}]")
```

---

## Step 6 — 数据异常检测（采集后必须执行）

```python
import sys, numpy as np
sys.stdout.reconfigure(encoding="utf-8")  # 修复 Windows GBK 终端乱码

# 1. 资产负债率异常检测（BaoStock最新年报数据偶有错误）
if not balance_df.empty:
    latest_bal = balance_df.sort_values("statDate").iloc[-1]
    lta = float(latest_bal["liabilityToAsset"])
    if lta < 0.05 or lta > 0.98:
        print("[WARN] liabilityToAsset={:.4f}，疑似错误，建议手动核查年报".format(lta))

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
    latest_date = profit_df["statDate"].max()
    print("BaoStock 最新数据截止：{}".format(latest_date))
    if latest_date < "2025-01-01":
        print("[WARN] BaoStock 未包含2025年数据，需手动补充")
```

---

## Step 7 — 降级策略（API 彻底失败时）

| 数据项 | 降级路径 |
|--------|----------|
| 十大股东 / 机构持仓 | 巨潮资讯 http://www.cninfo.com.cn → 搜索代码 → 股东信息 |
| 股东人数变化 | 东方财富 https://data.eastmoney.com → 股东研究 |
| 行业PE均值 | 中证指数官网 https://www.csindex.com.cn → 估值数据 |
| 分红历史 | 上交所/深交所官网 → 公司公告 → 利润分配 |
| 管理层持股/薪酬 | 年报原文 PDF（巨潮资讯下载）→ 第 4 节"董事、监事、高级管理人员" |
| 公司公告（增减持/质押）| 巨潮资讯 → 公告查询 → 近2年 |

所有无法获取的数据项必须以 `[数据缺失：原因]` 标注，不得跳过或估算。

---

## Step 8 — 采集完成确认清单

逐项核对后方可进入 M1-M6 分析模块：

```
[ ] 近5年利润表：营收(MBRevenue) / 净利润(netProfit) / 净利率(npMargin) / 毛利率(gpMargin)
[ ] 近5年ROE：roeAvg（注意：BaoStock 为累计ROE，非扣非）
[ ] 近5年偿债：liabilityToAsset / currentRatio / quickRatio / ebitToInterest
[ ] 近5年现金流质量：CFOToNP / CFOToOR
[ ] 近5年杜邦分解：dupontROE / dupontAssetStoEquity / dupontAssetTurn
[ ] 当前 PE / PB（Sina hq 接口 + BaoStock 计算，或手动补充）
[ ] 历史月度股价（BaoStock query_history_k_data_plus）
[ ] 分红历史（AKShare 或手动）
[ ] 股东人数变化（AKShare 或手动）
[ ] 十大流通股东（AKShare 或手动）
[ ] 行业PE均值（AKShare 或中证指数官网）
[ ] 至少3家可比同行核心财务指标
[ ] 近5年重大公告（增减持/质押/并购）
[ ] 管理层薪酬与持股
[ ] 数据异常检测（Step 6）已执行并注释异常项
```

采集完成后将结果汇总，再进入分析框架。
