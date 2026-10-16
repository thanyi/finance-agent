# 桌面运行与校准

依赖 Python 标准库、Mac 的 `prlctl` 和 Windows 自带 PowerShell／Win32。无需 Windows Python、QMT、OCR 服务或密码文件。项目通过 Parallels Home 共享可访问；其他位置显式指定 `--guest-root`。

先运行 `doctor --open-trade`，读取返回目录内的 `driver.json` 和 `screen.png`，确认预期账户、普通沪深交易页面及底部资金持仓表。折叠面板与认证失效必须区分，行情软件登录不代表交易认证。若诊断显示底部「界面还原」，检查该截图后用 `doctor --open-trade --reviewed-screenshot <该目录>/screen.png` 还原；这一步不会直接完成交易面板校准。

登录后可能出现「今日申购」弹窗（`TradeIpoDuiDlg`）。先检查当前诊断截图，确认是该窗口且要关闭；再单独运行 `doctor --dismiss-ipo --reviewed-screenshot <诊断目录>/screen.png`。脚本核对截图时效、窗口身份、进程、版本、尺寸和DPI，只向该窗口发送关闭消息，不操作申购按钮。检查返回的新截图后再校准或采集；其他弹窗不能套用此操作。

## 校准

检查截图后运行：

```bash
python3 <skill目录>/scripts/eastmoney.py doctor --open-trade --calibrate \
  --reviewed-screenshot <诊断目录>/screen.png \
  --account-marker '<截图中的脱敏账户标识>'
```

截图需来自本工具且不超过五分钟。记录版本、DPI、尺寸和证据哈希，不持久化句柄。内置相对锚点来自经典版已有布局；只有截图一致时才可校准。不同布局应先适配并验证对应目标，不能仅运行命令强行通过。账户标识仅接受含星号的脱敏形式。

全局参数放在子命令前：`--vm <UUID>`、`--guest-root <Windows中的项目路径>`、`--output <data/eastmoney内目录>`、`--client-pid <PID>`。只自动选择唯一运行的 Windows 虚拟机。PID仅选择本次调用，不跨重启保存。文件和目录分别限制为当前用户读写、访问。

## 必要操作细节

- `prlctl exec --current-user` 进入登录桌面；默认执行在系统会话。
- 使用 skill 中的 PowerShell 文件和共享 JSON 请求，长脚本不直接塞进命令行。路径使用 PowerShell 单引号转义，参数通过 JSON 传递。
- 枚举窗口并核对所属进程、标题、子控件。进程的 `MainWindowHandle` 可能只是行情窗口。
- 自绘证券界面不提供持仓单元格的 UIA 模式。正常路径是刷新、表格右键「数据导出」、XLSX 向导、共享目录解析。
- 打开自绘菜单与点击导出应在同一个 Windows 调用中完成；新 `prlctl exec` 可能创建控制台，使菜单失去焦点。
- 文件名通过 `WM_SETTEXT` 设置，再以带超时的 `WM_GETTEXT` 回读。跨进程 `GetWindowText` 的空结果不能证明 Edit 为空。
- 按钮消息只发送给核对过父窗口、进程、编号、类别和标签的导出向导控件。导出选择全部字段，完成后关闭向导。

## 刷新与错误

默认每30秒一轮，共10轮；耗时超出间隔则顺延、不重叠。每轮使用唯一文件，内容相同不判失败。新文件必须通过ZIP、表头、数量及市值检查。

超时后先检查当前界面；不要直接重放点击。未知弹窗、布局变化和认证要求不重试。认证失效时保存 watch 进度，重新登录后用 `watch --resume <watch目录>` 继续。

填单只验证指定持仓ETF、100份整数倍及当次客户端参考价，不验证行情时效、成交条件或交易权限。失败后清空；清空失败应告知用户可能保留草稿，不能报告通过。

当前12.1.1的输入区同样为自绘控件。本次买卖检查在发现无法回读字段后、写入前停止。不能为了让检查通过而改用未经核对的键盘输入；后续支持其他输入后端需另行实现并验证回读和清空。
