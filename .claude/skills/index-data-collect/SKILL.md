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

## Step 1a — 系统代理处理（macOS 全局代理 / Clash 环境下必须）

> 部分环境下 macOS 将系统代理设为 `127.0.0.1:7897`（ClashX / Surge / V2rayU 等），导致 `push2his.eastmoney.com`、`push2.eastmoney.com`、`88.push2.eastmoney.com` 等 API 域名被拦截，表现为 `ProxyError: Unable to connect to proxy` 或 `RemoteDisconnected`。  
> 以下两种方案任选其一，用于所有需要访问易被拦截域名的场景。

**方案 A：requests Session + trust_env=False（推荐）**
```python
import requests
session = requests.Session()
session.trust_env = False  # 绕过 macOS 系统代理
resp = session.get(url, timeout=15)  # 而非 requests.get()
```

**方案 B：urllib + ProxyHandler({})**
```python
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open(url, timeout=15) as resp:
    ...
```

> `bond_zh_us_rate()` 等少数函数访问的是不受拦截的域名，不需要此处理。

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

# ── 动态发现：若不在映射表中，通过 AKShare 搜索 ──
if meta is None:
    try:
        import akshare as ak
        all_idx = ak.index_csindex_all()
        search_cols = ["指数简称", "指数代码", "指数全称"]
        for col in all_idx.columns:
            if col not in search_cols:
                continue
            mask = all_idx[col].astype(str).str.contains(target, na=False)
            if mask.any():
                row = all_idx[mask].iloc[0]
                index_name  = str(row.get("指数简称", row.get("指数全称", target)))
                index_code  = str(row["指数代码"])
                index_style = "theme"
                print(f"[动态发现] {index_name}({index_code})，默认风格标签=theme")
                meta = {"code": index_code, "style": index_style, "use_relative_pe": False,
                        "index_name": index_name}
                break
    except Exception as e:
        print(f"[动态发现失败] {e}")

if meta is None:
    raise ValueError("未收录的指数，请先到中证指数官网/交易所官网确认代码，再继续")

index_name         = meta.get("index_name", key) if meta.get("index_name") else key
index_code         = meta["code"]
index_bs_code      = meta.get("bs_code")  # 可能为 None
index_style        = meta["style"]
benchmark_bs_code  = meta.get("benchmark_bs_code", "sh.000300")
use_relative_pe    = meta.get("use_relative_pe", False)

print(index_name, index_code, index_bs_code or "BaoStock代码未知", index_style)
```

> 若动态发现也未命中，先去中证指数官网确认名称、代码，再手动填入。  
> **注意**：中证 93xxxx / 932xxx 系列主题指数通常不在 BaoStock 收录范围内，Step 3 会降级走腾讯行情代理。  
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
if index_bs_code is None:
    print(f"[跳过] BaoStock 无 {index_code} 代码，直接尝试腾讯行情...")
else:
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
if index_bs_code is not None:
    bs.logout()

if hist_df.empty:
    print("[降级] BaoStock 未收录该指数，尝试腾讯行情代理...")
    try:
        import requests as _req, json as _json
        _session = _req.Session()
        _session.trust_env = False  # 绕过系统代理
        _tx_url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sz{index_code},day,,,1000,qfq"
        _resp = _session.get(_tx_url, timeout=15)
        _tx_data = _resp.json()
        _klines = _tx_data.get("data", {}).get(f"sz{index_code}", {}).get("day", [])
        if not _klines:
            # 若指数代码查不到，尝试用 ETF 代理标识（需调用者传入 etf_proxy_code）
            _etf_proxy = globals().get("etf_proxy_code")
            if _etf_proxy:
                _tx_url2 = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sz{_etf_proxy},day,,,1000,qfq"
                _resp2 = _session.get(_tx_url2, timeout=15)
                _tx_data2 = _resp2.json()
                _klines = _tx_data2.get("data", {}).get(f"sz{_etf_proxy}", {}).get("day", [])
                if _klines:
                    print(f"[代理] 使用 ETF {_etf_proxy} 行情代理指数 {index_code}")
        if _klines:
            _rows = []
            for _k in _klines:
                # 腾讯格式: [date, open, close, high, low, volume]
                _rows.append({"date": _k[0], "open": float(_k[1]), "close": float(_k[2]),
                              "high": float(_k[3]), "low": float(_k[4]), "volume": float(_k[5])})
            hist_df = pd.DataFrame(_rows)
            hist_df["date"] = pd.to_datetime(hist_df["date"])
            hist_df = hist_df.sort_values("date").reset_index(drop=True)
            print(f"[腾讯行情] {len(hist_df)} 个交易日，{hist_df['date'].min().date()} → {hist_df['date'].max().date()}")
            print(f"[注] 腾讯行情缺少 turn（换手率）字段，后续拥挤度改用成交额代理")
    except Exception as _e:
        print(f"[腾讯行情失败] {_e}")

if hist_df.empty:
    print("[数据缺失：指数历史行情，BaoStock + 腾讯均未返回数据]")
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

## Step 4 — 指数估值（PE 与 5 年分位）

> `choose-index-now` 的估值判断只需要当前值和 5 年分位。  
> 当前 AKShare 版本使用 `stock_zh_index_value_csindex` 获取指数估值，**仅返回 PE（市盈率）和股息率，不再返回 PB（市净率）**。

```python
import akshare as ak
import pandas as pd

pe_hist = None
current_pe = None
pe_pct_5y = None

try:
    # stock_zh_index_value_csindex 返回 {日期, 指数代码, …, 市盈率1, 市盈率2, 股息率1, 股息率2}
    val_df = ak.stock_zh_index_value_csindex(symbol=index_code)
    val_df["日期"] = pd.to_datetime(val_df["日期"])
    val_df["市盈率1"] = pd.to_numeric(val_df["市盈率1"], errors="coerce")
    val_df = val_df[["日期", "市盈率1"]].dropna().sort_values("日期").reset_index(drop=True)
    val_df.columns = ["date", "pe"]
    pe_hist = val_df

    # 5年分位
    latest_date = pe_hist["date"].max()
    five_year_df = pe_hist[pe_hist["date"] >= latest_date - pd.DateOffset(years=5)].copy()
    current_pe = float(five_year_df["pe"].iloc[-1])
    pe_pct_5y = (five_year_df["pe"] <= current_pe).mean() * 100
    pe_5y_min = five_year_df["pe"].min()
    pe_5y_max = five_year_df["pe"].max()

    print(f"当前PE：{current_pe:.2f}  |  5年分位：{pe_pct_5y:.1f}%")
    print(f"PE 5年区间：[{pe_5y_min:.2f}, {pe_5y_max:.2f}]")
except Exception as e:
    print(f"[数据缺失：指数 PE 数据，原因：{e}]")

# PB 不再可用
current_pb = None
pb_pct_5y = None
print("PB：[数据缺失：stock_zh_index_value_csindex 不再返回 PB 数据]")
```

若自动接口不可用，按下面路径手动补：

| 数据项 | 优先来源 | 备用来源 |
|--------|----------|----------|
| PE 当前值与 5 年分位 | 中证指数官网 → 指数估值页 | 理杏仁 |
| PB（市净率） | 理杏仁（中证指数官网已不提供） | 无，需手动从年报导出 |

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
    # 参照 Step 4 的同样方法，再采一次沪深300 的 PE 历史
    benchmark_code = "000300"
    benchmark_pe_df = None
    try:
        _bm_raw = ak.stock_zh_index_value_csindex(symbol=benchmark_code)
        _bm_raw["日期"] = pd.to_datetime(_bm_raw["日期"])
        _bm_raw["市盈率1"] = pd.to_numeric(_bm_raw["市盈率1"], errors="coerce")
        benchmark_pe_df = _bm_raw[["日期", "市盈率1"]].dropna().sort_values("日期").reset_index(drop=True)
        benchmark_pe_df.columns = ["date", "pe"]
    except Exception as e:
        print(f"[数据缺失：沪深300 PE历史，原因：{e}]")

    if benchmark_pe_df is not None and not benchmark_pe_df.empty:
        rel_df = pd.merge(
            pe_hist[["date", "pe"]],
            benchmark_pe_df[["date", "pe"]],
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
    # stock_hsgt_fund_flow_summary_em 返回沪股通/深股通南北向逐日数据
    north_flow = ak.stock_hsgt_fund_flow_summary_em()
    north_flow["交易日"] = pd.to_datetime(north_flow["交易日"])
    north_flow["成交净买额"] = pd.to_numeric(north_flow["成交净买额"], errors="coerce")
    # 只取北向（沪股通 + 深股通）
    north_buy = north_flow[north_flow["板块"].isin(["沪股通", "深股通"])].copy()
    if not north_buy.empty:
        daily_net = north_buy.groupby("交易日")["成交净买额"].sum()
        north_10d = float(daily_net.tail(10).sum())
        print(f"北向资金近10日净流入（沪股通+深股通合计）：{north_10d:.2f} 亿元")
    else:
        print("[注] 北向资金数据暂无当日数据，可能未开市或数据为0")
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
| 指数历史行情（BaoStock 未收录） | 腾讯行情 API（web.ifzq.gtimg.cn）/ 代表性 ETF 行情代理 |
| PE 当前值与 5 年分位 | `ak.stock_zh_index_value_csindex` → 中证指数官网 → 理杏仁 |
| PB | `stock_zh_index_value_csindex` 已不返回 PB，需手动从理杏仁或年报导出 |
| 10年期国债收益率 | 中债收益率曲线 / 中国债券信息网 |
| 北向资金近10日净流入 | `ak.stock_hsgt_fund_flow_summary_em` → 东方财富北向资金页 |
| 指数换手率 | 东方财富指数页；若仍缺失，改用代表性 ETF 的成交额代理，并标注 `[代理指标]` |
| 成长指数相对估值 | `ak.stock_zh_index_value_csindex`（目标指数 + 沪深300） → 手动计算 |

所有无法获取的数据项必须以 `[数据缺失：原因]` 标注，不得跳过或估算。

---

## Step 11 — 采集完成确认清单

逐项核对后方可进入 `choose-index-now` 或其他指数分析模块：

```text
[ ] 指数名称与代码已确认（映射表/动态发现）
[ ] 指数风格标签已确认：broad / large / mid / growth / dividend / theme
[ ] 当前 PE + 5年分位（stock_zh_index_value_csindex）
[ ] PB：[数据缺失：API不再返回]（如有需要手动从理杏仁补充）
[ ] 10Y 国债收益率（含具体日期）
[ ] 盈利收益率与股债比价
[ ] 近60日收盘价序列（BaoStock / 腾讯API）
[ ] RSRS 得分（或明确标注历史长度不足）
[ ] MA20 当前值 vs 5日前值
[ ] 近30日涨幅
[ ] 5日 / 90日 活跃度比值（换手率优先，成交额代理次之）
[ ] 北向资金近10日净流入（stock_hsgt_fund_flow_summary_em 或标注缺失）
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
    # PB: stock_zh_index_value_csindex 已不返回 PB，如有需要手动补充
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
