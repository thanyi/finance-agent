---
name: etf-data-collect
description: 在网上查询A股ETF基础数据，给ETF分析框架使用
---

# etf-data-collect

采集指定A股ETF的完整基础数据，供后续六模块分析使用。

## 使用方式

```
/etf-data-collect [ETF代码]
示例：/etf-data-collect 510300（沪深300ETF）或 159915（创业板ETF）
```

---

## Step 1 — 确认环境依赖

```bash
pip install akshare baostock requests pandas numpy
```

---

## Step 2 — 确定ETF类型与市场前缀

```python
import sys
sys.stdout.reconfigure(encoding="utf-8")  # 修复 Windows GBK 终端乱码

symbol = "510300"  # ← 替换目标ETF代码（纯数字，不含前缀）

# 市场前缀规则（同股票）
market = "sz" if symbol.startswith(("1", "3")) else "sh"
bs_code = f"{market}.{symbol}"  # BaoStock格式：sh.510300 / sz.159915

# ETF类型判断（根据代码段初步分类，需结合名称确认）
# 510xxx / 511xxx / 512xxx / 513xxx → 上交所股票/债券/商品/跨境 ETF
# 159xxx / 161xxx / 164xxx → 深交所股票/债券/跨境 ETF
# 518xxx → 商品ETF（如黄金）
print(f"ETF代码：{symbol}，市场：{market.upper()}，BaoStock代码：{bs_code}")

# 必须手动确认以下信息后再继续：
# etf_type = "equity"    # 股票型（宽基/行业/主题）
# etf_type = "bond"      # 债券型（国债/企债/可转债）
# etf_type = "commodity" # 商品型（黄金/原油）
# etf_type = "qdii"      # 跨境型（QDII）
```

---

## Step 3 — ETF历史行情（BaoStock主路径）与补充信息（AKShare可选）

> 历史行情优先使用 BaoStock，避免直接请求代理敏感的东方财富链路。
> ⚠️ AKShare / 东方财富接口在代理环境（如 127.0.0.1:7897）下不稳定，只作补充信息源；失败时按 Step 11 降级处理，**不得跳过或估算**。
> ⚠️ BaoStock 对 ETF 历史数据的覆盖存在局限：部分 ETF 仅返回近期数据（数十条），若 `len(etf_df) < 200` 须在 Step 10 标注并按 Step 11 补充。

```python
import sys
sys.stdout.reconfigure(encoding="utf-8")
import akshare as ak
import baostock as bs
import pandas as pd

# 3.1 ETF补充快照（可选：AKShare，失败不影响主流程）
try:
    etf_spot = ak.fund_etf_spot_em()
    etf_info = etf_spot[etf_spot["代码"] == symbol].reset_index(drop=True)
    if etf_info.empty:
        print(f"[数据缺失：ETF补充快照，原因：{symbol} 未在东方财富ETF列表中找到]")
    else:
        print("=== ETF实时行情（AKShare补充）===")
        print(etf_info.to_string(index=False))
except Exception as e:
    print(f"[数据缺失：ETF补充快照，原因：{e}]")

# 3.2 ETF历史日行情（主路径：BaoStock，前复权）
etf_df = None
etf_hist = None
etf_hist_adj = None

lg = bs.login()
rs = bs.query_history_k_data_plus(
    bs_code,
    "date,open,high,low,close,volume,amount,turn,pctChg",
    start_date="2020-01-01",
    end_date="2026-12-31",
    frequency="d",
    adjustflag="2",  # 前复权
)
rows = []
while rs.error_code == "0" and rs.next():
    rows.append(rs.get_row_data())
bs.logout()

if rows:
    etf_df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume", "amount", "turnover", "pct_chg"])
    for col in ["open", "high", "low", "close", "volume", "amount", "turnover", "pct_chg"]:
        etf_df[col] = pd.to_numeric(etf_df[col], errors="coerce")
    etf_df["date"] = pd.to_datetime(etf_df["date"])
    etf_hist_adj = etf_df.copy()
    etf_hist = etf_df.rename(columns={
        "date": "日期", "open": "开盘", "high": "最高", "low": "最低",
        "close": "收盘", "volume": "成交量", "amount": "成交额",
        "turnover": "换手率", "pct_chg": "涨跌幅",
    })
    print(f"\n=== ETF历史行情（末5行）===")
    print(etf_df[["date", "close", "pct_chg", "amount", "turnover"]].tail(5).to_string(index=False))
    print(f"\n共 {len(etf_df)} 条记录，起始日期：{etf_df['date'].iloc[0].date()}")
    if len(etf_df) < 200:
        print(f"⚠️ [WARN] 仅返回 {len(etf_df)} 条，BaoStock对此ETF历史覆盖不足，须按 Step 11 手动补充")
else:
    print("[数据缺失：ETF历史行情，原因：BaoStock 未返回数据；请按 Step 11 手动补充]")
```

---

## Step 4 — 标的指数历史数据（BaoStock，仅股票型ETF）

> 用于计算跟踪误差。债券/商品/跨境ETF跳过此步，记为 `[不适用：非股票型ETF]`。

```python
import baostock as bs
import numpy as np

# 主要指数 BaoStock 代码对照表：
# 沪深300 → sh.000300  |  中证500 → sh.000905  |  上证50 → sh.000016
# 创业板指 → sz.399006  |  科创50 → sh.000688  |  中证1000 → sh.000852
# 上证指数 → sh.000001  |  深证成指 → sz.399001

index_bs_code = "sh.000300"  # ← 替换为ETF追踪的指数代码

# ETF history is already available from Step 3; only query index history here
lg = bs.login()
rs = bs.query_history_k_data_plus(
    index_bs_code,
    "date,close,pctChg",
    start_date="2020-01-01",
    end_date="2026-12-31",
    frequency="d"
)
idx_rows = []
while rs.error_code == "0" and rs.next():
    idx_rows.append(rs.get_row_data())
bs.logout()

idx_df = pd.DataFrame(idx_rows, columns=["date", "close", "pctChg"])
idx_df["close"]   = pd.to_numeric(idx_df["close"],   errors="coerce")
idx_df["pctChg"]  = pd.to_numeric(idx_df["pctChg"],  errors="coerce")
idx_df["date"]    = pd.to_datetime(idx_df["date"])

print(f"=== 指数历史数据（{index_bs_code}，末5行）===")
print(idx_df.tail(5).to_string(index=False))
```

---

## Step 5 — 跟踪质量计算（仅股票型ETF）

```python
# 对齐ETF与指数日期
etf_ret = etf_hist_adj[["date", "pct_chg"]].copy()
etf_ret.columns = ["date", "etf_pct"]
etf_ret["date"]    = pd.to_datetime(etf_ret["date"])
etf_ret["etf_pct"] = pd.to_numeric(etf_ret["etf_pct"], errors="coerce")

merged = pd.merge(etf_ret, idx_df[["date", "pctChg"]], on="date", how="inner")
merged["diff"] = merged["etf_pct"] - merged["pctChg"]

# 跟踪误差（年化）= 日差值标准差 × √252
tracking_error_annual = merged["diff"].std() * np.sqrt(252)

# 跟踪偏差（年化）= ETF年化收益 - 指数年化收益
# 取近1年数据计算
one_year_data = merged[merged["date"] >= merged["date"].max() - pd.DateOffset(years=1)]
etf_cagr_1y   = (1 + one_year_data["etf_pct"] / 100).prod() - 1
idx_cagr_1y   = (1 + one_year_data["pctChg"] / 100).prod() - 1
tracking_diff_1y = (etf_cagr_1y - idx_cagr_1y) * 100  # 百分比

print(f"=== 跟踪质量（基于{len(merged)}个交易日）===")
print(f"年化跟踪误差（TE）：{tracking_error_annual:.4f}%")
print(f"近1年跟踪偏差（TD）：{tracking_diff_1y:.4f}%")
print(f"  TE < 0.5%  → 优秀 | 0.5~1% → 良好 | 1~2% → 一般 | >2% → 差")
print(f"  TD 接近 -费率 → 正常 | 偏正 → ETF表现超越指数（可能有额外收益）| 偏负过多 → 跟踪失效")
```

---

## Step 6 — ETF持仓数据

```python
# date 参数格式：年份字符串，如 "2024"（取最新一期报告）
try:
    holdings = ak.fund_portfolio_hold_em(symbol=symbol, date="2024")
    print("=== ETF持仓（前10大，最新季报）===")
    if not holdings.empty:
        print(holdings.head(10).to_string(index=False))
        top10_weight = holdings["占净值比例"].head(10).apply(
            lambda x: float(str(x).replace("%", ""))
        ).sum()
        print(f"\n前10大持仓合计权重：{top10_weight:.2f}%")
    else:
        print("[数据缺失：持仓数据为空]")
except Exception as e:
    print(f"[数据缺失：持仓数据，原因：{e}]")
```

---

## Step 7 — 标的指数估值历史（仅股票型ETF）

> 债券/商品/跨境ETF跳过此步，记为 `[不适用：非股票型ETF]`。
> AKShare 指数估值相关接口（`index_value_hist_funddb` 等）可用性未经验证，不作为主路径。
> 本步骤提供计算框架，数据须从官方/可验证来源手动获取后填入。

```python
# index_name / index_code 参照：沪深300(000300)、中证500(000905)、创业板指(399006) 等
index_name = "沪深300"    # ← 替换为ETF追踪的指数名称
index_code = "000300"     # ← 替换为对应指数代码

# ── 自动尝试（不保证成功）────────────────────────────────────────────
pe_hist = None
pb_hist = None

for fn_name in ["index_value_hist_funddb", "stock_index_pe_lg"]:
    try:
        fn = getattr(ak, fn_name, None)
        if fn is None:
            continue
        if fn_name == "index_value_hist_funddb":
            pe_hist = fn(symbol=index_name, indicator="市盈率")
            pb_hist = fn(symbol=index_name, indicator="市净率")
        else:
            df = fn()
            name_col = next((c for c in df.columns if "名称" in c), None)
            if name_col:
                df = df[df[name_col] == index_name]
            pe_col = next((c for c in df.columns if "PE" in c.upper()), None)
            pb_col = next((c for c in df.columns if "PB" in c.upper()), None)
            date_col = next((c for c in df.columns if "日期" in c or "date" in c.lower()), None)
            if date_col and pe_col:
                pe_hist = df[[date_col, pe_col]].rename(columns={date_col: "date", pe_col: "pe"})
            if date_col and pb_col:
                pb_hist = df[[date_col, pb_col]].rename(columns={date_col: "date", pb_col: "pb"})
        if pe_hist is not None and not pe_hist.empty:
            print(f"[接口 {fn_name} 调用成功]")
            break
    except Exception as e:
        print(f"[接口 {fn_name} 不可用：{e}]")

# ── 数据降级提示 ─────────────────────────────────────────────────────
if pe_hist is None or (hasattr(pe_hist, 'empty') and pe_hist.empty):
    print(f"[数据缺失：{index_name} PE历史，原因：AKShare估值接口不可用]")
    print(f"  → 手动查询：https://www.csindex.com.cn/zh-CN/indices/index-detail/{index_code} → 估值数据")
    print(f"  → 备用来源：https://www.lixinger.com （免费历史PE/PB分位）")
else:
    # ── 自动计算分位 ──────────────────────────────────────────────────
    if not isinstance(pe_hist.columns.tolist()[0], str) or "date" not in pe_hist.columns:
        pe_hist.columns = ["date", "pe"]
    pe_hist["pe"] = pd.to_numeric(pe_hist["pe"], errors="coerce")
    pe_hist["date"] = pd.to_datetime(pe_hist["date"])
    current_pe = pe_hist["pe"].iloc[-1]
    pe_pct_3y = (pe_hist[pe_hist["date"] >= pe_hist["date"].max() - pd.DateOffset(years=3)]["pe"] <= current_pe).mean() * 100
    pe_pct_5y = (pe_hist[pe_hist["date"] >= pe_hist["date"].max() - pd.DateOffset(years=5)]["pe"] <= current_pe).mean() * 100
    print(f"=== {index_name} PE估值 ===")
    print(f"当前PE：{current_pe:.2f}  |  3年分位：{pe_pct_3y:.1f}%  |  5年分位：{pe_pct_5y:.1f}%")

if pb_hist is None or (hasattr(pb_hist, 'empty') and pb_hist.empty):
    print(f"[数据缺失：{index_name} PB历史，原因：AKShare估值接口不可用]")
else:
    if "date" not in pb_hist.columns:
        pb_hist.columns = ["date", "pb"]
    pb_hist["pb"] = pd.to_numeric(pb_hist["pb"], errors="coerce")
    pb_hist["date"] = pd.to_datetime(pb_hist["date"])
    current_pb = pb_hist["pb"].iloc[-1]
    pb_pct_3y = (pb_hist[pb_hist["date"] >= pb_hist["date"].max() - pd.DateOffset(years=3)]["pb"] <= current_pb).mean() * 100
    pb_pct_5y = (pb_hist[pb_hist["date"] >= pb_hist["date"].max() - pd.DateOffset(years=5)]["pb"] <= current_pb).mean() * 100
    print(f"当前PB：{current_pb:.2f}  |  3年分位：{pb_pct_3y:.1f}%  |  5年分位：{pb_pct_5y:.1f}%")
```

---

## Step 8 — ETF规模趋势（月度净值规模）

```python
# 直接从 Step 3 已采集的 etf_df 聚合月度数据（无需额外网络请求）
if etf_df is not None and not etf_df.empty:
    etf_df["ym"] = etf_df["date"].dt.to_period("M")
    etf_monthly = etf_df.groupby("ym").agg(
        close=("close", "last"),
        amount=("amount", "sum"),
        turnover=("turnover", "mean"),
    ).reset_index()
    etf_monthly = etf_monthly[etf_monthly["ym"] >= (etf_df["ym"].max() - 12)]
    print("=== ETF月度行情（末13个月，含成交额反映流动性趋势）===")
    print(etf_monthly[["ym", "close", "amount", "turnover"]].to_string(index=False))
    avg_monthly_amount = etf_monthly["amount"].mean()
    print(f"\n月均成交额：{avg_monthly_amount / 1e8:.1f} 亿元")
else:
    print("[数据缺失：ETF月度规模数据，原因：Step 3 历史行情未成功获取]")
```

---

## Step 9 — 实时价格（Sina hq，直连无代理）

```python
import urllib.request

url = f"https://hq.sinajs.cn/list={market}{symbol}"
req = urllib.request.Request(
    url,
    headers={"Referer": "https://finance.sina.com.cn", "User-Agent": "Mozilla/5.0"}
)
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
try:
    with opener.open(req, timeout=10) as r:
        raw = r.read().decode("gbk")
    fields = raw.split('"')[1].split(",")
    etf_name      = fields[0]
    current_price = float(fields[3])
    prev_close    = float(fields[2])
    today_high    = float(fields[4])
    today_low     = float(fields[5])
    volume        = int(fields[8])
    turnover      = float(fields[9])
    print(f"=== {etf_name}（{symbol}）实时行情 ===")
    print(f"当前价格：{current_price} 元  |  昨收：{prev_close} 元")
    print(f"今日高/低：{today_high} / {today_low}")
    print(f"成交量：{volume} 手  |  成交额：{turnover/1e8:.2f} 亿元")
except Exception as e:
    print(f"[数据缺失：实时价格，原因：{e}]")
```

---

## Step 10 — 数据异常检测（采集后必须执行）

```python
# 1. 跟踪误差异常（股票型ETF）
if 'tracking_error_annual' in dir():
    if tracking_error_annual > 2.0:
        print(f"⚠️ [WARN] 年化跟踪误差={tracking_error_annual:.4f}%，显著偏高（>2%），疑似跟踪失效或流动性问题")
    if abs(tracking_diff_1y) > 1.5:
        print(f"⚠️ [WARN] 近1年跟踪偏差={tracking_diff_1y:.4f}%，偏离过大（>1.5%），需核查费率与复制方式")

# 2. 规模警告
if 'etf_info' in dir() and not etf_info.empty:
    try:
        vol_col = [c for c in etf_info.columns if "成交" in c and "额" in c]
        if vol_col:
            daily_vol = float(str(etf_info[vol_col[0]].iloc[0]).replace(",", ""))
            if daily_vol < 1e7:  # 日成交额低于1000万
                print(f"⚠️ [WARN] 日成交额={daily_vol/1e4:.0f}万元，流动性极差（<1000万），需警惕买卖价差损耗")
            elif daily_vol < 5e7:
                print(f"⚠️ [WARN] 日成交额={daily_vol/1e4:.0f}万元，流动性偏低（<5000万），谨慎大额操作")
    except:
        pass

# 3. PE/PB 极值警告（股票型ETF）
if 'current_pe' in dir():
    if pe_pct_5y > 90:
        print(f"⚠️ [WARN] PE处于5年 {pe_pct_5y:.1f}% 分位，历史高估区间，须谨慎")
    if pe_pct_5y < 10:
        print(f"[信号] PE处于5年 {pe_pct_5y:.1f}% 分位，历史低估区间")

# 4. 数据时效检查
if 'etf_hist' in dir() and not etf_hist.empty:
    from datetime import date
    latest = pd.to_datetime(etf_hist["日期"]).max().date()
    lag = (date.today() - latest).days
    if lag > 7:
        print(f"⚠️ [WARN] ETF行情数据最新日期为 {latest}，距今 {lag} 天，可能不是最新数据")
```

---

## Step 11 — 降级策略（API 彻底失败时）

| 数据项 | 降级路径 |
|--------|----------|
| ETF基本信息/规模 | 天天基金 https://fund.eastmoney.com/[代码].html |
| 持仓数据 | 基金季报 → 天天基金 → 持仓 |
| 指数估值历史 | 中证指数官网 https://www.csindex.com.cn → 指数详情 → 估值数据 |
| 指数PE/PB分位 | 万得资讯（需账号）/ 理杏仁（免费历史分位）https://www.lixinger.com |
| 跟踪误差 | 晨星/好买/天天基金 ETF详情页 → 风险指标 |
| ETF规模变化 | 基金季报净值规模变化 |
| 债券ETF久期/信用利差 | 中债信息网 https://www.chinabond.com.cn |

---

## Step 12 — 采集完成确认清单

逐项核对后方可进入 M1-M6 分析模块：

```
[ ] ETF类型确认：股票型（宽基/行业/主题）/ 债券型 / 商品型 / 跨境型
[ ] ETF名称、基金公司、成立日期、管理费率、托管费率
[ ] 当前实时价格（Sina hq）
[ ] 历史日行情：近5年（含不复权 + 前复权）
[ ] 追踪指数确认（名称 + 代码）
[ ] 年化跟踪误差（TE）（股票型必须）
[ ] 近1年跟踪偏差（TD）（股票型必须）
[ ] 持仓数据：前10大持仓 + 占比（最新季报）
[ ] 指数当前PE/PB + 3年/5年历史分位（股票型必须）
[ ] 月度成交额趋势（近12个月）
[ ] 数据异常检测（Step 10）已执行并注释异常项
[ ] 债券型补充：久期 / 信用评级分布 / 到期收益率
[ ] 商品型补充：实物/期货复制方式 / 展期成本
[ ] 跨境型补充：汇率风险 / 标的市场估值
```

采集完成后将结果汇总，再进入分析框架。
