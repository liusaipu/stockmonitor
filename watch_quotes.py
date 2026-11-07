"""
股票行情监视器（macOS / Linux / Windows 通用 Python 版）

与 Windows 的 PowerShell 版保持相同的命令行和默认值：
  用法: watch [刷新秒数] [--index on|off] [--stealth on|off] [--hy on|off] [--once] [-h]

快捷键:
  Esc          退出程序；盘口页返回列表
  R            立即刷新行情 / 盘口
  C            按涨跌幅排序（高→低，再按一次恢复）
  V            按量比排序（高→低，再按一次恢复）
  S            切换隐蔽模式 / 普通模式
  B / F9       老板键：切换 npm 构建日志 / 正常行情
  ↑ / ↓        移动光标；盘口页切换上一只 / 下一只
  → / Enter    进入盘口详情
  ←            盘口页返回列表
  Ctrl+C       强制退出
"""

import atexit
import json
import os
import shutil
import signal
import sys
import threading
import time
import traceback

from quotes_core import (
    BASE_DIR, F_CODE, F_MARKET, F_NAME, F_PRICE, F_PCT_CHG, F_CHG,
    F_VOL, F_AMT, F_TURNOVER, F_VOL_RATIO, F_HIGH, F_LOW, F_OPEN, F_PREV_CLOSE,
    F_TOTAL_CAP, F_FLOAT_CAP, F_LATEST_VOL,
    search_secids, fetch_quote_rows, get_key, enter_raw_mode, restore_terminal,
    is_tty, truncate_name, truncate_to_width, is_etf, fmt_num, fmt_open_price,
    display_width, pad_left, pad_right, fetch_depth_data, get_market_total_amounts,
    warm_market_total_cache, apply_half_year_pct, start_half_year_update,
    complete_half_year_update, get_daily_mas, complete_ma_update,
    get_pepb, complete_pepb_update,
)

DEFAULT_INTERVAL = 15
STEALTH_TABLE_WIDTH = 64
NORMAL_TABLE_WIDTH = 114

# ── ANSI / 控制台 ──────────────────────────────────────────
_C_RESET = '\033[0m'
# 隐蔽模式不再使用暗灰（macOS 深色终端下几乎看不清），改用终端默认前景色
_C_GRAY = '\033[39m'
_C_WHITE = '\033[97m'
_C_RED = '\033[91m'
_C_GREEN = '\033[92m'
_C_BLACK_BG = '\033[40m'
_C_DARKGRAY_BG = '\033[100m'
_C_HIDE_CURSOR = '\033[?25l'
_C_SHOW_CURSOR = '\033[?25h'

_ANSI = False
_VT_ENABLED = False
_windows_state = None
_first_frame = True


def _enable_windows_console():
    """Windows：UTF-8 代码页 + 启用 ANSI 转义序列。"""
    global _windows_state, _VT_ENABLED
    if os.name != 'nt':
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        old_mode = None
        if handle not in (0, -1) and kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            old_mode = mode.value
            _VT_ENABLED = bool(kernel32.SetConsoleMode(handle, old_mode | 0x0004))
        old_out_cp = kernel32.GetConsoleOutputCP()
        old_in_cp = kernel32.GetConsoleCP()
        kernel32.SetConsoleOutputCP(65001)
        kernel32.SetConsoleCP(65001)
        _windows_state = (old_out_cp, old_in_cp, handle, old_mode)
    except Exception:
        _windows_state = None


def _restore_windows_console():
    if os.name != 'nt' or _windows_state is None:
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        old_out_cp, old_in_cp, handle, old_mode = _windows_state
        kernel32.SetConsoleOutputCP(old_out_cp)
        kernel32.SetConsoleCP(old_in_cp)
        if old_mode is not None:
            kernel32.SetConsoleMode(handle, old_mode)
    except Exception:
        pass


def _init_output():
    global _ANSI
    _enable_windows_console()
    for stream in (sys.stdout, sys.stderr):
        try:
            if hasattr(stream, 'reconfigure'):
                stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass
    try:
        _ANSI = bool(sys.stdout.isatty()) and (os.name != 'nt' or _VT_ENABLED)
    except Exception:
        _ANSI = False
    atexit.register(restore_terminal)
    atexit.register(_restore_windows_console)


def _style(text, fg=None, bg=None):
    """返回 (text, ANSI 前缀)；不支持 ANSI 时前缀为空。"""
    if not _ANSI:
        return (text, '')
    prefix = (fg or '') + (bg or '')
    return (text, prefix)


def set_title(title):
    if _ANSI:
        try:
            sys.stdout.write(f'\033]0;{title}\007')
            sys.stdout.flush()
        except Exception:
            pass
    if os.name == 'nt':
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleTitleW(title)
        except Exception:
            pass


def clear_screen():
    if _ANSI:
        sys.stdout.write('\033[2J\033[H')
        sys.stdout.flush()
    else:
        os.system('cls' if os.name == 'nt' else 'clear')


def _term_size():
    try:
        size = shutil.get_terminal_size((120, 40))
        return max(20, size.columns), max(10, size.lines)
    except Exception:
        return 120, 40


def draw_frame(lines):
    """整帧重绘：光标回左上角逐行覆盖，避免 os.system('clear') 的闪烁。"""
    global _first_frame
    cols, rows = _term_size()
    if not _ANSI:
        clear_screen()
        for text, _style_prefix in lines:
            print(text)
        return
    if _first_frame:
        sys.stdout.write('\033[2J')
        _first_frame = False
    out = ['\033[H']
    rendered = []
    for text, prefix in lines[:rows]:
        text = truncate_to_width(text, cols - 2)
        pad = max(0, cols - 2 - display_width(text))
        text = text + ' ' * pad
        rendered.append(prefix + text + _C_RESET + '\033[K')
    out.append('\n'.join(rendered))
    out.append('\033[J')
    try:
        sys.stdout.write(''.join(out))
        sys.stdout.flush()
    except BrokenPipeError:
        raise


def print_lines_plain(lines):
    for text, _prefix in lines:
        print(text)


def _show_cursor():
    if _ANSI:
        try:
            sys.stdout.write(_C_SHOW_CURSOR)
            sys.stdout.flush()
        except Exception:
            pass


# ── 命令行参数 ─────────────────────────────────────────────

class UsageError(Exception):
    pass


def _parse_on_off(value):
    v = str(value).strip().lower()
    if v in ('on', 'true', '1', 'yes', 'y'):
        return True
    if v in ('off', 'false', '0', 'no', 'n'):
        return False
    return None


def parse_args(argv):
    """解析参数，返回 dict；出错抛 UsageError。

    支持 `--index on` 与 `--index=on` 两种写法，与 PowerShell 版一致。
    """
    cfg = {
        'interval': DEFAULT_INTERVAL,
        'show_index': False,
        'stealth': True,
        'fetch_hy': False,
        'once': False,
        'help': False,
    }
    args = list(argv[1:])
    if any(a in ('-h', '--help') for a in args):
        cfg['help'] = True
        return cfg

    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ('--once', '-once'):
            cfg['once'] = True
            i += 1
            continue
        if arg.startswith('-'):
            name, sep, value = arg.partition('=')
            name = name.lstrip('-').lower()
            if name not in ('index', 'stealth', 'hy'):
                raise UsageError(f'无法识别的参数: {arg}')
            if not sep:
                i += 1
                if i >= len(args) or args[i].startswith('-'):
                    raise UsageError(f'--{name} 需要 on 或 off')
                value = args[i]
            parsed = _parse_on_off(value)
            if parsed is None:
                raise UsageError(f'--{name} 的值必须是 on 或 off（收到: {value}）')
            if name == 'index':
                cfg['show_index'] = parsed
            elif name == 'stealth':
                cfg['stealth'] = parsed
            else:
                cfg['fetch_hy'] = parsed
            i += 1
            continue
        try:
            interval = int(arg)
        except ValueError:
            raise UsageError(f'无法识别的参数: {arg}') from None
        if interval <= 0:
            raise UsageError(f'刷新秒数必须大于 0（收到: {arg}）')
        cfg['interval'] = interval
        i += 1
    return cfg


def print_help(stream=None):
    stream = stream or sys.stdout
    print('用法: watch [刷新秒数] [--index on|off] [--stealth on|off] [--hy on|off] [--once] [-h]', file=stream)
    print('', file=stream)
    print('参数:', file=stream)
    print('  刷新秒数          自动刷新间隔，默认 15 秒', file=stream)
    print('  --index on|off    是否显示指数方块，默认 off', file=stream)
    print('                    （隐蔽模式固定显示上证指数、深证成指）', file=stream)
    print('  --stealth on|off  隐蔽模式（英文、朴素窗口标题），默认 on', file=stream)
    print('  --hy on|off       获取并显示半年涨跌，默认 off', file=stream)
    print('  --once            只取一次数据后退出', file=stream)
    print('  -h, --help        显示此帮助信息', file=stream)
    print('', file=stream)
    print('Windows 下命令为 watch.cmd；macOS / Linux 下为 ./watch，参数完全相同。', file=stream)
    print('', file=stream)
    print('列表页快捷键:', file=stream)
    print('  Esc        退出程序', file=stream)
    print('  R          立即刷新行情', file=stream)
    print('  C          按涨跌幅排序（高→低，再按一次恢复）', file=stream)
    print('  V          按量比排序（高→低，再按一次恢复）', file=stream)
    print('  S          切换隐蔽模式 / 普通模式', file=stream)
    print('  B / F9     切换 npm 构建日志外观 / 正常行情', file=stream)
    print('  ↑ / ↓      移动光标选择股票', file=stream)
    print('  → / Enter  进入选中股票的盘口详情', file=stream)
    print('', file=stream)
    print('盘口详情页快捷键:', file=stream)
    print('  R          刷新盘口', file=stream)
    print('  ↑ / ↓      切换到上一只 / 下一只自选股盘口', file=stream)
    print('  ← / Esc    返回列表', file=stream)
    print('  Ctrl+C     强制退出', file=stream)


def _usage_error(message):
    print(f'错误: {message}', file=sys.stderr)
    print('', file=sys.stderr)
    print_help(sys.stderr)
    return 2


# ── 数据状态 ───────────────────────────────────────────────

def load_watchlist():
    path = os.path.join(BASE_DIR, 'watchlist.json')
    with open(path, 'r', encoding='utf-8-sig') as f:
        wl = json.load(f)
    stocks = [str(x) for x in wl.get('stocks', [])]
    indices = [str(x) for x in wl.get('indices', [])]
    return stocks, indices


def _row_secid(row):
    return f"{row.get(F_MARKET, '0')}.{row.get(F_CODE, '')}"


_MISSING = (None, '', '-')


def _merge_row(old, new):
    if old is None:
        return dict(new)
    merged = dict(old)
    for key, value in new.items():
        if value not in _MISSING:
            merged[key] = value
    return merged


class AppState:
    def __init__(self):
        self.stock_ids = []
        self.index_ids = []
        self.rows = {}
        self.last_success = {}
        self.last_quote_ts = None
        self.status = ''
        self.loaded = False

    def stock_rows(self):
        return [self.rows[s] for s in self.stock_ids if s in self.rows]

    def index_rows(self):
        return [self.rows[s] for s in self.index_ids if s in self.rows]

    def merge(self, result):
        now = time.time()
        self.stock_ids = result['stock_ids']
        self.index_ids = result['index_ids']
        for row in result['stocks'] + result['indices']:
            secid = _row_secid(row)
            self.rows[secid] = _merge_row(self.rows.get(secid), row)
            self.last_success[secid] = now
        self.last_quote_ts = now
        self.loaded = True
        missing = result.get('missing_stock', 0) + result.get('missing_index', 0)
        if missing:
            self.status = f'未找到 {missing} 个名称（检查 watchlist.json）'
        else:
            self.status = ''

    def quote_time(self):
        if not self.last_quote_ts:
            return '--:--:--'
        return time.strftime('%H:%M:%S', time.localtime(self.last_quote_ts))

    def is_stale(self, secid, interval):
        last = self.last_success.get(secid)
        if last is None:
            return True
        return (time.time() - last) > max(90, interval * 3)


class Worker:
    """一次性后台任务：start 后由主循环 take 取结果。"""

    def __init__(self):
        self._thread = None
        self._done = False
        self._result = None
        self._error = None
        self._lock = threading.Lock()

    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self, target):
        if self.running():
            return False
        self._done = False
        self._result = None
        self._error = None

        def run():
            try:
                result = target()
            except Exception as e:
                with self._lock:
                    self._error = e
                    self._done = True
                return
            with self._lock:
                self._result = result
                self._done = True

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()
        return True

    def take(self):
        with self._lock:
            if not self._done:
                return None
            result, error = self._result, self._error
            self._done = False
            self._result = None
            self._error = None
            return result, error


def _fetch_list():
    stocks, indices = load_watchlist()
    stock_ids = search_secids(stocks)
    index_ids = search_secids(indices)
    if not stock_ids and not index_ids:
        raise RuntimeError('没有可查询的股票或指数（检查 watchlist.json 或网络）')
    rows = fetch_quote_rows(stock_ids + index_ids)
    index_set = set(index_ids)
    stock_rows = []
    index_rows = []
    for row in rows:
        if _row_secid(row) in index_set:
            index_rows.append(row)
        else:
            stock_rows.append(row)
    return {
        'stock_ids': stock_ids,
        'index_ids': index_ids,
        'stocks': stock_rows,
        'indices': index_rows,
        'missing_stock': max(0, len(stocks) - len(stock_ids)),
        'missing_index': max(0, len(indices) - len(index_ids)),
    }


# ── 列表行格式化（与 PowerShell 版字段/顺序/宽度一致）──────

def _numeric_key(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def apply_sort(rows, sort_state):
    """按当前排序状态返回新列表；无效值排在最后。"""
    if sort_state['c'] != 0:
        field = F_PCT_CHG
        reverse = sort_state['c'] == 1
    elif sort_state['v'] != 0:
        field = F_VOL_RATIO
        reverse = sort_state['v'] == 1
    else:
        return rows

    def key(row):
        value = _numeric_key(row.get(field))
        if value is None:
            return float('-inf') if reverse else float('inf')
        return value

    return sorted(rows, key=key, reverse=reverse)


def _fmt_row_stealth(row, has_cursor, stale):
    digits = 3 if is_etf(row) else 2
    cursor = '!' if stale else ('>' if has_cursor else ' ')
    price = fmt_num(row.get(F_PRICE), digits=digits)
    change = fmt_num(row.get(F_CHG), digits=digits)
    pct = fmt_num(row.get(F_PCT_CHG))
    turnover = fmt_num(row.get(F_TURNOVER))
    turnover_str = '-' if turnover == '-' else f'{turnover}%'
    vr = fmt_num(row.get(F_VOL_RATIO))
    return (
        cursor + pad_left(row.get(F_CODE, ''), 7) + ' '
        + pad_right(truncate_name(row.get(F_NAME, '')), 8) + ' '
        + pad_left(f'{pct}%', 9) + ' '
        + pad_left(change, 9) + ' '
        + pad_left(price, 8) + ' '
        + pad_left(vr, 8) + ' '
        + pad_left(turnover_str, 8)
    )


def _fmt_row_normal(row, has_cursor, stale):
    digits = 3 if is_etf(row) else 2
    cursor = '!' if stale else ('>' if has_cursor else ' ')
    code = row.get(F_CODE, '')
    name = truncate_name(row.get(F_NAME, ''))
    price = fmt_num(row.get(F_PRICE), digits=digits)
    prev_close = fmt_num(row.get(F_PREV_CLOSE), digits=digits)
    open_price, open_arrow = fmt_open_price(
        row.get(F_OPEN), row.get(F_PREV_CLOSE), digits=digits
    )
    high = fmt_num(row.get(F_HIGH), digits=digits)
    low = fmt_num(row.get(F_LOW), digits=digits)
    change = fmt_num(row.get(F_CHG), digits=digits)
    pct = fmt_num(row.get(F_PCT_CHG))
    turnover = fmt_num(row.get(F_TURNOVER))
    vr = fmt_num(row.get(F_VOL_RATIO))
    amount = fmt_num(row.get(F_AMT), divisor=100000000)
    hy_pct = fmt_num(row.get('hyPct'))
    prefix = cursor + pad_left(code, 7) + ' ' + pad_right(name, 8) + ' '
    suffix = (
        ' ' + pad_left(change, 8) + ' '
        + pad_left(price, 7) + ' '
        + pad_left(prev_close, 7) + ' '
        + pad_left(open_price, 7) + open_arrow + ' '
        + pad_left(high, 7) + ' '
        + pad_left(low, 7) + ' '
        + pad_left(vr, 7) + ' '
        + pad_left(turnover, 8) + ' '
        + pad_left(amount, 9) + ' '
        + pad_left(f'{hy_pct}%', 10)
    )
    return prefix + pad_left(f'{pct}%', 8) + suffix


def _build_index_lines(rows, stealth):
    lines = []
    if not rows:
        return lines
    if stealth:
        lines.append(_style('-' * STEALTH_TABLE_WIDTH, _C_GRAY))
        for row in rows:
            name = truncate_name(row.get(F_NAME, ''))
            price = fmt_num(row.get(F_PRICE))
            change = fmt_num(row.get(F_CHG))
            pct = fmt_num(row.get(F_PCT_CHG))
            line = (
                pad_right(name, 8) + ' ' + pad_left(price, 10) + ' '
                + pad_left(f'{change} / {pct}%', 20)
            )
            lines.append(_style(line, _C_GRAY))
        return lines

    lines.append(_style('-' * NORMAL_TABLE_WIDTH, _C_GRAY))
    per_row = 5
    sep = ' | '
    sep_w = display_width(sep)
    boxes = []
    min_box_w = 0
    for row in rows:
        name = truncate_name(row.get(F_NAME, ''))
        price = fmt_num(row.get(F_PRICE))
        change = fmt_num(row.get(F_CHG))
        pct = fmt_num(row.get(F_PCT_CHG))
        right2 = f'{change} / {pct}%'
        boxes.append((name, price, right2))
        min_box_w = max(
            min_box_w,
            display_width(name) + display_width(price),
            display_width(right2),
        )
    box_w = (NORMAL_TABLE_WIDTH - (per_row - 1) * sep_w) // per_row
    if box_w < min_box_w:
        box_w = min_box_w

    for i in range(0, len(boxes), per_row):
        parts1 = []
        parts2 = []
        for j in range(per_row):
            if i + j >= len(boxes):
                break
            name, price, right2 = boxes[i + j]
            pad1 = box_w - display_width(name) - display_width(price)
            part1 = name + ' ' * max(0, pad1) + price
            pad2 = box_w - display_width(right2)
            part2 = ' ' * max(0, pad2) + right2
            parts1.append(part1)
            parts2.append(part2)
        line1 = sep.join(parts1)
        line2 = sep.join(parts2)
        line1 += ' ' * max(0, NORMAL_TABLE_WIDTH - display_width(line1))
        line2 += ' ' * max(0, NORMAL_TABLE_WIDTH - display_width(line2))
        lines.append(_style(line1, _C_WHITE))
        lines.append(_style(line2, _C_WHITE))
    return lines


def _build_market_total_line(index_rows, stealth):
    info = get_market_total_amounts(index_rows)
    if not info or info.get('total_yi') is None or info.get('ratio') is None:
        return None
    label = '三市总额/量比 '
    amount = str(info['total_yi'])
    value = f"{amount} / {info['ratio']}"
    width = STEALTH_TABLE_WIDTH if stealth else NORMAL_TABLE_WIDTH
    if stealth:
        target_slash_pos = 32
        spaces = target_slash_pos - display_width(label) - display_width(amount)
        if spaces < 0:
            spaces = 0
        line = label + ' ' * spaces + value
    else:
        line = label + value
    line = pad_right(line, width)
    return _style(line, _C_GRAY if stealth else _C_WHITE)


def build_button_bar(stealth, depth_mode=False):
    if depth_mode:
        buttons = (
            ('R refresh', 'R 刷新'),
            ('↑↓ switch', '↑↓ 切换'),
            ('← back', '← 返回'),
        )
    else:
        buttons = (
            ('R refresh', 'R 刷新'),
            ('C sort%', 'C 涨跌幅排序'),
            ('V sortVR', 'V 量比排序'),
            ('B hide', 'B 伪装'),
            ('S mode', 'S 切换'),
            ('Esc exit', 'Esc 退出'),
        )
    labels = [f'[{b[0] if stealth else b[1]}]' for b in buttons]
    text = ' '.join(labels)
    if stealth:
        return _style(text, _C_GRAY)
    return _style(text, '\033[30m', '\033[100m')


def _viewport(rows, cursor, term_height):
    """行数超过终端高度时，围绕光标开窗，避免整屏滚动。"""
    if not term_height or term_height <= 0:
        return rows, 0, len(rows), False
    limit = max(5, term_height - 10)
    if len(rows) <= limit:
        return rows, 0, len(rows), False
    start = min(max(0, cursor - limit // 2), len(rows) - limit)
    return rows[start:start + limit], start, start + limit, True


def build_list_lines(state, cfg, sort_state, cursor, term_height=40, include_buttons=True):
    stealth = cfg['stealth']
    interval = cfg['interval']
    stock_rows = state.stock_rows()
    apply_half_year_pct(stock_rows)
    display_rows = apply_sort(stock_rows, sort_state)

    status = f' | {state.status}' if state.status else ''
    if stealth:
        title = f'System Monitor  |  Data: {state.quote_time()} (Refresh: {interval}s){status}'
    else:
        title = (
            f'自选股行情 ({len(display_rows)} 只)    '
            f'行情时间: {state.quote_time()} ({interval} 秒自动刷新){status}'
        )
    window, start, end, clipped = _viewport(display_rows, cursor, term_height)
    if clipped:
        title += f'  [{start + 1}-{end}/{len(display_rows)}]'
    lines = [_style(title, _C_GRAY if stealth else _C_WHITE)]

    if not state.loaded and not display_rows:
        if state.status:
            lines.append(_style(f'  {state.status}', _C_GRAY if stealth else _C_WHITE))
        else:
            lines.append(_style('  正在加载行情数据...', _C_GRAY if stealth else _C_WHITE))
        if include_buttons:
            lines.append(_style('', ''))
            lines.append(build_button_bar(stealth, False))
        return lines

    if stealth:
        header = (
            ' ' + pad_left('Code', 7) + ' ' + pad_right('Name', 8) + ' '
            + pad_left('Chg%', 9) + ' ' + pad_left('Chg', 9) + ' '
            + pad_left('Last', 8) + ' ' + pad_left('VolR', 8) + ' '
            + pad_left('Turn%', 8)
        )
        lines.append(_style(header, _C_GRAY))
        lines.append(_style('-' * STEALTH_TABLE_WIDTH, _C_GRAY))
    else:
        header = (
            ' ' + pad_left('代码', 7) + ' ' + pad_right('名称', 8) + ' '
            + pad_left('涨跌幅', 8) + ' ' + pad_left('涨跌额', 8) + ' '
            + pad_left('最新', 7) + ' ' + pad_left('昨收', 7) + ' '
            + pad_left('今开', 8) + ' ' + pad_left('最高', 7) + ' '
            + pad_left('最低', 7) + ' ' + pad_left('量比', 7) + ' '
            + pad_left('换手%', 8) + ' ' + pad_left('成交额', 9) + ' '
            + pad_left('半年涨跌', 10)
        )
        lines.append(_style(header, _C_WHITE))
        lines.append(_style('-' * NORMAL_TABLE_WIDTH, _C_GRAY))

    for offset, row in enumerate(window):
        index = start + offset
        secid = _row_secid(row)
        stale = state.is_stale(secid, interval)
        has_cursor = index == cursor
        if stealth:
            lines.append(_style(_fmt_row_stealth(row, has_cursor, stale), _C_GRAY))
        else:
            bg = _C_BLACK_BG if index % 2 == 0 else _C_DARKGRAY_BG
            lines.append(_style(_fmt_row_normal(row, has_cursor, stale), _C_WHITE, bg))

    index_rows = state.index_rows()
    if stealth:
        rows_to_show = [
            r for r in index_rows
            if r.get(F_NAME) in ('上证指数', '深证成指')
        ]
    else:
        rows_to_show = index_rows if cfg['show_index'] else []

    if rows_to_show:
        lines.extend(_build_index_lines(rows_to_show, stealth))
        total_line = _build_market_total_line(index_rows, stealth)
        if total_line is not None:
            lines.append(total_line)

    if include_buttons:
        lines.append(_style('', ''))
        lines.append(build_button_bar(stealth, False))
    return lines


# ── 盘口详情（与 PowerShell 版布局一致）────────────────────

def _fmt_yi(value):
    try:
        return f'{float(value) / 1e8:.2f}'
    except (TypeError, ValueError):
        return '-'


def _fmt_cap(value):
    try:
        f = float(value)
        if f >= 1e12:
            return f'{f / 1e12:.3f}', '万亿'
        return f'{f / 1e8:.2f}', '亿'
    except (TypeError, ValueError):
        return '-', '亿'


def _fmt_latest_vol(value):
    try:
        return f'{abs(int(float(value))):,}'
    except (TypeError, ValueError):
        return '-'


def _fmt_depth_price(value, digits=2):
    return fmt_num(value, digits=digits)


def _fmt_depth_vol(value):
    try:
        return f'{int(value):,}'
    except (TypeError, ValueError):
        return '-'


def _left_line(label, value):
    label = str(label)
    value = str(value)
    return pad_right(label, 6) + ' ' + pad_right(value, 16)


def _right_line(label, price, volume):
    return (
        pad_right(str(label), 4) + ' '
        + pad_left(str(price), 10) + ' '
        + pad_left(str(volume), 12)
    )


def _safe_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt_price_pct(price, prev_close):
    try:
        p = float(price)
        pc = float(prev_close)
        if pc == 0:
            return '-'
        pct = (p - pc) / pc * 100
        sign = '+' if pct > 0 else ''
        return f'{sign}{pct:.2f}%'
    except (TypeError, ValueError):
        return '-'


def _fmt_avg_price(row, depth=None):
    try:
        depth = depth or {}
        amount = depth.get('amount')
        volume = depth.get('volume')
        if amount in _MISSING:
            amount = row.get(F_AMT)
        if volume in _MISSING:
            volume = row.get(F_VOL)
        amount = float(amount)
        volume = float(volume)
        if volume <= 0 or amount <= 0:
            return '-'
        digits = 3 if is_etf(row) else 2
        return fmt_num(amount / (volume * 100), digits=digits)
    except (TypeError, ValueError):
        return '-'


def _calc_seal_amount(depth, row):
    price = _safe_float(
        depth.get('price') if depth.get('price') is not None else row.get(F_PRICE)
    )
    limit_up = _safe_float(depth.get('limit_up'))
    limit_down = _safe_float(depth.get('limit_down'))
    if price is None:
        return None, None
    digits = 3 if is_etf(row) else 2
    eps = 10 ** (-digits - 1)
    bids = depth.get('bids', [])
    asks = depth.get('asks', [])
    if limit_up is not None and abs(price - limit_up) < eps:
        if bids and bids[0][0] is not None and bids[0][1] is not None:
            seal_price, seal_vol = bids[0]
            return '买一封板', seal_price * seal_vol * 100
    if limit_down is not None and abs(price - limit_down) < eps:
        if asks and asks[0][0] is not None and asks[0][1] is not None:
            seal_price, seal_vol = asks[0]
            return '卖一封板', seal_price * seal_vol * 100
    return None, None


def _build_depth_skeleton(row):
    return {
        'name': row.get(F_NAME, ''),
        'code': row.get(F_CODE, ''),
        'price': None,
        'prev_close': _safe_float(row.get(F_PREV_CLOSE)),
        'open': _safe_float(row.get(F_OPEN)),
        'high': _safe_float(row.get(F_HIGH)),
        'low': _safe_float(row.get(F_LOW)),
        'volume': None,
        'amount': None,
        'turnover': _safe_float(row.get(F_TURNOVER)),
        'change': _safe_float(row.get(F_CHG)),
        'pct_chg': _safe_float(row.get(F_PCT_CHG)),
        'limit_up': None,
        'limit_down': None,
        'asks': [],
        'bids': [],
    }


def _limit_ratio_for(row):
    name = str(row.get(F_NAME, '')).upper()
    code = str(row.get(F_CODE, ''))
    if 'ST' in name:
        return 0.05
    if code.startswith(('688', '300', '301')):
        return 0.20
    if code.startswith(('8', '4')):
        return 0.30
    return 0.10


def _depth_title(depth, row, stealth):
    name = depth.get('name') or row.get(F_NAME, '')
    code = depth.get('code') or row.get(F_CODE, '')
    price_digits = 3 if is_etf(row) else 2
    price_value = depth.get('price') if depth.get('price') is not None else row.get(F_PRICE)
    change_value = depth.get('change') if depth.get('change') is not None else row.get(F_CHG)
    pct_value = depth.get('pct_chg') if depth.get('pct_chg') is not None else row.get(F_PCT_CHG)
    price = fmt_num(price_value, digits=price_digits)
    change = fmt_num(change_value, digits=price_digits)
    pct = fmt_num(pct_value)
    latest_vol = _fmt_latest_vol(row.get(F_LATEST_VOL))
    try:
        change_num = float(change_value)
    except (TypeError, ValueError):
        change_num = 0
    if change_num > 0:
        arrow, color = '▲', (_C_GRAY if stealth else _C_RED)
    elif change_num < 0:
        arrow, color = '▼', (_C_GRAY if stealth else _C_GREEN)
    else:
        arrow, color = '─', (_C_GRAY if stealth else _C_WHITE)
    code_pad = pad_right(code, 6)
    name_pad = pad_right(name, 8)
    price_col = pad_left(price + ' ' + arrow, 10)
    change_col = pad_left(change, 9)
    pct_col = pad_left(pct + '%', 7)
    vol_col = pad_left(latest_vol, 11)
    return _style(
        f'{code_pad} {name_pad} {price_col} {change_col} {pct_col} {vol_col}',
        color,
    )


def build_depth_lines(state, cfg, depth, row, status='', include_buttons=True):
    stealth = cfg['stealth']
    normal = _C_GRAY if stealth else _C_WHITE
    sep_style = _C_GRAY
    if depth is None:
        depth = _build_depth_skeleton(row)

    price_digits = 3 if is_etf(row) else 2
    price = fmt_num(
        depth.get('price') if depth.get('price') is not None else row.get(F_PRICE),
        digits=price_digits,
    )
    prev_close = fmt_num(
        depth.get('prev_close') if depth.get('prev_close') is not None else row.get(F_PREV_CLOSE),
        digits=price_digits,
    )
    open_price = fmt_num(
        depth.get('open') if depth.get('open') is not None else row.get(F_OPEN),
        digits=price_digits,
    )
    high = fmt_num(
        depth.get('high') if depth.get('high') is not None else row.get(F_HIGH),
        digits=price_digits,
    )
    low = fmt_num(
        depth.get('low') if depth.get('low') is not None else row.get(F_LOW),
        digits=price_digits,
    )
    prev_close_raw = (
        depth.get('prev_close') if depth.get('prev_close') is not None
        else row.get(F_PREV_CLOSE)
    )
    high_pct = _fmt_price_pct(
        depth.get('high') if depth.get('high') is not None else row.get(F_HIGH),
        prev_close_raw,
    )
    low_pct = _fmt_price_pct(
        depth.get('low') if depth.get('low') is not None else row.get(F_LOW),
        prev_close_raw,
    )

    limit_up = _safe_float(depth.get('limit_up'))
    limit_down = _safe_float(depth.get('limit_down'))
    if limit_up is None or limit_down is None:
        base = _safe_float(prev_close_raw)
        if base is not None and base != 0:
            ratio = _limit_ratio_for(row)
            limit_up = round(base * (1 + ratio), 2)
            limit_down = round(base * (1 - ratio), 2)
    limit_up_str = fmt_num(limit_up, digits=price_digits)
    limit_down_str = fmt_num(limit_down, digits=price_digits)

    turnover = fmt_num(row.get(F_TURNOVER))
    vol_ratio = fmt_num(row.get(F_VOL_RATIO))
    amount = _fmt_yi(row.get(F_AMT))
    total_cap, total_cap_unit = _fmt_cap(row.get(F_TOTAL_CAP))
    float_cap, float_cap_unit = _fmt_cap(row.get(F_FLOAT_CAP))
    avg_price = _fmt_avg_price(row, depth)

    left_values = [
        avg_price,
        prev_close,
        open_price,
        high + ' ' * max(1, 16 - display_width(high) - display_width(high_pct)) + high_pct,
        low + ' ' * max(1, 16 - display_width(low) - display_width(low_pct)) + low_pct,
        limit_up_str,
        limit_down_str,
        '-' if turnover == '-' else f'{turnover}%',
        '-' if amount == '-' else f'{amount}亿',
        vol_ratio,
    ]
    left_labels = ['均价', '昨收', '今开', '最高', '最低', '涨停', '跌停', '换手', '成交', '量比']

    asks = list(depth.get('asks') or [])
    bids = list(depth.get('bids') or [])
    right_lines = []
    for i in range(5, 0, -1):
        pair = asks[5 - i] if len(asks) >= 5 - i + 1 else (None, None)
        right_lines.append(_right_line(
            f'卖{i}',
            _fmt_depth_price(pair[0], price_digits),
            _fmt_depth_vol(pair[1]),
        ))
    right_lines.append('-' * 28)
    for i in range(1, 6):
        pair = bids[i - 1] if len(bids) >= i else (None, None)
        right_lines.append(_right_line(
            f'买{i}',
            _fmt_depth_price(pair[0], price_digits),
            _fmt_depth_vol(pair[1]),
        ))

    has_depth = any(
        p is not None and v is not None
        for p, v in (asks + bids)
    )

    lines = [_depth_title(depth, row, stealth)]
    lines.append(_style('-' * 57, sep_style))
    if not has_depth:
        lines.append(_style('  暂无盘口数据', sep_style))

    for i, label in enumerate(left_labels):
        left = _left_line(label, left_values[i])
        right = right_lines[i] if has_depth and i < len(right_lines) else ''
        lines.append(_style(left + '    ' + right if right else left, normal))

    if status:
        lines.append(_style(f'  盘口刷新失败: {status}', sep_style))

    lines.append(_style('-' * 57, sep_style))

    secid = _row_secid(row)
    mas = get_daily_mas(secid)
    if mas:
        ma_labels = ['均线', '10日', '20日', '30日', '120日', '250日']
        ma_values = [
            fmt_num(mas.get('ma10'), digits=price_digits),
            fmt_num(mas.get('ma20'), digits=price_digits),
            fmt_num(mas.get('ma30'), digits=price_digits),
            fmt_num(mas.get('ma120'), digits=price_digits),
            fmt_num(mas.get('ma250'), digits=price_digits),
        ]
        col_width = 0
        for i, label in enumerate(ma_labels):
            width = display_width(label)
            if i > 0:
                width = max(width, display_width(ma_values[i - 1]))
            col_width = max(col_width, width)
        sep = 4
        while sep > 1 and (len(ma_labels) * col_width + (len(ma_labels) - 1) * sep) > 53:
            sep -= 1
        sep_str = ' ' * sep
        ma_header = sep_str.join(pad_right(label, col_width) for label in ma_labels)
        ma_cells = [''] + ma_values
        ma_line = sep_str.join(pad_left(cell, col_width) for cell in ma_cells)
        lines.append(_style(ma_header, normal))
        lines.append(_style(ma_line, normal))
        lines.append(_style('-' * 57, sep_style))

    pepb = get_pepb(secid)
    if pepb:
        pe_display = '-' if pepb.get('pe') is not None and pepb['pe'] < 0 else fmt_num(pepb.get('pe'))
        line1 = (
            pad_right('市值', 6) + ' ' + pad_left(total_cap + total_cap_unit, 14)
            + '        ' + pad_right('PE', 6) + ' ' + pad_left(pe_display, 14)
        )
        line2 = (
            pad_right('流通', 6) + ' ' + pad_left(float_cap + float_cap_unit, 14)
            + '        ' + pad_right('PB', 6) + ' ' + pad_left(fmt_num(pepb.get('pb')), 14)
        )
        lines.append(_style(line1, normal))
        lines.append(_style(line2, normal))
    else:
        line = (
            pad_right('市值', 6) + ' ' + pad_left(total_cap + total_cap_unit, 14)
            + '        ' + pad_right('流通', 6) + ' ' + pad_left(float_cap + float_cap_unit, 14)
        )
        lines.append(_style(line, normal))
    lines.append(_style('', normal))

    seal_label, seal_amount = _calc_seal_amount(depth, row)
    if seal_label and seal_amount is not None:
        lines.append(_style(f'{seal_label}: {_fmt_yi(seal_amount)}亿', normal))

    fetched_at = depth.get('_fetched_at') or '等待后台刷新'
    lines.append(_style(f'盘口数据时间: {fetched_at}', sep_style))
    lines.append(_style('', normal))
    if include_buttons:
        lines.append(build_button_bar(stealth, True))
    return lines


# ── 老板键伪装页 ───────────────────────────────────────────

def build_boss_lines():
    t = time.strftime('%H:%M:%S')
    lines = [
        '> npm run build',
        '',
        '> project@1.0.0 build D:\\project',
        '',
        '> tsc && vite build',
        '',
        'vite v5.0.0 building for production...',
        '[12:34:56] 128 modules transformed.',
        'dist/index.html                   0.45 kB',
        'dist/assets/index-a1b2c3d4.js   142.31 kB',
        '[12:34:56] built in 3.42s',
        '',
        f'[{t}] Watching for changes...',
    ]
    return [_style(line, _C_GRAY) for line in lines]


# ── 主流程 ─────────────────────────────────────────────────

def _print_once(cfg):
    stocks, indices = load_watchlist()
    stock_ids = search_secids(stocks)
    index_ids = search_secids(indices)
    if not stock_ids and not index_ids:
        print('没有可查询的股票或指数（检查 watchlist.json 或网络）', file=sys.stderr)
        return 1
    try:
        rows = fetch_quote_rows(stock_ids + index_ids)
    except Exception as e:
        print(f'行情获取失败: {e}', file=sys.stderr)
        return 1

    state = AppState()
    index_set = set(index_ids)
    stock_rows = []
    index_rows = []
    for row in rows:
        if _row_secid(row) in index_set:
            index_rows.append(row)
        else:
            stock_rows.append(row)
    state.stock_ids = stock_ids
    state.index_ids = index_ids
    state.merge({
        'stock_ids': stock_ids,
        'index_ids': index_ids,
        'stocks': stock_rows,
        'indices': index_rows,
        'missing_stock': max(0, len(stocks) - len(stock_ids)),
        'missing_index': max(0, len(indices) - len(index_ids)),
    })

    if cfg['fetch_hy'] and stock_ids:
        if start_half_year_update(stock_ids):
            deadline = time.time() + 40
            while time.time() < deadline:
                if complete_half_year_update():
                    break
                time.sleep(0.1)
    apply_half_year_pct(state.stock_rows())

    if index_rows:
        warm_market_total_cache()

    lines = build_list_lines(
        state, cfg, {'c': 0, 'v': 0}, cursor=-1,
        term_height=0, include_buttons=False,
    )
    print_lines_plain(lines)
    return 0


def _install_signals():
    if os.name == 'nt':
        return

    def _handler(signum, frame):
        raise SystemExit(0)

    for sig in (getattr(signal, 'SIGTERM', None), getattr(signal, 'SIGHUP', None)):
        if sig is not None:
            try:
                signal.signal(sig, _handler)
            except Exception:
                pass


def _run_interactive(cfg):
    state = AppState()
    list_worker = Worker()
    depth_worker = Worker()
    market_worker = Worker()
    depth_cache = {}
    current_depth = None
    current_row = None
    depth_rows = []
    depth_index = 0
    depth_status = ''
    sort_state = {'c': 0, 'v': 0}
    cursor = 0
    in_depth = False
    boss_mode = False
    next_list = 0.0
    next_depth = 0.0
    next_hy = 0.0
    next_market = 0.0
    next_boss_draw = 0.0
    dirty = True

    if not is_tty():
        print('错误: 交互模式需要在终端中运行；如需取一次数据请加 --once', file=sys.stderr)
        return 2

    restored = enter_raw_mode()
    if not restored and os.name != 'nt':
        print('错误: 当前终端不支持交互模式；请使用 --once', file=sys.stderr)
        return 2

    set_title('System' if cfg['stealth'] else '行情监控')
    clear_screen()

    try:
        list_worker.start(_fetch_list)
        next_list = time.monotonic() + cfg['interval']

        while True:
            now = time.monotonic()

            taken = list_worker.take()
            if taken is not None:
                data, err = taken
                if err is not None:
                    if state.loaded:
                        state.status = f'行情获取失败，显示缓存数据: {err}'
                    else:
                        state.status = f'行情获取失败: {err}'
                else:
                    state.merge(data)
                next_list = now + cfg['interval']
                dirty = True

            taken = depth_worker.take()
            if taken is not None:
                data, err = taken
                if err is not None:
                    depth_status = str(err)
                else:
                    secid = data.get('_secid')
                    if secid:
                        depth_cache[secid] = data
                    if current_row is not None and secid == _row_secid(current_row):
                        current_depth = data
                        depth_status = ''
                dirty = True

            taken = market_worker.take()
            if taken is not None:
                dirty = True

            if complete_half_year_update():
                dirty = True
            if complete_ma_update() and in_depth:
                dirty = True
            if complete_pepb_update() and in_depth:
                dirty = True

            if not boss_mode and not in_depth:
                if not list_worker.running() and now >= next_list:
                    list_worker.start(_fetch_list)
                    next_list = now + cfg['interval']

            if in_depth and not boss_mode:
                if current_row is not None:
                    secid = _row_secid(current_row)

                    def fetch_depth(secid=secid):
                        data = fetch_depth_data(secid, retries=2)
                        if data is None:
                            raise RuntimeError('盘口数据获取失败')
                        data['_secid'] = secid
                        data['_fetched_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
                        return data

                    if not depth_worker.running() and now >= next_depth:
                        depth_worker.start(fetch_depth)
                        next_depth = now + 10

            if cfg['fetch_hy'] and state.stock_ids and now >= next_hy:
                visible = state.stock_rows()
                apply_half_year_pct(visible)
                if start_half_year_update([_row_secid(r) for r in visible]):
                    next_hy = now + cfg['interval'] * 4
                else:
                    next_hy = now + cfg['interval']

            if state.index_rows() and now >= next_market and not market_worker.running():
                market_worker.start(warm_market_total_cache)
                next_market = now + 60

            if boss_mode and now >= next_boss_draw:
                dirty = True
                next_boss_draw = now + 1

            if dirty:
                if boss_mode:
                    lines = build_boss_lines()
                elif in_depth:
                    lines = build_depth_lines(
                        state, cfg, current_depth, current_row, depth_status
                    )
                else:
                    _, term_height = _term_size()
                    display_rows = apply_sort(state.stock_rows(), sort_state)
                    if display_rows:
                        cursor = min(max(0, cursor), len(display_rows) - 1)
                    lines = build_list_lines(
                        state, cfg, sort_state, cursor,
                        term_height=term_height, include_buttons=True,
                    )
                draw_frame(lines)
                dirty = False

            key = get_key(0.05)
            if key is None:
                continue

            if key in ('ESC', 'CTRL_C'):
                if in_depth:
                    in_depth = False
                    current_depth = None
                    current_row = None
                    depth_rows = []
                    depth_status = ''
                    dirty = True
                else:
                    break
                continue

            if key == 'LEFT':
                if in_depth:
                    in_depth = False
                    current_depth = None
                    current_row = None
                    depth_rows = []
                    depth_status = ''
                    dirty = True
                continue

            if key in ('r', 'R'):
                if in_depth:
                    if current_row is not None and not depth_worker.running():
                        secid = _row_secid(current_row)

                        def refresh_depth(secid=secid):
                            data = fetch_depth_data(secid, retries=2)
                            if data is None:
                                raise RuntimeError('盘口数据获取失败')
                            data['_secid'] = secid
                            data['_fetched_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
                            return data

                        depth_worker.start(refresh_depth)
                        next_depth = time.monotonic() + 10
                else:
                    if not list_worker.running():
                        list_worker.start(_fetch_list)
                        next_list = time.monotonic() + cfg['interval']
                dirty = True
                continue

            if key in ('ENTER', 'RIGHT'):
                if not in_depth:
                    display_rows = apply_sort(state.stock_rows(), sort_state)
                    if display_rows:
                        depth_rows = list(display_rows)
                        depth_index = min(cursor, len(depth_rows) - 1)
                        current_row = depth_rows[depth_index]
                        current_depth = depth_cache.get(_row_secid(current_row))
                        if current_depth is None:
                            current_depth = _build_depth_skeleton(current_row)
                        in_depth = True
                        depth_status = ''
                        next_depth = 0.0
                dirty = True
                continue

            if key == 'UP':
                if in_depth and depth_rows:
                    depth_index = (depth_index - 1) % len(depth_rows)
                    current_row = depth_rows[depth_index]
                    current_depth = depth_cache.get(_row_secid(current_row))
                    if current_depth is None:
                        current_depth = _build_depth_skeleton(current_row)
                    depth_status = ''
                    next_depth = 0.0
                    dirty = True
                elif not in_depth:
                    display_rows = apply_sort(state.stock_rows(), sort_state)
                    if display_rows:
                        cursor = (cursor - 1) % len(display_rows)
                        dirty = True
                continue

            if key == 'DOWN':
                if in_depth and depth_rows:
                    depth_index = (depth_index + 1) % len(depth_rows)
                    current_row = depth_rows[depth_index]
                    current_depth = depth_cache.get(_row_secid(current_row))
                    if current_depth is None:
                        current_depth = _build_depth_skeleton(current_row)
                    depth_status = ''
                    next_depth = 0.0
                    dirty = True
                elif not in_depth:
                    display_rows = apply_sort(state.stock_rows(), sort_state)
                    if display_rows:
                        cursor = (cursor + 1) % len(display_rows)
                        dirty = True
                continue

            if key in ('c', 'C') and not in_depth:
                sort_state['c'] = 1 if sort_state['c'] == 0 else 0
                sort_state['v'] = 0
                dirty = True
                continue

            if key in ('v', 'V') and not in_depth:
                sort_state['v'] = 1 if sort_state['v'] == 0 else 0
                sort_state['c'] = 0
                dirty = True
                continue

            if key in ('s', 'S') and not in_depth:
                cfg['stealth'] = not cfg['stealth']
                cfg['show_index'] = not cfg['show_index']
                set_title('System' if cfg['stealth'] else '行情监控')
                dirty = True
                continue

            if key in ('b', 'B', 'F9'):
                boss_mode = not boss_mode
                if boss_mode:
                    set_title('System' if cfg['stealth'] else '行情监控')
                dirty = True
                continue

    finally:
        restore_terminal()
        _show_cursor()
        _restore_windows_console()
        clear_screen()
        print('已退出')


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    _init_output()
    _install_signals()
    try:
        cfg = parse_args(argv)
    except UsageError as e:
        return _usage_error(str(e))

    if cfg['help']:
        print_help()
        return 0

    try:
        if cfg['once']:
            return _print_once(cfg)
        return _run_interactive(cfg)
    except KeyboardInterrupt:
        return 0
    except BrokenPipeError:
        return 0
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        return 1
    finally:
        restore_terminal()
        _restore_windows_console()


if __name__ == '__main__':
    sys.exit(main())
