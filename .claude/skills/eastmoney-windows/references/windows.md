# 安装与 Windows 桌面操作

## 环境和安装

Windows 10/11、Windows Python 3.10及以上、系统自带Windows PowerShell 5.1，以及已安装的东方财富客户端。使用Windows的Python进程，不使用WSL、服务会话或无交互桌面的远程任务。终端与客户端使用同一Windows用户、会话及权限级别；不要求管理员权限。锁屏或UAC安全桌面无法操作时停止。

把整个 `eastmoney-windows` 目录放在本机磁盘。独立安装无需保留Mac版skill或项目源代码：

```powershell
Set-Location C:\你的解压目录\eastmoney-windows
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

Bypass只作用于本次安装脚本进程，不修改系统执行策略。默认安装到 `$env:CODEX_HOME\skills`，未设置时为 `$env:USERPROFILE\.codex\skills`。同名skill存在时不会覆盖。可用 `-DestinationRoot <项目的.claude\skills目录>` 安装为其他工具的项目skill。重新打开工具会话以发现新skill。

Windows下Git可能把符号链接检出为文本文件，因此不要依赖项目 `.codex/skills` 中的链接来安装；`install.ps1` 复制完整实际目录，无需开发者模式或链接权限。

如需Python，使用[Python官方Windows安装说明](https://docs.python.org/3/using/windows.html)，确认 `python --version` 能运行。桌面命令只使用标准库，没有 `pip install` 前置条件。时间戳直接使用北京时间UTC+08:00，不依赖Windows上可能缺失的IANA时区数据库。

## 诊断与校准

```powershell
python -X utf8 .\scripts\eastmoney.py doctor --open-trade
```

读取返回路径中的 `driver.json` 和 `screen.png`，核对账户、普通沪深交易页、资金持仓表和窗口布局。多个客户端使用 `--client-pid` 明确选择；只发现当前Windows会话内的进程，不跨会话猜选。句柄在每次操作前重新发现。

截图核对后：

```powershell
python -X utf8 .\scripts\eastmoney.py doctor --calibrate `
  --reviewed-screenshot '<本次诊断目录>\screen.png' `
  --account-marker '<截图中的脱敏账户标识>'
python -X utf8 .\scripts\eastmoney.py snapshot
```

截图有效期五分钟；尺寸、DPI、版本必须匹配。锚点来自经典版12.1.1已验证布局；窗口移动可接受，尺寸或缩放变化后重新检查、适配。不同布局不能只改配置强行通过。

交易面板折叠时，先用 `doctor` 检查底部「界面还原」，再用 `doctor --open-trade --reviewed-screenshot <该诊断截图>` 还原，之后检查新的交易截图并校准。出现「今日申购」窗口时，先核对新截图，再单独 `doctor --dismiss-ipo --reviewed-screenshot <该截图>`，检查关闭后的截图再继续。未知弹窗不接受或关闭。

## 本机文件与保护

默认数据根目录为 `%LOCALAPPDATA%\FinanceAgent\eastmoney-windows`，配置、原始XLSX、截图及JSON都在此目录。`--output` 只能选择该根目录下的子目录。不要复制其他机器的配置和账户文件。复制skill到中文或带空格的本机路径应正常工作；不在UNC共享路径中执行。

桥接层直接启动系统PowerShell，以UTF-8读写JSON，用脚本文件传递长逻辑；每次桌面调用限时60秒。运行目录关闭NTFS继承，仅向当前用户和SYSTEM授予访问，失败时停止；不能用Windows的 `chmod(0600)` 声称已保护账户资料。

持仓表通过正常XLSX导出获取；导出向导控件核对类别、编号和文字，设置文件名后用带超时的WM_GETTEXT回读。每轮使用独立文件和哈希，不能复用旧导出。原始文件含个人字段，不加入Git或skill包。

打开导出向导后，最多等待三秒确认其身份，不重复右键或菜单点击。等待期间出现其他对话框立即停止。若仍报告 `export_dialog_missing`，检查失败截图及新的 `doctor`；向导可能延迟出现，必须确认并取消这个导出向导后再重新采集，不能直接重放点击。

默认刷新30秒一轮，共10轮，串行、不重叠。失败保留旧快照并标记过期。超时和界面未知不盲目重放点击；先重新诊断。进程锁见[Windows CreateMutex说明](https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-createmutexw)，可操作桌面通过[OpenInputDesktop](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-openinputdesktop)核对。

`test --scenario native` 不操作券商界面，只验证本机PowerShell、Unicode往返、PNG截图、跨进程互斥、释放和进程异常结束后的检查要求。其通过不能代替持仓采集或实盘交易验证。
