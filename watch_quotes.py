"""
股票行情监视器（Python 版）
用法:
  python watch_quotes.py [刷新秒数] [--index on|off] [--once] [-h]

快捷键:
  ↑/↓      移动光标选择股票
  Enter/→  查看选中股票的盘口详情
  R        立即刷新
  C        按涨跌幅排序（第一次高到低，第二次恢复原顺序）
  V        按量比排序（第一次高到低，第二次恢复原顺序）
  ←/Esc    返回或退出
  Ctrl+C   强制退出
"""

import json
import os
import sys
import threading
import time
import traceback

from quotes_core import (
    BASE_DIR, F_CODE, F_MARKET, F_NAME, F_PRICE, F_PCT_CHG, F_CHG,
    F_VOL, F_AMT, F_TURNOVER, F_VOL_RATIO, F_HIGH, F_LOW, F_OPEN, F_PREV_CLOSE,
    F_TOTAL_CAP, F_FLOAT_CAP, F_PE, F_PB, F_LATEST_VOL,
    search_secids, fetch_quote_rows, get_key, truncate_name, is_etf,
    fmt_num, fmt_open_price, display_width, pad_center, pad_left, pad_right,
    print_indices, get_market_total_amounts, print_market_total, fetch_depth_data,
    warm_market_total_cache,
)


def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')


def get_quotes():
    wl_path = os.path.join(BASE_DIR, 'watchlist.json')
    with open(wl_path, 'r', encoding='utf-8') as f:
        wl = json.load(f)
    stocks = wl.get('stocks', [])
    indices = wl.get('indices', [])

    stock_secids = search_secids(stocks)
    index_secids = search_secids(indices)

    if not stock_secids and not index_secids:
        return None, None, None, '没有可查询的股票或指数'

    try:
        stock_rows = fetch_quote_rows(stock_secids)
        index_rows = fetch_quote_rows(index_secids)
    except Exception as e:
        return [], [], None, f'行情获取失败: {e}'
    return stock_rows, index_rows, None, None


def _fmt_row(r):
    """格式化单个股票行为字符串。"""
    code = r.get(F_CODE, '')
    name = truncate_name(r.get(F_NAME, ''))
    market = '深A' if str(r.get(F_MARKET, '')) == '0' else '沪A'
    price_digits = 3 if is_etf(r) else 2
    price = fmt_num(r.get(F_PRICE), digits=price_digits)
    prev_close = fmt_num(r.get(F_PREV_CLOSE), digits=price_digits)
    open_price, open_arrow = fmt_open_price(
        r.get(F_OPEN), r.get(F_PREV_CLOSE), digits=price_digits
    )
    high = fmt_num(r.get(F_HIGH), digits=price_digits)
    low = fmt_num(r.get(F_LOW), digits=price_digits)
    change = fmt_num(r.get(F_CHG), digits=price_digits)
    pct = fmt_num(r.get(F_PCT_CHG))
    turnover = fmt_num(r.get(F_TURNOVER))
    vr = fmt_num(r.get(F_VOL_RATIO))
    vol = fmt_num(r.get(F_VOL), digits=0)
    amt = fmt_num(r.get(F_AMT), divisor=100000000)
    return (
        f"{pad_right(name, 10)} {pad_right(code, 8)} {pad_right(market, 5)} "
        f"{pad_left(price, 8)} {pad_left(prev_close, 8)} "
        f"{pad_left(open_price, 8)}{open_arrow} {pad_left(high, 8)} {pad_left(low, 8)} "
        f"{pad_left(change, 9)} {pad_left(pct + '%', 9)} {pad_left(turnover, 8)} "
        f"{pad_left(vr, 8)} {pad_left(vol, 12)} {pad_left(amt, 9)}"
    )


def _numeric_key(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def apply_sort(rows, sort_state):
    """按当前排序状态对行进行排序，不改变原始顺序。"""
    if sort_state['c'] != 0:
        field = F_PCT_CHG
        reverse = sort_state['c'] == 1
    elif sort_state['v'] != 0:
        field = F_VOL_RATIO
        reverse = sort_state['v'] == 1
    else:
        return rows

    def key(r):
        v = _numeric_key(r.get(field))
        if v is None:
            return float('-inf') if reverse else float('inf')
        return v

    return sorted(rows, key=key, reverse=reverse)


def sort_hint(sort_state):
    """返回当前排序状态提示文本。"""
    parts = []
    if sort_state['c'] == 1:
        parts.append('C=涨跌幅↓')
    elif sort_state['c'] == 2:
        parts.append('C=涨跌幅↑')
    if sort_state['v'] == 1:
        parts.append('V=量比↓')
    elif sort_state['v'] == 2:
        parts.append('V=量比↑')
    return ' '.join(parts)


def print_stocks(rows, highlight_idx=-1):
    """打印自选股列表，支持光标高亮。"""
    header = (
        f"{pad_right('名称', 10)} {pad_right('代码', 8)} {pad_right('市场', 5)} "
        f"{pad_left('最新', 8)} {pad_left('昨收', 8)} {pad_left('今开', 9)} "
        f"{pad_left('最高', 8)} {pad_left('最低', 8)} {pad_left('涨跌额', 9)} "
        f"{pad_left('涨跌幅', 9)} {pad_left('换手%', 8)} {pad_left('量比', 8)} "
        f"{pad_left('成交量(手)', 12)} {pad_left('成交亿', 9)}"
    )
    print('  ' + header)
    print('-' * 134)
    for i, r in enumerate(rows):
        prefix = '> ' if i == highlight_idx else '  '
        print(prefix + _fmt_row(r))


def print_quotes(stock_rows, index_rows, show_index=True, highlight_idx=-1, data_time=None, status=''):
    now = data_time or time.strftime('%Y-%m-%d %H:%M:%S')
    suffix = f'    {status}' if status else ''
    print(f"自选股行情 ({len(stock_rows)} 只)    行情时间: {now}{suffix}")
    if stock_rows:
        print_stocks(stock_rows, highlight_idx=highlight_idx)
    if show_index and index_rows:
        print_indices(index_rows)
        market_info = get_market_total_amounts(index_rows)
        print_market_total(market_info)


def _fmt_yi(v):
    """把金额格式化为 亿，返回纯数值（单位在标签中）。"""
    try:
        return f"{float(v)/1e8:.2f}"
    except (TypeError, ValueError):
        return '-'


def _fmt_cap(v):
    """把市值格式化为 亿 或 万亿：默认用亿，>=1万亿时切换为万亿并保留3位小数。"""
    try:
        f = float(v)
        if f >= 1e12:
            return f"{f/1e12:.3f}", '万亿'
        return f"{f/1e8:.2f}", '亿'
    except (TypeError, ValueError):
        return '-', '亿'


def _fmt_depth_price(v, digits=2):
    return fmt_num(v, digits=digits)


def _fmt_depth_vol(v):
    try:
        return f"{int(v):,}"
    except (TypeError, ValueError):
        return '-'


def _fmt_latest_vol(v):
    try:
        return f"{abs(int(float(v))):,}"
    except (TypeError, ValueError):
        return '-'


def _row_to_secid(row):
    """把行情行转换为东方财富 secid。"""
    market = str(row.get(F_MARKET, '0'))
    code = row.get(F_CODE, '')
    return f"{market}.{code}"


def _left_line(label, value, width=20):
    """左侧信息行：标签左对齐、数值右对齐，总显示宽度固定。"""
    label = str(label)
    value = str(value)
    label_w = display_width(label)
    value_w = display_width(value)
    # 标签占 6 显示宽度（3 个汉字），数值占 14 显示宽度
    label_pad = label + ' ' * max(0, 6 - label_w)
    value_pad = ' ' * max(0, 14 - value_w) + value
    return label_pad + ' ' + value_pad


def _left_line_compact(label, value, value_width=16):
    """左侧信息行：标签区固定 6 显示宽，后空 3 格，数值区固定宽度左对齐。"""
    label = str(label)
    value = str(value)
    label_w = display_width(label)
    label_pad = label + ' ' * max(0, 6 - label_w)
    value_pad = pad_right(value, value_width)
    return label_pad + '   ' + value_pad


def _fmt_price_with_pct(price, pct, total_width=16, min_gap=3):
    """把价格与涨跌幅组合成固定总宽度，中间至少 min_gap 个空格。"""
    price = str(price)
    pct = str(pct)
    price_w = display_width(price)
    pct_w = display_width(pct)
    available = total_width - price_w - pct_w
    gap = min_gap if available >= min_gap else max(1, available)
    return price + ' ' * gap + pct


def _right_line(label, price, vol):
    """右侧盘口行：档位左对齐，价格和数量右对齐。"""
    label = str(label)
    price = str(price)
    vol = str(vol)
    label_pad = label + ' ' * max(0, 4 - display_width(label))
    price_pad = ' ' * max(0, 10 - display_width(price)) + price
    vol_pad = ' ' * max(0, 12 - display_width(vol)) + vol
    return f"{label_pad} {price_pad} {vol_pad}"


def _safe_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _fmt_price_pct(price, prev_close):
    """计算某价格相对昨收的涨跌幅百分比。"""
    try:
        p = float(price)
        pc = float(prev_close)
        if pc == 0:
            return '-'
        pct = (p - pc) / pc * 100
        sign = '+' if pct > 0 else ''
        return f"{sign}{pct:.2f}%"
    except (TypeError, ValueError):
        return '-'


def _calc_seal_amount(depth, row):
    """若当前价格触及涨停/跌停，计算并返回封板金额（元）。"""
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
    """用列表行情行构造盘口骨架。

    名称、昨收、今开、最高、最低等静态字段可立即显示；
    五档盘口等动态字段留空，等待后台异步刷新。
    """
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


def _fmt_depth_title(depth, row):
    """格式化盘口窗口标题行：代码 名称 当前价格▲▼ 涨跌额 涨跌幅 现手。"""
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
        c = float(depth.get('change') if depth.get('change') is not None else row.get(F_CHG))
        arrow = '▲' if c > 0 else '▼' if c < 0 else '─'
    except (TypeError, ValueError):
        arrow = '─'
    code_pad = pad_right(code, 6)
    name_pad = pad_right(name, 8)
    price_part = price + ' ' + arrow
    price_col = pad_left(price_part, 10)
    change_col = pad_left(change, 9)
    pct_col = pad_left(pct + '%', 7)
    vol_col = pad_left(latest_vol, 11)
    return f"{code_pad} {name_pad} {price_col} {change_col} {pct_col} {vol_col}"


def _fmt_avg_price(row):
    """计算分时均价 = 成交额 / 成交量(股)。"""
    try:
        amt = float(row.get(F_AMT, 0))
        vol = float(row.get(F_VOL, 0))
        if vol <= 0 or amt <= 0:
            return '-'
        digits = 3 if is_etf(row) else 2
        return fmt_num(amt / (vol * 100), digits=digits)
    except (TypeError, ValueError):
        return '-'


def print_depth(depth, row):
    """打印单只股票盘口详情窗口。depth 可能是完整数据，也可能是仅含静态行情的骨架。"""
    if depth is None:
        depth = {}

    print(_fmt_depth_title(depth, row))

    price_digits = 3 if is_etf(row) else 2
    price = fmt_num(depth.get('price') if depth.get('price') is not None else row.get(F_PRICE), digits=price_digits)
    prev_close = fmt_num(depth.get('prev_close') if depth.get('prev_close') is not None else row.get(F_PREV_CLOSE), digits=price_digits)
    open_price = fmt_num(depth.get('open') if depth.get('open') is not None else row.get(F_OPEN), digits=price_digits)
    high = fmt_num(depth.get('high') if depth.get('high') is not None else row.get(F_HIGH), digits=price_digits)
    low = fmt_num(depth.get('low') if depth.get('low') is not None else row.get(F_LOW), digits=price_digits)
    prev_close_raw = depth.get('prev_close') if depth.get('prev_close') is not None else row.get(F_PREV_CLOSE)
    high_pct = _fmt_price_pct(depth.get('high') if depth.get('high') is not None else row.get(F_HIGH), prev_close_raw)
    low_pct = _fmt_price_pct(depth.get('low') if depth.get('low') is not None else row.get(F_LOW), prev_close_raw)
    limit_up = fmt_num(depth.get('limit_up'), digits=price_digits)
    limit_down = fmt_num(depth.get('limit_down'), digits=price_digits)

    turnover = fmt_num(row.get(F_TURNOVER))
    vol_ratio = fmt_num(row.get(F_VOL_RATIO))
    amount = _fmt_yi(row.get(F_AMT))
    total_cap, total_cap_unit = _fmt_cap(row.get(F_TOTAL_CAP))
    float_cap, float_cap_unit = _fmt_cap(row.get(F_FLOAT_CAP))
    latest_vol = _fmt_latest_vol(row.get(F_LATEST_VOL))
    avg_price = _fmt_avg_price(row)
    pe = fmt_num(row.get(F_PE))
    pb = fmt_num(row.get(F_PB))

    main_sep = '-' * 57
    print(main_sep)

    left_lines = [
        _left_line_compact('最新', price),
        _left_line_compact('均价', avg_price),
        _left_line_compact('昨收', prev_close),
        _left_line_compact('今开', open_price),
        _left_line_compact('最高', _fmt_price_with_pct(high, high_pct)),
        _left_line_compact('最低', _fmt_price_with_pct(low, low_pct)),
        _left_line_compact('涨停', limit_up),
        _left_line_compact('跌停', limit_down),
        _left_line_compact('换手', turnover + '%'),
        _left_line_compact('成交', amount + '亿'),
        _left_line_compact('量比', vol_ratio),
    ]

    asks = depth.get('asks', [])
    bids = depth.get('bids', [])
    right_lines = []
    for i in range(5, 0, -1):
        price, vol = asks[5 - i] if len(asks) >= i else (None, None)
        right_lines.append(
            _right_line(f"卖{i}", _fmt_depth_price(price), _fmt_depth_vol(vol))
        )
    right_lines.append('-' * 28)
    for i in range(1, 6):
        price, vol = bids[i - 1] if len(bids) >= i else (None, None)
        right_lines.append(
            _right_line(f"买{i}", _fmt_depth_price(price), _fmt_depth_vol(vol))
        )

    for i in range(len(left_lines)):
        left = left_lines[i]
        right = right_lines[i] if i < len(right_lines) else ''
        print(f"{left}    {right}")

    print(main_sep)

    def _bottom_pair(left_label, left_val, right_label, right_val):
        left = _left_line_compact(left_label, left_val)
        right = _left_line_compact(right_label, right_val)
        left_w = display_width(left)
        pad = max(1, 20 - left_w)
        return left + ' ' * pad + right

    print(_bottom_pair('市值', total_cap + total_cap_unit, 'PE', pe))
    print(_bottom_pair('流通', float_cap + float_cap_unit, 'PB', pb))

    seal_label, seal_amount = _calc_seal_amount(depth, row)
    if seal_label and seal_amount is not None:
        print(f"{seal_label}: {_fmt_yi(seal_amount)}亿")

    print(f"盘口数据时间: {depth.get('_fetched_at', '等待后台刷新')}")
    print()
    print("[Enter/Esc 返回  R 刷新]")


def wait_for_key_or_timeout(interval):
    """轮询等待 interval 秒或直到有按键，返回按键字符串或 None。

    以 0.1 秒为粒度轮询，既保证响应速度又不过分消耗 CPU。
    """
    elapsed = 0.0
    step = 0.1
    while elapsed < interval:
        key = get_key()
        if key is not None:
            return key
        time.sleep(step)
        elapsed += step
    return None


def main():
    if '--help' in sys.argv or '-h' in sys.argv:
        print_help()
        return

    interval = 3
    show_index = True
    once = False
    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg in ('--index', '-index'):
            i += 1
            if i >= len(sys.argv):
                raise ValueError('--index 需要 on 或 off')
            value = sys.argv[i].lower()
            if value in ('on', 'true', '1'):
                show_index = True
            elif value in ('off', 'false', '0'):
                show_index = False
            else:
                raise ValueError(f'无效的 --index 参数: {sys.argv[i]}')
        elif arg == '--once':
            once = True
        else:
            try:
                parsed = int(arg)
            except ValueError:
                raise ValueError(f'无法识别的参数: {arg}') from None
            if parsed <= 0:
                raise ValueError('刷新秒数必须大于 0')
            interval = parsed
        i += 1

    if once:
        stock_rows, index_rows, err, _ = get_quotes()
        if err:
            print(err)
            sys.exit(1)
        warm_market_total_cache()
        print_quotes(stock_rows, index_rows, show_index)
        return

    # ── 交互模式 ──
    stock_rows = []
    index_rows = []
    sort_state = {'c': 0, 'v': 0}  # 0=原样, 1=从高到低
    selected_idx = 0
    in_depth = False
    current_depth = None
    current_depth_row = None
    depth_rows = []          # 盘口模式下可切换的股票列表
    depth_idx = 0            # 当前盘口股票在 depth_rows 中的索引
    depth_fetch_result = {'depth': None, 'done': False, 'error': None}
    depth_fetch_thread = None
    depth_fetch_generation = 0
    depth_cache = {}         # secid -> 五档数据，切换股票时立即填充，后台拉到新数据再刷新
    list_fetch_result = {'stocks': None, 'indices': None, 'done': False, 'error': None}
    list_fetch_thread = None
    list_fetch_generation = 0
    list_status = ''
    last_quote_success = None

    def start_async_list_fetch():
        """启动后台线程刷新列表行情，避免阻塞按键响应。"""
        nonlocal list_fetch_thread, list_fetch_generation, list_status
        list_fetch_generation += 1
        gen = list_fetch_generation
        list_fetch_result['done'] = False
        list_fetch_result['error'] = None
        list_status = '正在刷新...'

        def fetcher():
            nonlocal list_status, last_quote_success
            try:
                s_rows, i_rows, err, _ = get_quotes()
            except Exception as e:
                s_rows, i_rows, err = None, None, f'行情获取失败: {e}'
            if gen == list_fetch_generation:
                list_fetch_result['stocks'] = s_rows
                list_fetch_result['indices'] = i_rows
                list_fetch_result['error'] = err
                list_fetch_result['done'] = True
                if err:
                    list_status = err + '，显示缓存数据'
                else:
                    last_quote_success = time.strftime('%Y-%m-%d %H:%M:%S')
                    list_status = ''
            if not err:
                warm_market_total_cache()

        list_fetch_thread = threading.Thread(target=fetcher, daemon=True)
        list_fetch_thread.start()

    def is_fetching_list():
        return list_fetch_thread is not None and list_fetch_thread.is_alive()

    def apply_list_result():
        """把后台取回的列表数据合并到当前显示，返回错误字符串或 None。"""
        nonlocal stock_rows, index_rows
        err = list_fetch_result['error']
        if not err and list_fetch_result['stocks'] is not None:
            stock_rows = list_fetch_result['stocks'] or []
            index_rows = list_fetch_result['indices'] or []
        list_fetch_result['done'] = False
        return err

    def start_async_depth_fetch():
        """启动后台线程获取当前盘口股票的五档数据。"""
        nonlocal depth_fetch_thread, depth_fetch_generation
        if current_depth_row is None:
            return
        depth_fetch_generation += 1
        gen = depth_fetch_generation
        depth_fetch_result['done'] = False
        depth_fetch_result['depth'] = None
        depth_fetch_result['error'] = None
        row = current_depth_row

        def fetcher():
            secid = _row_to_secid(row)
            try:
                depth = fetch_depth_data(secid, retries=2)
                error = None
            except Exception as e:
                depth = None
                error = str(e)
            # 只有最新一次请求的结果才生效，避免旧线程覆盖当前股票
            if gen == depth_fetch_generation:
                if depth is not None:
                    depth['_fetched_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
                    depth_cache[secid] = depth
                depth_fetch_result['depth'] = depth
                depth_fetch_result['error'] = error
                depth_fetch_result['done'] = True

        depth_fetch_thread = threading.Thread(target=fetcher, daemon=True)
        depth_fetch_thread.start()

    def is_fetching_depth():
        return depth_fetch_thread is not None and depth_fetch_thread.is_alive()

    def _depth_for_row(row):
        """取该股票的五档数据：缓存命中直接填充显示，否则先用骨架占位。"""
        cached = depth_cache.get(_row_to_secid(row))
        return cached if cached is not None else _build_depth_skeleton(row)

    clear_screen()
    try:
        # 首次加载（后台获取，不阻塞按键，期间可 Esc 退出）
        start_async_list_fetch()
        clear_screen()
        print("正在加载行情数据...")
        while not list_fetch_result['done']:
            if wait_for_key_or_timeout(0.1) == 'ESC':
                return
        err = apply_list_result()
        if err:
            print(err)
            return

        needs_redraw = True
        while True:
            display_rows = apply_sort(stock_rows, sort_state)
            if needs_redraw:
                clear_screen()
                if in_depth:
                    print_depth(current_depth, current_depth_row)
                else:
                    if selected_idx >= len(display_rows):
                        selected_idx = max(0, len(display_rows) - 1)
                    print_quotes(
                        display_rows, index_rows, show_index, highlight_idx=selected_idx,
                        data_time=last_quote_success, status=list_status,
                    )
                    hint = sort_hint(sort_state)
                    print(f"\n[↑↓ 选择  Enter/→ 盘口  R 刷新  C 涨跌幅  V 量比  Esc 退出] {hint}")
                needs_redraw = False

            # 等待期间保持原帧，数据到达或状态变化后才重绘
            wait_interval = 0.1 if in_depth and is_fetching_depth() else interval
            key = wait_for_key_or_timeout(wait_interval)

            if key is None:
                if in_depth and depth_fetch_result['done']:
                    if depth_fetch_result['depth'] is not None:
                        current_depth = depth_fetch_result['depth']
                    depth_fetch_result['done'] = False
                    needs_redraw = True
                    continue
                if not in_depth and list_fetch_result['done']:
                    apply_list_result()
                    needs_redraw = True
                    continue

            if key is None:
                if in_depth:
                    if not is_fetching_depth():
                        start_async_depth_fetch()
                        needs_redraw = True
                else:
                    if not is_fetching_list():
                        start_async_list_fetch()
                        needs_redraw = True
                continue

            # ── 按键处理 ──
            if key == 'ESC':
                if in_depth:
                    in_depth = False
                    current_depth = None
                    current_depth_row = None
                    depth_rows = []
                    depth_idx = 0
                    depth_fetch_generation += 1  # 让旧线程结果失效
                else:
                    break

            elif key == 'LEFT':
                if in_depth:
                    in_depth = False
                    current_depth = None
                    current_depth_row = None
                    depth_rows = []
                    depth_idx = 0
                    depth_fetch_generation += 1

            elif key in ('r', 'R'):
                if in_depth:
                    if not is_fetching_depth():
                        start_async_depth_fetch()
                else:
                    if not is_fetching_list():
                        start_async_list_fetch()
                needs_redraw = True
                continue

            elif key in ('ENTER', 'RIGHT'):
                if not in_depth and 0 <= selected_idx < len(display_rows):
                    depth_rows = list(display_rows)
                    depth_idx = selected_idx
                    current_depth_row = depth_rows[depth_idx]
                    current_depth = _depth_for_row(current_depth_row)
                    in_depth = True
                    start_async_depth_fetch()
                needs_redraw = True
                continue

            elif key == 'UP':
                if in_depth:
                    if depth_idx > 0:
                        depth_idx -= 1
                        current_depth_row = depth_rows[depth_idx]
                        current_depth = _depth_for_row(current_depth_row)
                        start_async_depth_fetch()
                elif selected_idx > 0:
                    selected_idx -= 1

            elif key == 'DOWN':
                if in_depth:
                    if depth_idx < len(depth_rows) - 1:
                        depth_idx += 1
                        current_depth_row = depth_rows[depth_idx]
                        current_depth = _depth_for_row(current_depth_row)
                        start_async_depth_fetch()
                elif selected_idx < len(display_rows) - 1:
                    selected_idx += 1

            elif key in ('c', 'C'):
                if not in_depth:
                    sort_state['c'] = (sort_state['c'] + 1) % 2
                    sort_state['v'] = 0

            elif key in ('v', 'V'):
                if not in_depth:
                    sort_state['v'] = (sort_state['v'] + 1) % 2
                    sort_state['c'] = 0

            needs_redraw = True

    except KeyboardInterrupt:
        pass
    finally:
        clear_screen()
        print("已退出")


def print_help(script_name='watch_quotes.py'):
    print(f"用法: python {script_name} [刷新秒数] [--index on|off] [--once] [-h]")
    print()
    print("参数:")
    print("  刷新秒数          自动刷新间隔，默认 3 秒")
    print("  --index on|off    是否显示指数方块，默认 on")
    print("  --once            只取一次数据后退出")
    print("  -h, --help        显示此帮助信息")
    print()
    print("快捷键:")
    print("  ↑/↓      移动光标选择股票")
    print("  Enter/→  查看选中股票的盘口详情")
    print("  R        立即刷新数据")
    print("  C        按涨跌幅排序（第一次高到低，第二次恢复原顺序）")
    print("  V        按量比排序（第一次高到低，第二次恢复原顺序）")
    print("  ←/Esc    退出盘口或退出程序")
    print("  Ctrl+C   强制退出")


if __name__ == '__main__':
    original_output_cp = None
    if os.name == 'nt':
        try:
            import ctypes
            original_output_cp = ctypes.windll.kernel32.GetConsoleOutputCP()
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        except Exception:
            original_output_cp = None
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
    finally:
        if original_output_cp:
            ctypes.windll.kernel32.SetConsoleOutputCP(original_output_cp)
