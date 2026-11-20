股票行情监视器 - 办公隐蔽版
==============================

使用方法
--------
1. 双击 watch.cmd，默认 15 秒刷新，隐蔽模式
2. 或命令行运行：watch.cmd [刷新秒数] [参数]

常用命令
--------
  watch.cmd                    隐蔽模式，15秒刷新（办公推荐）
  watch.cmd 30                 隐蔽模式，30秒刷新
  watch.cmd --once             取一次数据后退出
  watch.cmd --stealth off --index on
                               恢复中文红绿 + 指数方块（在家用）

快捷键
------
  Esc          退出
  R            立即后台刷新一次行情，并重新计算后续自动刷新时间
  C            按涨跌幅排序（第一次高到低，第二次恢复原顺序）
  V            按量比排序（第一次高到低，第二次恢复原顺序）
  B / F9       老板键：切换伪装 npm 构建日志 / 正常行情
  S            切换办公模式（灰白隐蔽）/ 家庭模式（中文红绿）
  ↑ / ↓        移动光标
  → / Enter    进入盘口详情（五档买卖）
  ← / Esc      盘口页返回列表

盘口详情
--------
在自选股列表中将光标移到想看的股票上，按 → 或 Enter 进入盘口页：
- 显示买卖五档价格与挂单量（单位：手）
- 显示最高、最低、涨停、跌停、成交额、现手及盘口数据更新时间
- 显示 10/20/30/120/250 日均线（每日首次进入盘口时计算并缓存）
- 显示 PE / PB（每日首次启动后后台错时计算并缓存）
- 按 R 立即刷新盘口
- 按 ↑ / ↓ 切换到上一只 / 下一只自选股盘口
- 按 ← 或 Esc 返回列表
- 刷新时只重画变化的数字，避免整屏闪烁

参数说明
--------
  --index on|off     是否显示指数方块，默认 off
                     （办公模式会固定显示上证指数、深证成指）
  --stealth on|off   隐蔽模式（灰白文字、伪装窗口标题），默认 on
  --hy on|off        启动时获取并缓存半年涨跌，默认 off
                     缓存会先展示，后台更新失败也不会空白
  --once             只取一次数据后退出

文件说明
--------
  watch.cmd            启动器（cmd 下运行）
  watch_quotes.ps1     主程序（PowerShell）
  quote_worker.ps1     后台行情获取（东财 / 新浪）
  market_worker.ps1    后台三市历史成交额获取
  watch_quotes.py      Python 备用版本
  quotes_core.py       Python 数据源与格式化工具
  watchlist.json       自选股配置
  .secids_cache.json        股票代码缓存（删除后会重新搜索）
  .hy_pct_cache.json        半年涨跌幅缓存（删除后会重新获取）
  .market_total_cache.json  沪深京三市场成交额缓存（删除后会重新获取）
  .ma_cache.json            个股日均线缓存（10/20/30/120/250 日，删除后会重新计算）
  .pepb_cache.json          个股 PE/PB 缓存（删除后会重新计算）
  verify.ps1                修改后自检脚本：验证编码与一次运行是否正常

行情数据源与冗余
----------------
- 默认优先使用东方财富（searchapi / push2 / push2his）接口
- 当东财行情接口（push2.eastmoney.com）因 DNS、网络或临时屏蔽不可用时，
  会自动降级到新浪财经（hq.sinajs.cn）获取实时行情
- 新浪财经兜底目前支持：沪深 A 股、上证指数、深证成指、创业板指、科创 50
  （北证 50 暂无法通过新浪简版接口获取，会显示为空）
- 所有脚本在请求失败时均会自动重试 1 次，降低偶发网络抖动的影响

注意事项
--------
- 办公模式默认灰白英文显示，窗口标题为 System
- 按 B 键立即伪装成 npm 构建日志，再按恢复
- 修改 watchlist.json 可增删股票和指数；名称需与行情搜索结果的正式名称完全一致
- 仅使用键盘操作，鼠标点击和控制台鼠标捕获已停用
- 行首 ! 表示该股票行情长时间未成功更新，> 表示当前光标位置
- 首次运行如提示执行策略限制，请用 watch.cmd 启动
- 若长时间频繁刷新仍被屏蔽，可适当加大刷新间隔（如 watch.cmd 60）

编码要求（重要）
----------------
watch_quotes.ps1 必须保存为「UTF-8 带 BOM」。
Windows PowerShell 5.1 读取没有 BOM 的脚本时会按系统默认编码（GBK）解析，
中文字符串和注释会被错位拆开，启动时报出
“& 字符不允许”“数组索引表达式无效（Array index expression is missing）”等解析错误。
用记事本“另存为”或某些编辑器保存时容易丢掉 BOM，改完脚本请运行一次：

    powershell -ExecutionPolicy Bypass -File verify.ps1

它会检查 BOM 并试跑一次 watch.cmd --once。

界面刷新说明
------------
隐蔽列表通过 $host.UI.RawUI.SetBufferContents + NewBufferCellArray 写入，
只重绘发生变化的行；网络结果统一由后台任务接收，不在数据层直接打印警告。
普通模式和盘口页在窗口尺寸变化时会重建布局，过宽内容按窗口宽度截断。

名称列固定 8 列（= 4 个汉字宽），不足补空格，超过按显示宽度截断。
行情标题显示最近一次成功取数时间；失败或部分数据过旧时会显示状态提示，
不会把单纯的界面重绘时间当作行情更新时间。

若再次出现闪屏，可运行：

    powershell -ExecutionPolicy Bypass -File .dsh_tests\run_verify.ps1

它会在独立窗口里跑 4 帧，并将预期内容与实际屏幕逐行比对到
.dsh_tests\verify_frame.log。双击 watch-debug.cmd 可开启写屏日志，
日志文件为 .frame_log.txt。脚本保留 $ProgressPreference = 'SilentlyContinue'，
避免 PowerShell 网络进度条干扰控制台。
