# Windows 官网模拟盘

此流程只用于用户明确要求的东方财富官方模拟账户操作。实盘桌面脚本仍不提交委托；`test --scenario simulation` 是本地状态机，不能当作网站订单结果。

## 在本机打开模拟盘

官方入口：[我的组合](https://group.eastmoney.com/MyCombin.html)、[创建模拟组合](https://group.eastmoney.com/createcom.html)、[模拟交易室](https://group.eastmoney.com/room/index.html)。网站认证与证券客户端认证独立，人工在网页登录。多个模拟组合需明确选择，不擅自改变组合设置。新建测试组合时使用明确测试名及「仅自己可见」。

浏览器自动化是可选依赖；桌面命令不需要安装它。若当前工具没有可用浏览器控制，可在Windows的独立Python会话中使用Playwright及已安装的Edge或Chrome：

```powershell
python -m pip install playwright
python -X utf8 -i
```

在同一Python交互会话执行，保持会话存活等待用户认证：

```python
import os
from pathlib import Path
from playwright.sync_api import sync_playwright
runtime = Path(os.environ['LOCALAPPDATA']) / 'FinanceAgent' / 'eastmoney-windows'
profile = runtime / 'simulator-browser'
pw = sync_playwright().start()
context = pw.chromium.launch_persistent_context(str(profile), channel='msedge', headless=False)
page = context.pages[0] if context.pages else context.new_page()
page.goto('https://group.eastmoney.com/createcom.html', wait_until='domcontentloaded')
```

先运行一次桌面 `doctor` 为专用运行目录设置私有NTFS权限，再在其下创建浏览器用户目录。已安装Chrome时可改为 `channel='chrome'`。不复制Mac浏览器配置、日常浏览器资料、密码或Cookie；不要使用旧Mac调试端口、绝对路径。当前上下文中只操作官方模拟页面，结束时 `context.close(); pw.stop()`。

## 页面检查与操作

进入自己的组合对应交易室后，严格核对HTTPS主机为 `group.eastmoney.com`、路径为 `/room/index.html`，再核对模拟组合名称、虚拟资金和本次组合标识。页面上的证券交易、基金交易链接可能进入实盘，不点击这些链接。

当前网页适配点来自2026-10-16官网实测；先观察本机DOM，页面变化时重新确认：

| 元素 | 用途 |
|---|---|
| `#hptZjzh` | 当前模拟资金账号；每次提交前比较已核对的目标，不持久化到skill |
| `li[data-type="ccxx"]` / `gd` / `drwt` / `drcj` | 持仓／挂单／当日委托／当日成交 |
| `#ccxx<代码> .kysl` | 当前可卖数量 |
| `span.tab_buy` / `span.tab_sale` | 买入／卖出方向，核对active状态 |
| `#futcode` | 证券代码；等候建议列表并核对 `data-stockdata` 后选择 |
| `[data-bind="f57"]` | 报价对应代码，需与表单目标一致；报价异步更新 |
| `#price` / `#codenumber` | 价格／数量，提交前重新回读 |
| `#gt10` / `#gt3` | 当前页面跌停／涨停价，不能抄用以前的价格 |
| `#btnOrder` | 模拟买卖提交按钮；可能直接提交，没有二次确认 |
| `#drwt<合同编号>` / `#drcj<编号>` | 委托／成交回报 |
| `#gd<合同编号> .chedan` | 指定挂单的撤单入口 |
| `#Cxc_Dialog .question` / `#btnCxcConfirm` | 核对准确的「确认撤单？」后确认 |

先保存初始资金、持仓和已有委托；只用用户授权的最小测试数量，卖出不得超过已有可用数量。数值填写及建议列表异步返回，等待按钮可用，不强行启用。提交前把意图写到运行目录，核对方向、标的、价格、数量、目标组合，再只点击一次。

委托表行存在不代表已刷新；等待目标编号的实际状态及数量，另从成交页核对价格、金额和手续费。回报未知先查单，不盲目重发。只撤本次产生且编号、代码、价格、数量均匹配的测试单。保持同一控制会话操作撤单提示，确认提示消失后先查状态，不把打开确认框当作撤单成功。

最终要核对「全部成交」或「全部撤单」、成交数量、冻结释放及资金变化，再清空表单。当天买入可能不能当天卖出，不伪造可用持仓或为了恢复可用数量补单。只用Decimal核对已显示的资金口径；完整实盘对账仍是另一项工作。

## 验证范围

上述网页流程曾在Mac控制的官方模拟盘取得买入、卖出各100份全部成交及一笔100份全部撤单。它证明网站DOM及流程，不等于本Windows版已在本机重新登录并下单。Windows原生版自己的已验证项见[验证记录](validation.md)。个人账号、订单、截图和浏览器用户目录只保留在本机运行目录，不打包或提交。
