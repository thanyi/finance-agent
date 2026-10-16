# Windows 原生版验证记录

验证日期：2026-10-16（Asia/Shanghai）。本skill为独立Windows版本，使用Windows Python直接调用本机PowerShell。Mac主机仅用于本次开发期间传送文件和收集证据，不属于交付skill的运行依赖。

实测环境为Windows 11 ARM虚拟机，现有CPython 3.14.6 x64、Windows PowerShell 5.1及东方财富经典版12.1.1；不把此结果描述为已覆盖所有物理机、x64或客户端版本。

| 项目 | 当前证据 |
|---|---|
| 本机Python CLI | Windows实测通过：可从本地独立目录运行，六个命令无VM或共享目录参数 |
| 本机桥接、中文数据和截图 | Windows实测通过：本机PowerShell、Unicode往返、3024×1658 PNG截图 |
| 跨进程互斥及异常退出 | Windows实测通过：另一个进程被拒绝，释放后可取得锁，异常结束需检查 |
| NTFS权限 | Windows实测通过：运行目录关闭权限继承，仅当前用户及SYSTEM两条完全控制规则，设置后已回读核验 |
| 独立安装与中文路径 | Windows实测通过：安装脚本将14个文件复制到含中文和空格的本机独立路径；从安装后目录运行模拟状态机成功 |
| 登录状态识别 | Windows实测通过：正常启动客户端后识别auth_required |
| 本机持仓导出 | Windows实测通过：人工登录、本机截图校准后直接导出XLSX，读取3条持仓；持仓市值合计通过 |
| 持续刷新 | 本机采集成功后继续验证默认10轮刷新 |
| 原生版离线测试 | 22项通过，项目合计81项通过；包含编码、路径、锁、超时、解析、失效、恢复和ZIP完整性 |
| Windows网页模拟盘 | 已适配本机浏览器流程；本轮没有在Windows浏览器重复登录或提交模拟订单 |
| 实盘提交、成交及券商撤单 | 未提供、未验证 |
| 完整实盘资金对账 | 仍需账户完整分项；不把可用资金当作资金余额 |

本地开发证据仅放在项目已忽略的 `data/eastmoney-windows/`。真实Windows运行数据位于 `%LOCALAPPDATA%\FinanceAgent\eastmoney-windows`，不随skill或ZIP分发。验证结果将随实际执行更新，待执行项不能记为通过。

## 证据索引

Windows本机数据根目录下的相对路径：

- 本机运行时及权限自检：`runs/20261016-225053-c49fc6b238-test/test.json`。
- 独立安装后的本地状态机：`runs/20261016-225202-244f7eaafd-test/test.json`；安装回报另保存在开发证据 `runs/20261016-225202-98c80e2675-orchestration/result.json`。
- 认证缺失识别：`runs/20261016-224805-86f73cd1be-doctor/`。
- 人工登录后的本机诊断与校准：`runs/20261016-225354-7ad85f68ec-doctor/`、`runs/20261016-225513-16c54a5062-doctor/`。
- 本机持仓导出：`runs/20261016-225610-6e5c8e9fd4-snapshot/`。
