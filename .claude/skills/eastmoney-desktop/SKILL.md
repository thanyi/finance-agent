---
name: eastmoney-desktop
description: 在 Mac 的 Parallels Windows 中读取东方财富证券资金与持仓，执行不提交委托的买卖填单检查，并验证刷新、认证失效和客户端恢复；用于桌面账户操作，不用于选股分析或真实下单。
---

# 东方财富桌面账户助手

使用随 skill 分发的脚本，不依赖之前会话的 `/tmp` 文件、窗口句柄或用户名。以本文件真实路径定位 `scripts/eastmoney.py`；可从任意工作目录调用。其他客户端先诊断，不能直接沿用经典版锚点。

## 读取资金与持仓

```bash
python3 <skill目录>/scripts/eastmoney.py doctor --open-trade
python3 <skill目录>/scripts/eastmoney.py snapshot
python3 <skill目录>/scripts/eastmoney.py watch --interval 30 --count 10
python3 <skill目录>/scripts/eastmoney.py reconcile
```

首次运行先读取 [桌面操作说明](references/desktop.md)，检查诊断输出中的截图，再按说明校准。窗口尺寸、版本或 DPI 改变后重新检查截图。多个虚拟机或客户端不能自行猜选。区分“折叠／未打开交易界面”和“登录失效”。

所有原始导出、脱敏 JSON、截图、机器配置及运行结果都写入项目 `data/eastmoney/`，禁止复制真实账户数据到 skill、测试夹具或提交记录。采集时间和客户端导出时间不能冒充行情更新时间。

## 验证与恢复

用户要求检查填单时运行：

```bash
python3 <skill目录>/scripts/eastmoney.py form-check --side buy --symbol 515880 --quantity 100
python3 <skill目录>/scripts/eastmoney.py form-check --side sell --symbol 515880 --quantity 100
python3 <skill目录>/scripts/eastmoney.py test --scenario simulation
```

填单前核对当前截图中的脱敏账户标识，填写后检查 `filled.png` 与回读结果，并确认 `cleared.png` 和清空回读。不能点击最终买入／卖出按钮，不能发送 Enter 或交易快捷键。脚本没有真实提交或券商撤单接口。模拟结果不能解释为真实成交或真实撤单已验证。

用户要求验证退出登录、重启或资金分项时，读取 [恢复与资金口径](references/recovery.md)。当前任务已授权的测试无需重复询问；其他任务不能仅因加载 skill 就退出登录或重启应用。需要人工认证时保留进度，提示用户完成登录后继续，不保存或读取密码，不反复尝试认证。

## 结果纪律

- 分别报告成功、部分数据、认证要求、过期或失败。失败不能把旧快照当成新结果，读取失败不能报告空仓。
- 自绘界面目标不确定、未知弹窗、字段无法回读或清空失败时停止，保留证据。不要绕过检查使用裸坐标继续。
- 只有明确识别持仓表头且无记录才能报告空仓。不能补造资金项，不能用可用资金替代资金余额对账。
- 已知结果与限制见 [验证记录](references/validation.md)。按实际证据更新，区分实测、模拟、失败、待人工处理和未验证。
