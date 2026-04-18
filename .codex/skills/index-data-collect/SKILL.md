---
name: index-data-collect
description: 在网上查询A股指数基础数据，给 choose-index-now 和指数分析框架使用；当需要判断某个指数当前估值、趋势、拥挤度，或为指数/ETF配置做前置数据采集时使用
---

# index-data-collect

采集指定A股指数的估值 / 趋势 / 拥挤度基础数据，供 `choose-index-now` 或更完整的指数分析使用。

## 使用方式

```text
/index-data-collect [指数名称或代码]

示例：
/index-data-collect 沪深300（000300）
/index-data-collect 创业板指（399006）
/index-data-collect 中证500（000905）
/index-data-collect 科创50（000688）
```

输入的是**指数名称或代码**，不是 ETF 代码。

---

## Step 1 — 确认环境依赖

```bash
pip install akshare baostock requests pandas numpy
```

---

## Step 2 — 确认指数代码、BaoStock 前缀与风格标签

> 指数不像股票，不能只靠首位数字机械判断市场；优先使用常见指数映射表，未知指数必须手动确认。

```python
COMMON_INDEX_MAP = {
    "沪深300":  {"code": "000300", "bs_code": "sh.000300", "style": "broad",    "benchmark_bs_code": "sh.000300", "use_relative_pe": False},
    "上证50":   {"code": "000016", "bs_code": "sh.000016", "style": "large",    "benchmark_bs_code": "sh.000300", "use_relative_pe": False},
    "中证500":  {"code": "000905", "bs_code": "sh.000905", "style": "mid",      "benchmark_bs_code": "sh.000300", "use_relative_pe": False},
    "中证1000": {"code": "000852", "bs_code": "sh.000852", "style": "growth",   "benchmark_bs_code": "sh.000300", "use_relative_pe": True},
    "创业板指": {"code": "399006", "bs_code": "sz.399006", "style": "growth",   "benchmark_bs_code": "sh.000300", "use_relative_pe": True},
    "深证成指": {"code": "399001", "bs_code": "sz.399001", "style": "broad",    "benchmark_bs_code": "sh.000300", "use_relative_pe": False},
    "科创50":   {"code": "000688", "bs_code": "sh.000688", "style": "growth",   "benchmark_bs_code": "sh.000300", "use_relative_pe": True},
    "中证红利": {"code": "000922", "bs_code": "sh.000922", "style": "dividend", "benchmark_bs_code": "sh.000300", "use_relative_pe": False},
}

ALIASES = {
    "000300": "沪深300",
    "000016": "上证50",
    "000905": "中证500",
    "000852": "中证1000",
    "399006": "创业板指",
    "399001": "深证成指",
    "000688": "科创50",
    "000922": "中证红利",
}

target = "399006"  # ← 替换为目标指数名称或代码
key = ALIASES.get(target, target)
meta = COMMON_INDEX_MAP.get(key)

if meta is None:
    raise ValueError("未收录的指数，请先到中证指数官网/交易所官网确认 code 与 bs_code，再继续")

index_name         = key
index_code         = meta["code"]
index_bs_code      = meta["bs_code"]
index_style        = meta["style"]
benchmark_bs_code  = meta["benchmark_bs_code"]
use_relative_pe    = meta["use_relative_pe"]

print(index_name, index_code, index_bs_code, index_style)
```

> 若指数不在映射表内，先去中证指数官网确认名称、代码、指数编制机构，再手动填入 `index_code` 和 `index_bs_code`。  
> 成长风格指数（创业板指、科创50、中证1000）后续必须额外采集相对沪深300 的估值比值。

---

## Step 3 — 指数历史行情（BaoStock 主路径）

> 目标是一次拿到趋势计算需要的 `close / high / low`，并尽量补足 `amount / turn` 供拥挤度使用。  
> 若 `turn` 不可用，后续拥挤度改用成交额比值代理，并明确标注。

```python
import sys
sys.stdout.reconfigure(encoding="utf-8")

import baostock as bs
import pandas as pd

fields_candidates = [
    "date,open,high,low,close,pctChg,volume,amount,turn",
    "date,open,high,low,close,pctChg,volume,amount",
    "date,open,high,low,close,pctChg",
]

hist_df = pd.DataFrame()
bs.login()
for fields in fields_candidates:
    rs = bs.query_history_k_data_plus(
        index_bs_code,
        fields,
        start_date="2020-01-01",
        end_date="2026-12-31",
        frequency="d",
    )
    rows = []
    while rs.error_code == "0" and rs.next():
        rows.append(rs.get_row_data())
    if rows:
        hist_df = pd.DataFrame(rows, columns=fields.split(","))
        break
bs.logout()

if hist_df.empty:
    print("[数据缺失：指数历史行情，原因：BaoStock 未返回数据]")
else:
    for col in [c for c in ["open", "high", "low", "close", "pctChg", "volume", "amount", "turn"] if c in hist_df.columns]:
        hist_df[col] = pd.to_numeric(hist_df[col], errors="coerce")
    hist_df["date"] = pd.to_datetime(hist_df["date"])
    hist_df = hist_df.sort_values("date").reset_index(drop=True)

    print("=== 指数历史行情（末5行）===")
    show_cols = [c for c in ["date", "close", "pctChg", "amount", "turn"] if c in hist_df.columns]
    print(hist_df[show_cols].tail(5).to_string(index=False))
    print(f"\n总样本数：{len(hist_df)}")

    close_60d = hist_df[["date", "close"]].tail(60).copy() if len(hist_df) >= 60 else pd.DataFrame()
    high_low_18d = hist_df[["date", "high", "low"]].tail(18).copy() if len(hist_df) >= 18 else pd.DataFrame()
```

---

## Step 4 — 指数估值历史（PE / PB）与 5 年分位

> `choose-index-now` 的估值判断只需要当前值和 5 年分位。  
> 自动接口可尝试，但主结论必须优先信任中证指数官网、理杏仁等可验证来源；拿不到就标注 `[数据缺失：原因]`。

```python
import akshare as ak
import pandas as pd

pe_hist = None
pb_hist = None

try:
    pe_hist = ak.index_value_hist_funddb(symbol=index_name, indicator="市盈率")
    pb_hist = ak.index_value_hist_funddb(symbol=index_name, indicator="市净率")
except Exception as e:
    print(f"[数据缺失：AKShare 指数估值接口，原因：{e}]")

def normalize_val_df(df, value_name):
    if df is None or getattr(df, "empty", True):
        return None
    df = df.copy()
    cols = list(df.columns)
    date_col = next((c for c in cols if "date" in c.lower() or "日期" in c), None)
    value_col = next((c for c in cols if value_name in c or c.lower() in {"pe", "pb"}), None)
    if not date_col or not value_col:
        return None
    out = df[[date_col, value_col]].rename(columns={date_col: "date", value_col: value_name})
    out["date"] = pd.to_datetime(out["date"])
    out[value_name] = pd.to_numeric(out[value_name], errors="coerce")
    return out.dropna().sort_values("date").reset_index(drop=True)

pe_hist = normalize_val_df(pe_hist, "pe")
pb_hist = normalize_val_df(pb_hist, "pb")

def calc_5y_percentile(df, col):
    if df is None or df.empty:
        return None, None
    latest_date = df["date"].max()
    five_year_df = df[df["date"] >= latest_date - pd.DateOffset(years=5)].copy()
    if five_year_df.empty:
        return None, None
    current_value = five_year_df[col].iloc[-1]
    percentile = (five_year_df[col] <= current_value).mean() * 100
    return current_value, percentile

current_pe, pe_pct_5y = calc_5y_percentile(pe_hist, "pe")
current_pb, pb_pct_5y = calc_5y_percentile(pb_hist, "pb")

print(f"当前PE：{current_pe:.2f}  |  5年分位：{pe_pct_5y:.1f}%" if current_pe is not None else "PE：[数据缺失]")
print(f"当前PB：{current_pb:.2f}  |  5年分位：{pb_pct_5y:.1f}%" if current_pb is not None else "PB：[数据缺失]")
```

若自动接口不可用，按下面路径手动补：

| 数据项 | 优先来源 | 备用来源 |
|--------|----------|----------|
| PE / PB 当前值 | 中证指数官网指数详情页 | 理杏仁 |
| PE / PB 历史分位 | 理杏仁 | 手动导出历史序列后计算 |

---

## Step 5 — 10 年期国债收益率与股债比价

> 股债比价使用中国 10 年期国债收益率。  
> 该数据时效性强，必须记录**具体日期**。

```python
bond_10y = None

try:
    bond_df = ak.bond_zh_us_rate()
    date_col = next((c for c in bond_df.columns if "日期" in c or "date" in c.lower()), None)
    yield_col = next((c for c in bond_df.columns if "中国国债收益率10年" in c or "中国10年期国债收益率" in c), None)
    if date_col and yield_col:
        bond_df = bond_df[[date_col, yield_col]].dropna().copy()
        bond_df.columns = ["date", "yield_10y"]
        bond_df["date"] = pd.to_datetime(bond_df["date"])
        bond_df["yield_10y"] = pd.to_numeric(bond_df["yield_10y"], errors="coerce")
        latest_bond = bond_df.dropna().sort_values("date").iloc[-1]
        bond_10y = float(latest_bond["yield_10y"])
        print(f"10Y国债收益率：{bond_10y:.2f}%  |  日期：{latest_bond['date'].date()}")
except Exception as e:
    print(f"[数据缺失：10Y国债收益率，原因：{e}]")

earnings_yield = (1 / current_pe * 100) if current_pe not in (None, 0) else None
if earnings_yield is not None and bond_10y is not None:
    print(f"盈利收益率：{earnings_yield:.2f}%")
    print(f"股债比价倍数：{earnings_yield / bond_10y:.2f}x")
```

> 若 AKShare 失败，手动查询：中债收益率曲线官网 / 中国债券信息网。  
> 记录格式统一为：`10Y国债收益率：X.XX%，日期：YYYY-MM-DD`。

---

## Step 6 — 趋势数据（RSRS / MA20 / 近30日涨幅）

> 这一部分完全由 Step 3 的行情序列推导。  
> 若历史长度不足，不得缩短窗口硬算，直接标注 `[数据缺失：历史长度不足]`。

```python
import numpy as np

rsrs_score = None
ma20_now = None
ma20_prev5 = None
ret_30d = None

if len(hist_df) >= 31:
    ret_30d = (hist_df["close"].iloc[-1] / hist_df["close"].iloc[-31] - 1) * 100
    print(f"近30日涨幅：{ret_30d:.2f}%")
else:
    print("[数据缺失：近30日涨幅，原因：历史长度不足]")

if len(hist_df) >= 25:
    hist_df["ma20"] = hist_df["close"].rolling(20).mean()
    ma20_now = float(hist_df["ma20"].iloc[-1])
    ma20_prev5 = float(hist_df["ma20"].iloc[-6])
    print(f"MA20当前值：{ma20_now:.2f}  |  5日前：{ma20_prev5:.2f}")
else:
    print("[数据缺失：MA20方向，原因：历史长度不足]")

if len(hist_df) >= 618:
    win = 18
    beta_list = []
    r2_list = []
    dt_list = []

    for i in range(win - 1, len(hist_df)):
        sample = hist_df.iloc[i - win + 1:i + 1]
        x = sample["low"].to_numpy(dtype=float)
        y = sample["high"].to_numpy(dtype=float)
        beta, alpha = np.polyfit(x, y, 1)
        y_hat = beta * x + alpha
        ss_res = ((y - y_hat) ** 2).sum()
        ss_tot = ((y - y.mean()) ** 2).sum()
        r2 = 1 - ss_res / ss_tot if ss_tot != 0 else np.nan
        beta_list.append(beta)
        r2_list.append(r2)
        dt_list.append(sample["date"].iloc[-1])

    rsrs_df = pd.DataFrame({"date": dt_list, "beta": beta_list, "r2": r2_list})
    rsrs_df["beta_mean_600"] = rsrs_df["beta"].rolling(600).mean()
    rsrs_df["beta_std_600"] = rsrs_df["beta"].rolling(600).std()
    rsrs_df["z_score"] = (rsrs_df["beta"] - rsrs_df["beta_mean_600"]) / rsrs_df["beta_std_600"]
    rsrs_df["rsrs_score"] = rsrs_df["z_score"] * rsrs_df["r2"]
    rsrs_score = float(rsrs_df["rsrs_score"].iloc[-1])
    print(f"RSRS得分：{rsrs_score:.3f}")
else:
    print("[数据缺失：RSRS得分，原因：需至少618个交易日历史数据]")
```

---

## Step 7 — 成长风格指数专项数据（相对沪深300 估值比）

> 创业板指、科创50、中证1000 等成长型指数，不能只看绝对 PE。  
> 必须额外采集相对沪深300 的估值溢价，并计算 5 年分位。

```python
relative_pe_ratio = None
relative_pe_pct_5y = None

if use_relative_pe and pe_hist is not None and not pe_hist.empty:
    # 参照 Step 4 的同样方法，再采一次 benchmark_bs_code 对应指数的 PE 历史
    # 这里默认以沪深300 为基准
    benchmark_name = "沪深300"
    benchmark_pe_hist = None
    try:
        benchmark_pe_hist = ak.index_value_hist_funddb(symbol=benchmark_name, indicator="市盈率")
        benchmark_pe_hist = normalize_val_df(benchmark_pe_hist, "pe")
    except Exception as e:
        print(f"[数据缺失：基准指数PE历史，原因：{e}]")

    if benchmark_pe_hist is not None and not benchmark_pe_hist.empty:
        rel_df = pd.merge(
            pe_hist[["date", "pe"]],
            benchmark_pe_hist[["date", "pe"]],
            on="date",
            how="inner",
            suffixes=("_index", "_benchmark"),
        )
        rel_df["ratio"] = rel_df["pe_index"] / rel_df["pe_benchmark"]
        rel_df = rel_df.dropna().sort_values("date").reset_index(drop=True)
        if not rel_df.empty:
            latest_date = rel_df["date"].max()
            rel_5y = rel_df[rel_df["date"] >= latest_date - pd.DateOffset(years=5)].copy()
            relative_pe_ratio = float(rel_5y["ratio"].iloc[-1])
            relative_pe_pct_5y = (rel_5y["ratio"] <= relative_pe_ratio).mean() * 100
            print(f"成长相对估值比：{relative_pe_ratio:.2f}  |  5年分位：{relative_pe_pct_5y:.1f}%")
```

判读参考直接沿用 `choose-index-now`：

```text
成长指数相对沪深300 的 PE 比值 5年分位 < 30%  → 相对价值有吸引力
成长指数相对沪深300 的 PE 比值 5年分位 > 70%  → 成长溢价偏高，谨慎
```

---

## Step 8 — 拥挤度数据（5日 vs 90日活跃度 + 北向资金）

> 拥挤度优先使用指数自身换手率；若指数没有换手率，退化为成交额比值代理。  
> 北向资金是**市场风险偏好代理**，不是指数精确归因，拿不到可以标注缺失。

```python
crowding_metric = None
crowding_ratio = None
north_10d = None

if "turn" in hist_df.columns and hist_df["turn"].notna().sum() >= 90:
    turn_5 = hist_df["turn"].tail(5).mean()
    turn_90 = hist_df["turn"].tail(90).mean()
    crowding_metric = "换手率"
    crowding_ratio = turn_5 / turn_90 if turn_90 else None
elif "amount" in hist_df.columns and hist_df["amount"].notna().sum() >= 90:
    amt_5 = hist_df["amount"].tail(5).mean()
    amt_90 = hist_df["amount"].tail(90).mean()
    crowding_metric = "成交额代理"
    crowding_ratio = amt_5 / amt_90 if amt_90 else None
else:
    print("[数据缺失：拥挤度活跃度指标，原因：指数历史换手率/成交额不足]")

if crowding_ratio is not None:
    print(f"拥挤度活跃度：{crowding_metric} 5日/90日 = {crowding_ratio:.2f}x")

try:
    north_df = ak.stock_hsgt_north_net_flow_in_em()
    date_col = next((c for c in north_df.columns if "日期" in c or "date" in c.lower()), None)
    value_col = next((c for c in north_df.columns if "净流入" in c), None)
    if date_col and value_col:
        north_df = north_df[[date_col, value_col]].copy()
        north_df.columns = ["date", "net_inflow"]
        north_df["date"] = pd.to_datetime(north_df["date"])
        north_df["net_inflow"] = pd.to_numeric(north_df["net_inflow"], errors="coerce")
        north_10d = float(north_df.sort_values("date").tail(10)["net_inflow"].sum())
        print(f"北向资金近10日净流入：{north_10d:.2f} 亿元")
except Exception as e:
    print(f"[数据缺失：北向资金近10日净流入，原因：{e}]")
```

活跃度判读可直接映射为：

```text
> 2.0x  → ⚠️ 偏热
1.0x ~ 2.0x → 🟡 正常偏热
0.5x ~ 1.0x → 🟢 正常
< 0.5x → 🟢🟢 冷清
```

---

## Step 9 — 数据异常检测（采集后必须执行）

```python
from datetime import date

if not hist_df.empty:
    latest_hist_date = pd.to_datetime(hist_df["date"]).max().date()
    hist_lag = (date.today() - latest_hist_date).days
    if hist_lag > 7:
        print(f"⚠️ [WARN] 行情数据最新日期为 {latest_hist_date}，距今 {hist_lag} 天")

if len(hist_df) < 60:
    print(f"⚠️ [WARN] 仅有 {len(hist_df)} 个交易日，无法完整覆盖近60日收盘价序列")

if len(hist_df) < 618:
    print(f"⚠️ [WARN] 仅有 {len(hist_df)} 个交易日，RSRS 将标记为数据缺失")

if current_pe is not None and current_pe <= 0:
    print("⚠️ [WARN] 当前PE<=0，盈利收益率不具可比性，估值判断需弱化")

if pe_pct_5y is not None and pe_pct_5y > 90:
    print(f"⚠️ [WARN] PE 处于 5年 {pe_pct_5y:.1f}% 分位，处于历史高位区间")

if pb_pct_5y is not None and pb_pct_5y > 90:
    print(f"⚠️ [WARN] PB 处于 5年 {pb_pct_5y:.1f}% 分位，处于历史高位区间")

if crowding_ratio is not None and crowding_ratio > 2.0:
    print(f"⚠️ [WARN] 活跃度比值 {crowding_ratio:.2f}x，短期可能过热")

if crowding_metric == "成交额代理":
    print("[注] 当前拥挤度使用成交额代理，不是指数真实换手率")

if north_10d is None:
    print("[注] 北向资金近10日净流入缺失，该项允许跳过，但必须显式标注")
```

---

## Step 10 — 降级策略（API 不稳定或无数据时）

| 数据项 | 降级路径 |
|--------|----------|
| 指数代码 / 编制机构 | 中证指数官网 https://www.csindex.com.cn |
| 指数历史行情 | 新浪指数行情 / 东方财富指数页 / 交易所官网 |
| PE / PB 当前值与历史分位 | 中证指数官网 / 理杏仁 https://www.lixinger.com |
| 10年期国债收益率 | 中债收益率曲线 / 中国债券信息网 |
| 北向资金近10日净流入 | 东方财富北向资金页 / 新浪北向资金页 |
| 指数换手率 | 东方财富指数页；若仍缺失，改用代表性 ETF 的成交额或换手率代理，并标注 `[代理指标]` |
| 成长指数相对估值 | 目标指数 PE 历史 + 沪深300 PE 历史，手动导出后计算 |

所有无法获取的数据项必须以 `[数据缺失：原因]` 标注，不得跳过或估算。

---

## Step 11 — 采集完成确认清单

逐项核对后方可进入 `choose-index-now` 或其他指数分析模块：

```text
[ ] 指数名称、代码、BaoStock 代码已确认
[ ] 指数风格标签已确认：broad / large / mid / growth / dividend / theme
[ ] 当前 PE + 5年分位
[ ] 当前 PB + 5年分位
[ ] 10Y 国债收益率（含具体日期）
[ ] 盈利收益率与股债比价
[ ] 近60日收盘价序列
[ ] 近18日最高价 / 最低价序列
[ ] RSRS 得分（或明确标注历史长度不足）
[ ] MA20 当前值 vs 5日前值
[ ] 近30日涨幅
[ ] 5日 / 90日 活跃度比值（换手率优先，成交额代理次之）
[ ] 北向资金近10日净流入（或明确标注缺失）
[ ] 成长指数专项：相对沪深300 PE 比值 + 5年分位
[ ] 数据异常检测（Step 9）已执行并注释异常项
```

建议将最终采集结果整理为以下字段，直接交给 `choose-index-now`：

```python
payload = {
    "index_name": index_name,
    "index_code": index_code,
    "current_pe": current_pe,
    "pe_pct_5y": pe_pct_5y,
    "current_pb": current_pb,
    "pb_pct_5y": pb_pct_5y,
    "bond_10y": bond_10y,
    "earnings_yield": earnings_yield,
    "rsrs_score": rsrs_score,
    "ma20_now": ma20_now,
    "ma20_prev5": ma20_prev5,
    "ret_30d": ret_30d,
    "crowding_metric": crowding_metric,
    "crowding_ratio": crowding_ratio,
    "north_10d": north_10d,
    "relative_pe_ratio": relative_pe_ratio,
    "relative_pe_pct_5y": relative_pe_pct_5y,
}
```

采集完成后，再进入估值 / 趋势 / 拥挤度的三维判断。
