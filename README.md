# Finance Agent — A股价值投资深度分析

通过项目 Skills 实现 A 股公司与指数的数据采集、分析及 ETF 工具比较。

## 快速开始

```bash
# 安装依赖
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt

# 在 Claude Code 中运行
/analyze-stock 002415   # 海康威视
/analyze-stock 600519   # 贵州茅台
```

## Skills

| Skill | 触发方式 | 说明 |
|---|---|---|
| `analyze-stock` | `/analyze-stock [代码]` | M1-M6 完整价值投资分析 |
| `stock-data-collect` | `/stock-data-collect [代码]` | A股三张财报、行情、估值及公告采集（含降级与证据落盘） |
| `choose-index-now` | `/choose-index-now [指数]` | 指数估值、趋势与拥挤度判断 |
| `index-data-collect` | `/index-data-collect [指数]` | A股指数数据采集 |
| `check-entry-timing` | `/check-entry-timing [指数]` | 指数介入时点信号 |
| `check-etf-quality` | `/check-etf-quality [指数或ETF代码]` | 同指数 ETF 工具比较 |
| `eastmoney-desktop` | `/eastmoney-desktop` 或 `$eastmoney-desktop` | Parallels 中的资金持仓采集、不提交委托的填单检查及会话恢复验证 |

> `analyze-stock` 会自动调用 `stock-data-collect` 完成数据采集后再执行分析。

## 可直接运行的数据采集

推荐Python 3.11+。安装上述依赖后：

```bash
.venv/bin/python scripts/collect_finance.py stock 600519
.venv/bin/python scripts/collect_finance.py index 000300
.venv/bin/python scripts/collect_finance.py etf 510300
.venv/bin/python search_news.py 600519 贵州茅台 7
```

每次生成独立的 `data/` 目录，包含原始CSV、标准化表及带来源/日期/缺失原因的 `report.json`。默认截至昨日，不把盘中数据当完整日线。`partial` 表示部分数据可用，不能据此声称已完成完整分析。

新闻默认可用东方财富；配置环境变量 `TAVILY_API_KEY` 后优先使用Tavily。指数PB等可用已有权限的 `TUSHARE_TOKEN` 配合 `--tushare` 补充，默认不启用。`.env.example` 仅作变量说明，不会自动加载。

详见[数据源选择、审查发现与验证范围](docs/data-sources.md)。运行回归测试：

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

## 分析框架（M1-M6）

| 模块 | 权重 | 核心内容 |
|---|---|---|
| M1 经济护城河 | 20% | 品牌/网络效应/成本优势/转换成本等7类护城河 |
| M2 财务健康度 | 25% | ROE / FCF / 造假识别（Beneish M-Score）|
| M3 商业模式&管理层 | 15% | 定价权 / 股东回报 / 管理层量化指标 |
| M4 增长天花板 | 15% | 行业空间 / 成长驱动力 / 政策风险 |
| M5 市场情绪筹码 | 10% | 筹码结构 / 北向资金 / 融资余额 |
| M6 估值安全边际 | 15% | DCF / 格雷厄姆公式 / 相对估值 |

## 数据纪律（铁律）

- 禁止编造或估算任何财务数据
- 所有数据必须标注来源
- 无法获取的数据写明 `[数据缺失：原因]`，不得跳过

## 项目结构

```
.claude/
  skills/
    analyze-stock/SKILL.md
    stock-data-collect/SKILL.md
    choose-index-now/SKILL.md
    index-data-collect/SKILL.md
    check-entry-timing/SKILL.md
    check-etf-quality/SKILL.md
.codex/skills/                   # SKILL.md 符号链接到 .claude/skills
scripts/collect_finance.py        # stock/index/etf 采集入口
scripts/data_sources.py           # 接口时限、降级与证据落盘
scripts/finance_metrics.py        # 校验与派生指标
search_news.py                   # 新闻搜索与无密钥降级
requirements.txt                 # 经验证的运行依赖
requirements-dev.txt             # 回归测试与skill校验依赖
docs/data-sources.md             # 接口选择、验收与限制
tests/                           # 数据质量回归测试
CLAUDE.md                        # 全局数据纪律铁律
```

`.claude/skills` 是项目 skill 的唯一内容源；Codex 通过 `.codex/skills` 中的链接读取相同内容。`eastmoney-desktop` 链接整个目录以共享脚本及参考资料，运行证据只保留在 Git 忽略的 `data/eastmoney/`。
