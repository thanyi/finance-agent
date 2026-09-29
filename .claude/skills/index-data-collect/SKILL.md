---
name: index-data-collect
description: 采集A股指数行情、估值历史、国债收益率和趋势活跃度指标，为指数估值与介入时点分析提供可追溯数据
---

# index-data-collect

输入指数名称或代码，不是ETF代码。例如 `/index-data-collect 沪深300`、`/index-data-collect 399006`。

## 执行采集

定位含 `scripts/collect_finance.py` 的仓库根目录，使用已安装 `requirements.txt` 的Python环境：

```bash
.venv/bin/python scripts/collect_finance.py index 000300
.venv/bin/python scripts/collect_finance.py index 创业板指
```

若已有具备权限的 Tushare 账户，可在环境中配置 `TUSHARE_TOKEN` 并显式启用：

```bash
.venv/bin/python scripts/collect_finance.py index 399006 --tushare
```

默认使用免费无密钥源。Tushare 的 `index_dailybasic` 有权限和指数范围限制，不自动购买服务；不能假设它覆盖全部指数。没有权限时保留失败原因。

输出在独立的 `data/index_<目标>_<时间>/`。读取 `report.json` 和所需CSV，再进入 `choose-index-now` / `check-entry-timing`。不要复制另一个标的的历史样例数值。

## 接口路由

| 项目 | 数据来源与规则 |
|---|---|
| 指数身份 | 脚本 `INDEXES` 的名称、代码、交易所、风格映射；未知指数先核对编制机构再扩展，禁止模糊匹配取第一项 |
| 完整OHLC日线 | BaoStock → 东财 `stock_zh_index_daily_em` → 新浪 `stock_zh_index_daily`；空表、过期、缺字段触发降级 |
| 滚动PE历史 | 中证 `stock_zh_index_hist_csindex`；仅映射支持的中证/上证指数，创业板/深证指数不盲调中证接口 |
| 当前PE降级 | 中证近期 `stock_zh_index_value_csindex` 的市盈率1；独立口径，不拼接历史分位 |
| PB及深证估值补充 | 可选 Tushare `index_dailybasic`；同一指标的当前值与分位使用同一源同一字段 |
| 10年国债 | `bond_zh_us_rate` 的中国国债收益率10年（%），检验日期；失败查中债官网 |
| 北向净流入 | 公开披露调整后默认缺失，取消单日快照拼10日/20日历史的错误逻辑 |

不得使用已移除的 `index_value_hist_funddb` 作为默认接口。也不得把中证历史接口列名存在等同于OHLC有值：它可能仅有有效收盘价，不能凭此计算RSRS。

## 计算约束

- 默认只取上海时区昨日及之前数据，避免把盘中日线当完整日线。最新数据日期必填；14自然日新鲜度检查只是粗筛，使用前仍核对最新交易日及节假日。
- 行情按照日期排序，过滤未来数据；冲突重复日期、缺失/非法OHLC直接拒绝，不删掉中间坏点后继续硬算。
- 保存完整日线给动量模块使用；近30日收益需要31条收盘价，MA20及5日前MA20需要25条。
- RSRS统一为18日OLS斜率、最近600个斜率（含当前）的样本标准差标准化，再乘R²；最少617个交易日。低价无方差、斜率标准差为零时缺失。不要再乘beta，除非分析明确改用右偏RSRS并重新验证阈值。
- 活跃度先看有效换手率；90日换手率全零或不可用时，用成交额5日均值/90日均值，并标记“成交额代理”。新浪日线缺成交额时该项缺失，不用成交量冒充成交额。
- PE/PB 5年分位要求覆盖完整5年、至少1000条正值有效日样本、有效率≥95%、没有大段断档。最新值缺失或非正时，不把更早正值称为当前估值；近期接口不能输出5年分位。
- 成长指数相对沪深300的PE比值，需要两指数同源、同口径、同日历史；交集历史不足则仅报告可核验的当前比值或缺失，不能混用Tushare和中证。
- 盈利收益率=100/正PE（%）；股债比价仅在PE和正国债收益率日期一致时计算，否则标注缺失。
- ETF行情不能替代指数行情；指数价格回报不能冒充全收益回报。

## 交付给下游

`report.json.metrics` 中每项含 `value/status/source/asof/unit`；缺失项有 `reason`。下游应读取 `.value`，并将来源和日期随指标传递，不能把整个对象当数字。

核对：`current_pe/pe_pct_5y/current_pb/pb_pct_5y/bond_10y/earnings_yield/equity_bond_ratio/rsrs_score/ma20_now/ma20_prev5/ret_30d/crowding_ratio/relative_pe_ratio/relative_pe_pct_5y/north_10d/north_20d`。完整日线在 `prices.csv`。成交额、成交量单位依各数据表元信息，不跨源直接拼数值。

未取到的项目必须逐项写 `[数据缺失：原因]`，相应维度降低置信度。北向缺失不算流入或流出，也不算择时条件满足；PE分位缺失不得给出精确历史低估判断。

接口文档、来源选择与验收见 [docs/data-sources.md](../../../docs/data-sources.md)。
