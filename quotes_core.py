"""
股票行情核心库 —— 共享工具函数与数据源接口。

字段映射（东方财富 push2 / 新浪通用字段名）：
  f2  = 最新价       f3  = 涨跌幅(%)
  f4  = 涨跌额       f5  = 成交量(手)
  f6  = 成交额(元)   f8  = 换手率(%)
  f10 = 量比         f12 = 股票代码
  f13 = 市场(0深/1沪) f14 = 股票名称
  f15 = 最高价       f16 = 最低价
  f17 = 今开         f18 = 昨收
"""

import json
import re
import time
import unicodedata
import urllib.request
import urllib.parse
import os

# ── 字段名常量 ──────────────────────────────────────────────
F_PRICE       = 'f2'   # 最新价
F_PCT_CHG     = 'f3'   # 涨跌幅(%)
F_CHG         = 'f4'   # 涨跌额
F_VOL         = 'f5'   # 成交量(手)
F_AMT         = 'f6'   # 成交额(元)
F_TURNOVER    = 'f8'   # 换手率(%)
F_VOL_RATIO   = 'f10'  # 量比
F_CODE        = 'f12'  # 代码
F_MARKET      = 'f13'  # 市场 (0=深, 1=沪)
F_NAME        = 'f14'  # 名称
F_HIGH        = 'f15'  # 最高价
F_LOW         = 'f16'  # 最低价
F_OPEN        = 'f17'  # 今开
F_PREV_CLOSE  = 'f18'  # 昨收
F_TOTAL_CAP   = 'f20'  # 总市值
F_FLOAT_CAP   = 'f21'  # 流通市值
F_PE          = 'f9'   # 市盈率
F_PB          = 'f23'  # 市净率
F_LATEST_VOL  = 'f30'  # 现手（最近成交手）

# ── 脚本所在目录 ────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SECID_CACHE_FILE = os.path.join(BASE_DIR, '.secids_cache.json')


def log_message(message):
    if os.environ.get('WATCH_FRAME_LOG') != '1':
        return
    try:
        line = f"{time.strftime('%H:%M:%S')} {message}\n"
        with open(os.path.join(BASE_DIR, '.frame_log.txt'), 'a', encoding='utf-8') as f:
            f.write(line)
    except OSError:
        pass


# ── 3市场成交总额缓存与对应指数 ──────────────────────────────
MARKET_TOTAL_CACHE_FILE = os.path.join(BASE_DIR, '.market_total_cache.json')
MARKET_TOTAL_SECIDS = {
    'sh': '1.000001',   # 上证指数 -> 沪市
    'sz': '0.399001',   # 深证成指 -> 深市
    'bj': '0.899050',   # 北证50   -> 北市
}

# ── 上证/深证指数识别正则 ───────────────────────────────────
_RE_SH_INDEX      = re.compile(r'^000(001|688)$')
_RE_SZ_BJ_INDEX   = re.compile(r'^(399|899|930)\d{3}$')


def fetch(url, timeout=15, retries=1, extra_headers=None):
    """HTTP GET 并解析 JSON，支持自动重试。"""
    last_err = None
    for i in range(retries):
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            if extra_headers:
                headers.update(extra_headers)
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode('utf-8'))
        except Exception as e:
            last_err = e
            if i < retries - 1:
                time.sleep(0.2 * (i + 1))
    raise last_err


def secid_to_sina_code(secid):
    """将东方财富 secid (如 1.600519) 转换为新浪代码 (如 sh600519)。"""
    if '.' in secid:
        market, code = secid.split('.', 1)
    else:
        market, code = '0', secid
    prefix = {'0': 'sz', '1': 'sh'}.get(market, 'bj')
    if (market == '1' and _RE_SH_INDEX.match(code)) or \
       (market == '0' and _RE_SZ_BJ_INDEX.match(code)):
        prefix = 's_' + prefix
    return prefix + code


def parse_sina_quote_response(text):
    """解析新浪财经 JS 响应，返回与东财 push2 兼容的 dict 列表。"""
    rows = []
    for line in text.splitlines():
        m = re.search(r'var hq_str_([a-z_]+(\d+))="([^"]*)";', line)
        if not m:
            continue
        sina_code = m.group(1)
        content = m.group(3)
        if not content:
            continue
        parts = content.split(',')
        code = m.group(2)

        if re.search(r'^s?_?sh', sina_code):
            market_num = '1'
        elif re.search(r'^s?_?sz', sina_code):
            market_num = '0'
        elif re.search(r'^s?_?bj', sina_code):
            market_num = 'other'
        else:
            market_num = '1'

        row = {F_CODE: code, F_MARKET: market_num, F_NAME: parts[0],
               F_TURNOVER: '-', F_VOL_RATIO: '-'}

        if sina_code.startswith('s_'):
            # 指数
            if len(parts) < 4:
                continue
            row.update({
                F_PRICE: parts[1],
                F_CHG: parts[2],
                F_PCT_CHG: parts[3],
                F_VOL: parts[4] if len(parts) > 4 else '-',
                F_AMT: parts[5] if len(parts) > 5 else '-',
                F_HIGH: '-', F_LOW: '-', F_OPEN: '-', F_PREV_CLOSE: '-'
            })
            try:
                p = float(parts[1])
                c = float(parts[2])
                row[F_PREV_CLOSE] = round(p - c, 4)
            except Exception:
                pass
        else:
            # 个股
            if len(parts) < 6:
                continue
            row.update({
                F_PRICE: parts[3],
                F_PREV_CLOSE: parts[2],
                F_OPEN: parts[1],
                F_HIGH: parts[4],
                F_LOW: parts[5],
                F_VOL: str(int(parts[8]) // 100) if len(parts) > 8 and parts[8].isdigit() else '-',
                F_AMT: parts[9] if len(parts) > 9 else '-'
            })
            try:
                p = float(parts[3])
                pc = float(parts[2])
                row[F_CHG] = round(p - pc, 3)
                row[F_PCT_CHG] = round((p - pc) / pc * 100, 2) if pc != 0 else 0
            except Exception:
                row[F_CHG] = '-'
                row[F_PCT_CHG] = '-'
        rows.append(row)
    return rows


def fetch_quotes_sina(secids, retries=1):
    """通过新浪财经获取行情（兜底数据源）。"""
    if not secids:
        return []
    sina_codes = [secid_to_sina_code(s) for s in secids]
    url = 'https://hq.sinajs.cn/list=' + ','.join(sina_codes)
    last_err = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                'Referer': 'https://finance.sina.com.cn/'
            })
            with urllib.request.urlopen(req, timeout=15) as resp:
                # 使用 gbk 解码（gb2312 的超集，更安全）
                text = resp.read().decode('gbk', errors='replace')
            return parse_sina_quote_response(text)
        except Exception as e:
            last_err = e
            if i < retries - 1:
                time.sleep(0.3 * (i + 1))
    raise last_err


def fmt_num(v, digits=2, divisor=1):
    """格式化数字：千分位、指定小数位、可除数转换。"""
    if v in (None, '-', ''):
        return '-'
    try:
        f = float(v) / divisor
        if digits == 0:
            return f"{int(f):,}"
        return f"{f:,.{digits}f}"
    except Exception:
        return str(v)


def is_etf(row):
    """根据股票名称判断是否属于 ETF（名称含 ETF 字样）。"""
    name = str(row.get(F_NAME, ''))
    return 'ETF' in name.upper()


def fmt_price(v, row):
    """格式化交易价格：ETF 保留 3 位小数，普通股票/指数保留 2 位。"""
    return fmt_num(v, digits=3 if is_etf(row) else 2)


def char_width(c):
    if unicodedata.combining(c):
        return 0
    return 2 if unicodedata.east_asian_width(c) in ('W', 'F') else 1


def display_width(s):
    """按 East Asian Width 计算终端显示宽度。"""
    return sum(char_width(c) for c in str(s))


def pad_center(s, width):
    s = str(s)
    sw = display_width(s)
    left = max(0, (width - sw) // 2)
    right = max(0, width - sw - left)
    return ' ' * left + s + ' ' * right


def pad_left(s, width):
    s = str(s)
    sw = display_width(s)
    pad = max(0, width - sw)
    return ' ' * pad + s


def pad_right(s, width):
    s = str(s)
    sw = display_width(s)
    pad = max(0, width - sw)
    return s + ' ' * pad


def truncate_name(s, max_width=8):
    """截断名称至指定显示宽度。"""
    s = str(s)
    w = 0
    result = []
    for c in s:
        cw = char_width(c)
        if w + cw <= max_width:
            w += cw
            result.append(c)
        else:
            break
    return ''.join(result)


def split_name(name):
    """将股票名称拆分为两半（用于两行显示）。"""
    name = str(name)
    mid = (len(name) + 1) // 2
    return name[:mid], name[mid:]


def search_secids(names, meta=None):
    """通过东方财富搜索 API 将股票名称转为 secid。

    Args:
        names: 股票/指数名称列表
        meta: 可选 dict，用于回填 name/code/market 信息

    Returns:
        secid 列表 (如 ['1.600519', '0.000001'])
    """
    try:
        with open(SECID_CACHE_FILE, 'r', encoding='utf-8') as f:
            cache = json.load(f)
    except (OSError, ValueError, TypeError):
        cache = {}

    resolved = {}
    items_by_name = {}
    changed = False
    for name in names:
        if name in cache:
            resolved[name] = cache[name]
            continue
        try:
            url = (
                "https://searchapi.eastmoney.com/api/suggest/get?"
                f"input={urllib.parse.quote(name)}&type=14&count=5"
            )
            data = fetch(url, timeout=10)
            items = data.get('QuotationCodeTable', {}).get('Data') or []
            item = next((it for it in items if it.get('Name') == name), None)
            if item is None:
                log_message(f"security not matched exactly: {name}")
                continue
            secid = item.get('QuoteID')
            if not secid:
                continue
            resolved[name] = secid
            items_by_name[name] = item
            cache[name] = secid
            changed = True
        except Exception as e:
            log_message(f"security search failed: {name}: {e}")

    if changed:
        try:
            temp_path = SECID_CACHE_FILE + '.tmp'
            with open(temp_path, 'w', encoding='utf-8') as f:
                json.dump(cache, f, ensure_ascii=False)
            os.replace(temp_path, SECID_CACHE_FILE)
        except OSError as e:
            log_message(f"secid cache save failed: {e}")

    secids = [resolved[name] for name in names if name in resolved]
    if meta is not None:
        for name, item in items_by_name.items():
            secid = resolved[name]
            meta[secid] = {
                'name': item.get('Name', name),
                'code': item.get('Code'),
                'market': '深A' if str(item.get('MktNum')) == '0' else '沪A',
            }
    return secids


def fetch_quote_rows(secids):
    """获取行情数据：主用东财 push2，失败自动降级到新浪。

    Returns:
        list[dict]: 每只股票的行情字段，字段名见模块顶部常量
    """
    if not secids:
        return []

    # 1) 主数据源：东方财富
    try:
        fields = ','.join([
            F_CODE, F_MARKET, F_NAME, F_PRICE, F_PCT_CHG, F_CHG,
            F_VOL, F_AMT, F_TURNOVER, F_VOL_RATIO, F_HIGH, F_LOW,
            F_OPEN, F_PREV_CLOSE, F_TOTAL_CAP, F_FLOAT_CAP, F_PE, F_PB,
            F_LATEST_VOL
        ])
        secid_str = ','.join(secids)
        quote_url = (
            "https://push2.eastmoney.com/api/qt/ulist.np/get?"
            f"fltt=2&invt=2&fields={fields}&secids={secid_str}"
        )
        qdata = fetch(quote_url, timeout=15, retries=2)
        rows = qdata.get('data', {}).get('diff', [])
        if rows:
            return rows
    except Exception as e:
        log_message(f'EastMoney quote fetch failed: {e}')

    # 2) 兜底数据源：新浪财经
    try:
        rows = fetch_quotes_sina(secids, retries=2)
        if rows:
            log_message(f'switched to Sina fallback ({len(rows)}/{len(secids)} symbols)')
            return rows
    except Exception as e:
        log_message(f'Sina quote fetch failed: {e}')

    raise RuntimeError('所有行情数据源均不可用（东财、新浪）')


def fmt_open_price(open_val, prev_close_val, digits=2):
    """格式化今开价，附带相对昨收的箭头指示。"""
    price_str = fmt_num(open_val, digits=digits)
    try:
        o = float(open_val)
        p = float(prev_close_val)
        if o > p:
            return price_str, '↑'
        elif o < p:
            return price_str, '↓'
        else:
            return price_str, '-'
    except Exception:
        pass
    return price_str, ' '


def fetch_depth_data(secid, retries=1):
    """获取单只股票的五档买卖盘口数据。

    Args:
        secid: 东方财富 secid，如 '1.600519'
        retries: 重试次数

    Returns:
        dict 或 None（获取失败时）。
        字段: name, code, price, prev_close, open, high, low,
              volume, amount, turnover, change, pct_chg,
              limit_up, limit_down,
              asks: [(price, vol), ...] 卖1~卖5
              bids: [(price, vol), ...] 买1~买5
    """
    fields = (
        'f43,f44,f45,f46,f47,f48,f57,f58,f60,f169,f170,f8,'
        'f19,f20,f17,f18,f15,f16,f13,f14,f11,f12,'
        'f39,f40,f37,f38,f35,f36,f33,f34,f31,f32,'
        'f51,f52'
    )
    # Try main domain first, then numbered fallback servers
    urls = [
        f'https://push2.eastmoney.com/api/qt/stock/get?ut=fa5fd1943c7b386f172d5113dba1e33d&secid={secid}&fields={fields}',
        f'https://1.push2.eastmoney.com/api/qt/stock/get?ut=fa5fd1943c7b386f172d5113dba1e33d&secid={secid}&fields={fields}',
    ]

    # Price-related fields that need /100 adjustment on numbered fallback servers
    _price_fields = {
        'f43', 'f44', 'f45', 'f46', 'f60', 'f169', 'f170',
        'f19', 'f17', 'f15', 'f13', 'f11',
        'f39', 'f37', 'f35', 'f33', 'f31',
        'f51', 'f52',
    }

    headers = {
        'Referer': 'https://quote.eastmoney.com/',
        'Accept': 'application/json, text/javascript, */*; q=0.01',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    }

    last_err = None
    for url_idx, url in enumerate(urls):
        is_fallback = url_idx > 0
        # For main domain, retry according to retries param;
        # for fallback, just one shot per URL
        max_tries = retries if not is_fallback else 1
        for i in range(max_tries):
            try:
                data = fetch(url, timeout=10, extra_headers=headers)
            except Exception as e:
                last_err = e
                if i < max_tries - 1:
                    time.sleep(0.3 * (i + 1))
                continue
            d = data.get('data', {})
            if not d:
                last_err = RuntimeError('empty response data')
                continue

            def _float(key, default=None):
                v = d.get(key, '')
                try:
                    val = float(v) if v not in (None, '', '-') else default
                    # 东方财富 stock/get 接口的价格相关字段统一放大 100 倍
                    if val is not None and key in _price_fields:
                        val = val / 100.0
                    return val
                except (ValueError, TypeError):
                    return default

            def _int_from_float(key, default=None):
                v = _float(key, default)
                return int(v) if v is not None else default

            # 五档 (price, volume) 对 —— 注意字段编号是递减的
            asks = [
                (_float('f39'), _int_from_float('f40')),
                (_float('f37'), _int_from_float('f38')),
                (_float('f35'), _int_from_float('f36')),
                (_float('f33'), _int_from_float('f34')),
                (_float('f31'), _int_from_float('f32')),
            ]
            bids = [
                (_float('f19'), _int_from_float('f20')),
                (_float('f17'), _int_from_float('f18')),
                (_float('f15'), _int_from_float('f16')),
                (_float('f13'), _int_from_float('f14')),
                (_float('f11'), _int_from_float('f12')),
            ]

            return {
                'name': d.get('f58', ''),
                'code': d.get('f57', ''),
                'price': _float('f43'),
                'prev_close': _float('f60'),
                'open': _float('f46'),
                'high': _float('f44'),
                'low': _float('f45'),
                'volume': _float('f47'),
                'amount': _float('f48'),
                'turnover': _float('f8'),
                'change': _float('f169'),
                'pct_chg': _float('f170'),
                'limit_up': _float('f51'),
                'limit_down': _float('f52'),
                'asks': asks,
                'bids': bids,
            }
    if last_err:
        log_message(f'EastMoney depth fetch failed: {last_err}, trying Sina fallback')

    # ── 兜底：新浪财经 ──
    try:
        sina_code = secid_to_sina_code(secid)
        url = f'https://hq.sinajs.cn/list={sina_code}'
        req = urllib.request.Request(url, headers={
            'Referer': 'https://finance.sina.com.cn/',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            text = resp.read().decode('gbk', errors='replace')
        m = re.search(r'var hq_str_' + re.escape(sina_code) + r'="([^"]*)";', text)
        if m:
            content = m.group(1)
            if content:
                parts = content.split(',')
                if len(parts) >= 30:
                    name = parts[0]
                    code = sina_code[-6:] if len(sina_code) >= 6 else sina_code
                    open_p = _safe_float(parts[1])
                    prev_close = _safe_float(parts[2])
                    price = _safe_float(parts[3])
                    high = _safe_float(parts[4])
                    low = _safe_float(parts[5])
                    volume = _safe_float(parts[8]) / 100.0 if parts[8].isdigit() else None
                    amount = _safe_float(parts[9])
                    change = round(price - prev_close, 3) if price is not None and prev_close is not None else None
                    pct_chg = round(change / prev_close * 100, 2) if change is not None and prev_close else None
                    # 根据板块计算涨停跌停幅度
                    limit_ratio = _limit_ratio_for_code(code, name)
                    limit_up = round(prev_close * (1 + limit_ratio), 2) if prev_close is not None else None
                    limit_down = round(prev_close * (1 - limit_ratio), 2) if prev_close is not None else None
                    bids = [
                        (_safe_float(parts[11]), _safe_lot(parts[10])),
                        (_safe_float(parts[13]), _safe_lot(parts[12])),
                        (_safe_float(parts[15]), _safe_lot(parts[14])),
                        (_safe_float(parts[17]), _safe_lot(parts[16])),
                        (_safe_float(parts[19]), _safe_lot(parts[18])),
                    ]
                    asks = [
                        (_safe_float(parts[21]), _safe_lot(parts[20])),
                        (_safe_float(parts[23]), _safe_lot(parts[22])),
                        (_safe_float(parts[25]), _safe_lot(parts[24])),
                        (_safe_float(parts[27]), _safe_lot(parts[26])),
                        (_safe_float(parts[29]), _safe_lot(parts[28])),
                    ]
                    return {
                        'name': name,
                        'code': code,
                        'price': price,
                        'prev_close': prev_close,
                        'open': open_p,
                        'high': high,
                        'low': low,
                        'volume': volume,
                        'amount': amount,
                        'turnover': None,
                        'change': change,
                        'pct_chg': pct_chg,
                        'limit_up': limit_up,
                        'limit_down': limit_down,
                        'asks': asks,
                        'bids': bids,
                    }
    except Exception as e:
        log_message(f'Sina depth fallback failed: {e}')

    return None


def _safe_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _safe_lot(v):
    try:
        return int(float(v)) // 100
    except (TypeError, ValueError):
        return None


def _limit_ratio_for_code(code, name=''):
    """根据股票代码和名称返回涨跌停限制比例。"""
    code = str(code)
    name = str(name).upper()
    if 'ST' in name:
        return 0.05
    if code.startswith('688') or code.startswith('300') or code.startswith('301'):
        return 0.20
    if code.startswith('8') or code.startswith('4'):
        return 0.30
    return 0.10


def get_key(timeout=0.1):
    """跨平台非阻塞读取单个按键。

    在 Windows 上使用 msvcrt，在 Unix 上使用 tty+termios+select。

    Args:
        timeout: 轮询超时（秒），仅在 Unix select 模式下生效

    Returns:
        str 或 None:
            'UP', 'DOWN', 'LEFT', 'RIGHT', 'ENTER', 'ESC', 'R', 'r',
            或 None（超时无输入）
    """
    import sys
    if sys.platform == 'win32':
        import msvcrt
        if not msvcrt.kbhit():
            return None
        ch = msvcrt.getch()
        if ch in (b'\xe0', b'\x00'):
            ch2 = msvcrt.getch()
            _arrow = {b'H': 'UP', b'P': 'DOWN', b'K': 'LEFT', b'M': 'RIGHT'}
            return _arrow.get(ch2)
        if ch == b'\r':
            return 'ENTER'
        if ch == b'\x1b':
            return 'ESC'
        try:
            decoded = ch.decode('utf-8', errors='replace')
            return decoded if len(decoded) == 1 else None
        except Exception:
            return None
    else:
        # Unix: read escape sequences
        import select
        import tty
        import termios

        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            r, _, _ = select.select([sys.stdin], [], [], timeout)
            if not r:
                return None
            ch = sys.stdin.read(1)
            if ch == '\x1b':
                r2, _, _ = select.select([sys.stdin], [], [], 0.05)
                if r2:
                    ch2 = sys.stdin.read(1)
                    if ch2 == '[':
                        ch3 = sys.stdin.read(1)
                        _arrow = {'A': 'UP', 'B': 'DOWN', 'C': 'RIGHT', 'D': 'LEFT'}
                        return _arrow.get(ch3)
                return 'ESC'
            if ch in ('\r', '\n'):
                return 'ENTER'
            return ch if len(ch) == 1 else None
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)


def load_market_total_cache():
    """加载3市场成交额历史缓存。"""
    try:
        with open(MARKET_TOTAL_CACHE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def save_market_total_cache(cache):
    """原子保存3市场成交额历史缓存，避免中断留下半个 JSON。"""
    temp_path = MARKET_TOTAL_CACHE_FILE + '.tmp'
    try:
        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False)
        os.replace(temp_path, MARKET_TOTAL_CACHE_FILE)
    except OSError as e:
        log_message(f'market cache save failed: {e}')


_market_total_cache = None
_market_history_refresh_date = None
_market_history_ready = False
_market_total_next_retry = 0.0


def get_market_total_cache():
    """获取全局缓存对象（首次调用时从磁盘加载）。"""
    global _market_total_cache
    if _market_total_cache is None:
        _market_total_cache = load_market_total_cache()
    return _market_total_cache


def fetch_index_kline_amounts(secid, days=20):
    """获取指数日线成交额历史，返回 [(date, amount), ...]。"""
    import datetime
    today = datetime.date.today()
    beg = (today - datetime.timedelta(days=days + 5)).strftime('%Y%m%d')
    end = (today + datetime.timedelta(days=1)).strftime('%Y%m%d')
    url = (
        "https://push2his.eastmoney.com/api/qt/stock/kline/get?"
        f"secid={secid}&fields1=f1,f2,f3,f4,f5,f6&"
        "fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61&"
        f"klt=101&fqt=0&beg={beg}&end={end}"
    )
    try:
        data = fetch(url, timeout=15,
                     extra_headers={'Referer': 'https://quote.eastmoney.com/'})
        klines = data.get('data', {}).get('klines', [])
        result = []
        for k in klines:
            parts = k.split(',')
            if len(parts) >= 7:
                result.append((parts[0], float(parts[6])))
        return result
    except Exception as e:
        log_message(f'market total kline fetch failed for {secid}: {e}')
        return []


def warm_market_total_cache():
    """每天刷新历史成交额；由后台线程调用，不在绘制时访问网络。"""
    import datetime
    global _market_history_refresh_date, _market_history_ready, _market_total_next_retry
    today_str = datetime.date.today().strftime('%Y-%m-%d')
    if _market_history_refresh_date == today_str or time.time() < _market_total_next_retry:
        return
    _market_history_refresh_date = today_str
    cache = get_market_total_cache()
    changed = False
    fresh_count = 0
    for secid in MARKET_TOTAL_SECIDS.values():
        dates = cache.setdefault(secid, {})
        items = fetch_index_kline_amounts(secid, 20)
        if items:
            fresh_count += 1
        for date_str, amount in items:
            dates[date_str] = amount
            changed = True
    if changed:
        save_market_total_cache(cache)
    _market_history_ready = fresh_count == len(MARKET_TOTAL_SECIDS)
    if _market_history_ready:
        _market_total_next_retry = 0.0
    else:
        _market_history_refresh_date = None
        _market_total_next_retry = time.time() + 60


def _trading_elapsed_minutes(time_str):
    """计算 A 股从开盘到指定时刻的累计交易分钟数（09:30-11:30, 13:00-15:00，共240分钟）。"""
    import datetime
    t = datetime.datetime.strptime(time_str, '%H:%M')
    minutes = t.hour * 60 + t.minute

    am_start = 9 * 60 + 30   # 570
    am_end = 11 * 60 + 30    # 690
    pm_start = 13 * 60       # 780
    pm_end = 15 * 60         # 900

    if minutes <= am_start:
        return 0
    if minutes <= am_end:
        return minutes - am_start
    if minutes <= pm_start:
        return am_end - am_start
    if minutes <= pm_end:
        return (am_end - am_start) + (minutes - pm_start)
    return (am_end - am_start) + (pm_end - pm_start)  # 240


def get_market_total_amounts(index_rows):
    """计算沪深京三市场今日成交总额及量比。

    量比 = (今日累计成交额 / 已交易分钟数) / (过去5日日均成交额 / 240)

    Returns:
        dict: {'total_yi': 总额(亿), 'ratio': 量比} 或 None（数据不足）。
    """
    import datetime
    cache = get_market_total_cache()
    today = datetime.date.today()
    if not _market_history_ready or today.weekday() >= 5:
        return None
    today_str = today.strftime('%Y-%m-%d')

    # 从已有指数行情中提取今日成交额
    today_amounts = {}
    for r in index_rows:
        secid = f"{r.get(F_MARKET, '0')}.{r.get(F_CODE, '')}"
        if secid in MARKET_TOTAL_SECIDS.values():
            try:
                today_amounts[secid] = float(r.get(F_AMT, 0) or 0)
            except Exception:
                today_amounts[secid] = 0.0

    if len(today_amounts) != 3 or any(v <= 0 for v in today_amounts.values()):
        return None

    date_sets = [
        {d for d in cache.get(secid, {}) if d < today_str}
        for secid in MARKET_TOTAL_SECIDS.values()
    ]
    dates = sorted(set.intersection(*date_sets), reverse=True)[:5]
    if len(dates) < 5:
        return None

    # 只按三个市场共同交易日合计，避免日期错位
    total_by_day = [
        sum(cache[secid][d] for secid in MARKET_TOTAL_SECIDS.values())
        for d in dates
    ]

    avg = sum(total_by_day) / len(total_by_day)
    today_total = sum(today_amounts.values())

    current_time = datetime.datetime.now().strftime('%H:%M')
    elapsed = _trading_elapsed_minutes(current_time)
    if elapsed <= 0:
        return None

    total_minutes = 240
    ratio = (today_total / elapsed) / (avg / total_minutes) if avg > 0 else None
    return {
        'total_yi': round(today_total / 1e8, 2),
        'ratio': round(ratio, 2) if ratio is not None else None,
    }


def print_market_total(info, table_width=113):
    """在指数方块下方打印三市成交总额与量比。"""
    if not info or info.get('total_yi') is None or info.get('ratio') is None:
        return
    line = f"三市总额/量比 {info['total_yi']} / {info['ratio']}"
    print(pad_right(line, table_width))


def print_indices(rows, table_width=113):
    """以方块形式打印指数行情。"""
    print('-' * table_width)

    per_row = 5
    sep = ' │ '
    sep_w = display_width(sep)

    boxes = []
    min_box_w = 0
    for r in rows:
        name = r.get(F_NAME, '')
        price = fmt_num(r.get(F_PRICE))
        change = fmt_num(r.get(F_CHG))
        pct = fmt_num(r.get(F_PCT_CHG))
        right2 = f"{change} / {pct}%"
        boxes.append((name, price, right2))
        min_box_w = max(
            min_box_w,
            display_width(name) + display_width(price),
            display_width(right2),
        )

    box_w = (table_width - (per_row - 1) * sep_w) // per_row
    if box_w < min_box_w:
        box_w = min_box_w

    def fmt_top(name, price):
        pad = box_w - display_width(name) - display_width(price)
        if pad < 0:
            pad = 0
        return name + ' ' * pad + price

    def fmt_bottom(right2):
        pad = box_w - display_width(right2)
        if pad < 0:
            pad = 0
        return ' ' * pad + right2

    for i in range(0, len(boxes), per_row):
        parts1 = []
        parts2 = []
        for j in range(per_row):
            if i + j >= len(boxes):
                break
            name, price, right2 = boxes[i + j]
            parts1.append(fmt_top(name, price))
            parts2.append(fmt_bottom(right2))
        line1 = sep.join(parts1)
        line2 = sep.join(parts2)
        pad = table_width - display_width(line1)
        if pad > 0:
            line1 += ' ' * pad
        pad = table_width - display_width(line2)
        if pad > 0:
            line2 += ' ' * pad
        print(line1)
        print(line2)
