股票行情监视器（Windows / macOS 通用）
======================================

一个键盘操作的自选股行情终端，支持 Windows（PowerShell 版）和
macOS / Linux（Python 版）。两端使用相同的命令行、默认值、快捷键和
盘口布局，并共用同一套行情缓存文件。

最近更新（2026-11）
-------------------
- Windows / macOS 的命令行参数、默认值、帮助文本和退出码已对齐
- macOS 修复 SSL 证书校验失败导致行情为空的问题，并新增 curl / 新浪
  日线兜底，东财 push2his 不可用时仍可计算半年涨跌和日均线
- 两端缓存统一为 UTF-8 无 BOM，并兼容旧 BOM 文件，可交替运行
- 北交所代码使用 bj / s_bj 前缀，实时行情和日线兜底都可以命中
- macOS 隐蔽模式改用终端正常前景色，不再使用偏暗的灰色
- 新增 tests/ 单元测试和 macOS/Linux 的 verify.sh 自检脚本

启动方式
--------
Windows：
  1. 双击 watch.cmd
  2. 或在 cmd 中运行：watch.cmd [刷新秒数] [参数]

macOS / Linux：
  1. 打开终端，进入项目目录
  2. 运行：./watch [刷新秒数] [参数]
     （./watch.sh 等价，需要 Python 3.8+）

两端的参数完全一致，例如：
  watch.cmd                     Windows：隐蔽模式，15 秒刷新
  ./watch                       macOS：隐蔽模式，15 秒刷新
  watch.cmd 30                  Windows：30 秒刷新
  ./watch 30 --index on         macOS：30 秒刷新并显示指数方块
  watch.cmd --stealth off --index on
  ./watch --stealth off --index on
                                普通模式：中文 + 指数方块
  watch.cmd --once              取一次数据后退出
  ./watch --once

如果只想快速确认程序是否正常，运行：
  Windows：powershell -ExecutionPolicy Bypass -File verify.ps1
  macOS / Linux：./verify.sh

命令行参数
----------
  刷新秒数          自动刷新间隔（秒），默认 15
  --index on|off    是否显示指数方块，默认 off
                    （隐蔽模式固定显示上证指数、深证成指）
  --stealth on|off  隐蔽模式（英文、朴素窗口标题），默认 on
                    （macOS/Linux 使用终端正常前景色，不再使用暗灰色）
  --hy on|off       获取并显示半年涨跌，默认 off
  --once            只取一次数据后退出
  -h, --help        显示帮助

参数支持 on/off、true/false、1/0 三种写法，也支持 --index=on 形式。
参数错误时返回退出码 2，并打印与 --help 相同的用法说明；运行失败返回 1。

快捷键
------
列表页：
  Esc          退出程序
  R            立即刷新行情
  C            按涨跌幅排序（高→低，再按一次恢复）
  V            按量比排序（高→低，再按一次恢复）
  S            切换隐蔽模式 / 普通模式
  B / F9       老板键：切换伪装 npm 构建日志 / 正常行情
  ↑ / ↓        移动光标
  → / Enter    进入盘口详情
  Ctrl+C       强制退出

盘口详情页：
  R            立即刷新盘口
  ↑ / ↓        切换到上一只 / 下一只自选股盘口
  ← / Esc      返回列表
  Ctrl+C       强制退出

盘口详情
--------
- 买卖五档价格与挂单量（单位：手）
- 最新、均价、昨收、今开、最高、最低、涨停、跌停、换手、成交额、量比
- 10/20/30/120/250 日均线（每日首次进入盘口时计算并缓存）
- PE / PB（每日首次使用后后台错时计算并缓存）
- 触及涨停/跌停时显示封板金额
- 盘口每 10 秒自动刷新一次

平台差异
--------
两端的主流程、字段、列宽、排序和快捷方式一致，但受终端能力限制：
  * Windows 使用 PowerShell 控制台 API 做逐行增量重绘；
    macOS / Linux 使用 ANSI 整帧重绘，不会调用 clear 命令，闪烁已明显降低。
  * 窗口标题：Windows 通过控制台 API 设置；macOS / Linux 通过 ANSI OSC 序列，
    个别精简终端可能不支持。
  * 鼠标点击：两端均以键盘操作为主；Python 版不支持鼠标点击。
  * Windows 版无需安装 Python；macOS / Linux 需要 Python 3.8+。

文件说明
--------
  watch.cmd            Windows 启动器（双击或 cmd 中运行）
  watch                macOS / Linux 统一入口（Git Bash 中会转给 watch.cmd）
  watch.sh             macOS / Linux 启动器
  watch_quotes.ps1     Windows 主程序（PowerShell 5.1）
  watch_quotes.py      macOS / Linux 主程序（Python 3.8+）
  quotes_core.py       Python 数据源、缓存、格式化与终端工具
  quote_worker.ps1     Windows 后台行情获取（东财 / 新浪）
  market_worker.ps1    Windows 后台三市历史成交额获取
  watchlist.json       自选股配置
  verify.ps1           Windows 自检：BOM + 一次运行
  verify.sh            macOS / Linux 自检：BOM + 单元测试 + 一次运行
  tests/               Python 单元测试（CLI、缓存、格式化、渲染）

行情缓存（两端共用）
--------------------
  .secids_cache.json        名称 -> secid 缓存
  .hy_pct_cache.json        半年涨跌幅缓存
  .market_total_cache.json  沪深京三市场成交额历史缓存
  .ma_cache.json            个股日均线缓存
  .pepb_cache.json          个股 PE/PB 缓存
  .depth_cache.json         Windows 盘口缓存
  .frame_log.txt            设置 WATCH_FRAME_LOG=1 时的诊断日志

缓存统一为 UTF-8 无 BOM，并用临时文件原子替换。Python 读取时兼容
PowerShell 5.1 曾写出的带 BOM 文件，因此两端可以交替运行。删除缓存文件
后程序会自动重新获取。

行情数据源与冗余
----------------
- 实时行情优先东方财富 push2，失败自动降级到新浪财经 hq.sinajs.cn
- 日线（半年涨跌、日均线）优先东方财富 push2his，失败自动改用
  新浪 CN_MarketData.getKLineData
- 若系统有 curl，urllib 请求失败时会自动用 curl 重试；可用环境变量
  WATCH_DISABLE_CURL=1 关闭该行为
- 三市成交额历史在 push2his 不可用时会继续使用已有缓存，不会整块消失
- 所有请求失败会自动重试；新浪兜底支持沪深 A 股、北证 A 股、上证指数、
  深证成指、创业板指、科创 50、北证 50（使用 bj 前缀）
- 行情标题显示最近一次成功取数时间；长时间未更新的行首会标 `!`

编码与换行
----------
- watch_quotes.ps1 必须保存为 UTF-8 带 BOM。Windows PowerShell 5.1 读取
  无 BOM 脚本时会按系统默认编码（GBK）解析，中文字符串和注释会错位。
  改完脚本请运行：
      powershell -ExecutionPolicy Bypass -File verify.ps1
- Python 文件、watch、watch.sh、README 使用 UTF-8（无 BOM）。
- watch.cmd / watch-debug.cmd 使用 ASCII + CRLF。

macOS 特别说明
--------------
- 首次运行若提示 SSL 证书校验失败，程序会自动降级并在终端打印一次警告；
  彻底解决可运行 Python 安装目录下的 Install Certificates.command。
- 如果系统里有多个 Python，可用 WATCH_PYTHON=/path/to/python3 ./watch 指定。
- 交互模式需要真实终端；在管道/后台任务中请使用 --once。
- 终端窗口建议至少 24 行，列表行数过多时会围绕光标自动开窗显示。
- --hy on 首次运行需要抓取较多日线数据；--once --hy on 可能会多等十几秒。

Windows 特别说明
----------------
- 首次运行如提示执行策略限制，请用 watch.cmd 启动（内部已带
  -ExecutionPolicy Bypass）。
- 若长时间频繁刷新被行情服务端屏蔽，可加大刷新间隔，如 watch.cmd 60。
- 双击启动时，参数错误或运行失败后窗口会暂停以显示错误；在命令行中
  可用 echo %ERRORLEVEL% 查看退出码。
