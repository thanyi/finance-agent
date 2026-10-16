---
name: eastmoney-windows
description: 在纯 Windows 本机直接读取东方财富证券资金持仓，检查实盘填单但不提交，验证刷新和会话恢复；也指导官网模拟盘买卖撤单测试。无需 Mac、Parallels、WSL 或 QMT，不提供实盘下单。
---

# 东方财富 Windows 原生助手

这是可独立复制安装的 Windows skill。使用当前 skill 目录下的 `scripts/eastmoney.py`，Python 在已登录、未锁屏的 Windows 桌面中直接调用本机 PowerShell。不要调用 Mac 版桥接器或复用其他机器的配置、句柄和截图。

首次使用读取[安装与桌面操作](references/windows.md)。桌面诊断、导出解析和截图不需要安装第三方 Python 库。

```powershell
python -X utf8 .\scripts\eastmoney.py doctor --open-trade
python -X utf8 .\scripts\eastmoney.py snapshot
python -X utf8 .\scripts\eastmoney.py watch --interval 30 --count 10
python -X utf8 .\scripts\eastmoney.py reconcile
python -X utf8 .\scripts\eastmoney.py test --scenario native
```

诊断后检查本机截图、账户及布局，再按文档校准。上述相对路径假定当前目录为skill目录；其他目录应使用实际完整路径。运行数据默认存放 `%LOCALAPPDATA%\FinanceAgent\eastmoney-windows\`，由NTFS权限限制访问；不是安装目录，也不需要任何共享盘。

## 填单、恢复与模拟盘

实盘只允许用户要求的不提交填单检查：

```powershell
python -X utf8 .\scripts\eastmoney.py form-check --side buy --symbol 515880 --quantity 100
python -X utf8 .\scripts\eastmoney.py form-check --side sell --symbol 515880 --quantity 100
python -X utf8 .\scripts\eastmoney.py test --scenario simulation
```

字段不能可靠回读或清空时停止。禁止点击实盘最终买卖按钮、使用Enter或交易快捷键。脚本没有实盘提交或券商撤单接口。

用户要求退出登录、重启或资金核对时，读取[恢复和资金口径](references/recovery.md)，沿用当前任务已有授权。不要仅因加载skill而退出登录或重启软件；认证由用户在Windows本机完成，不读取或保存密码。

用户明确要求官方模拟盘时，读取[Windows官网模拟盘流程](references/simulation-web.md)。只有在已核对的官方模拟账户中，才可按用户要求提交模拟订单；不能把本地状态机结果当成官方回报。

## 运行结果

- 区分成功、部分数据、登录失效、过期和失败；失败保留旧快照并标记过期，不报告伪造的新结果。
- 未知弹窗、遮挡、DPI或布局变化时停止；没有明确表头的读取失败不能解释为空仓。
- 不补造缺失资金，不把可用资金当作资金余额。所有计算使用Decimal。
- 命名互斥量覆盖同一Windows会话中的多个skill副本；锁被其他进程持有时停止，前一工作进程崩溃时先检查界面。
- 实测环境、能力限制及证据见[验证记录](references/validation.md)。Mac版结果不自动等于Windows原生版通过。
