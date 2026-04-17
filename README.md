# Finance Agent — A股价值投资深度分析

基于格雷厄姆 / 巴菲特 / 费雪投资哲学，通过 Claude Code Skills 实现 A 股六模块深度分析。

## 快速开始

```bash
# 安装依赖
pip install akshare baostock requests pandas

# 在 Claude Code 中运行
/analyze-stock 002415   # 海康威视
/analyze-stock 600519   # 贵州茅台
```

## Skills

| Skill | 触发方式 | 说明 |
|---|---|---|
| `analyze-stock` | `/analyze-stock [代码]` | M1-M6 完整价值投资分析 |
| `stock-data-collect` | `/stock-data-collect [代码]` | A股基础数据采集（BaoStock / AKShare / Sina） |

> `analyze-stock` 会自动调用 `stock-data-collect` 完成数据采集后再执行分析。

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
    analyze-stock/SKILL.md       # M1-M6 分析框架
    stock-data-collect/SKILL.md  # 数据采集 SOP
CLAUDE.md                        # 全局数据纪律铁律
```
