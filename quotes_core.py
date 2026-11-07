"""
股票行情核心库 —— 数据源、缓存、格式化与终端工具。

Windows（watch_quotes.ps1）与 macOS/Linux（watch_quotes.py）共用同一套
磁盘缓存文件，因此这里所有 JSON 读写都按「UTF-8、无 BOM、原子替换」处理，
读取时兼容 PowerShell 5.1 写出的带 BOM 文件。

字段映射（东方财富 push2 / 新浪通用字段名）：
  f2  = 最新价       f3  = 涨跌幅(%)
  f4  = 涨跌额       f5  = 成交量(手)
  f6  = 成交额(元)   f8  = 换手率(%)
  f10 = 量比         f12 = 股票代码
  f13 = 市场(0深/1沪) f14 = 股票名称
  f15 = 最高价       f16 = 最低价
  f17 = 今开         f18 = 昨收
"""

import calendar
import concurrent.futures
import datetime
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

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
HY_CACHE_FILE = os.path.join(BASE_DIR, '.hy_pct_cache.json')
MARKET_TOTAL_CACHE_FILE = os.path.join(BASE_DIR, '.market_total_cache.json')
MA_CACHE_FILE = os.path.join(BASE_DIR, '.ma_cache.json')
PEPB_CACHE_FILE = os.path.join(BASE_DIR, '.pepb_cache.json')

# 统一的 UA；部分行情接口对空 UA 或 Python-urllib 直接拒绝
_UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
       'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36')


def log_message(message):
    """按需把诊断信息写入 .frame_log.txt（设置 WATCH_FRAME_LOG=1 时）。"""
    if os.environ.get('WATCH_FRAME_LOG') != '1':
        return
    try:
        line = f"{time.strftime('%H:%M:%S')} {message}\n"
        with open(os.path.join(BASE_DIR, '.frame_log.txt'), 'a', encoding='utf-8') as f:
            f.write(line)
    except OSError:
        pass


# ── JSON 缓存：读取兼容 BOM，写入无 BOM 且原子替换 ──────────

def load_json_cache(path, default=None):
    """读取 JSON 缓存；兼容 PowerShell 5.1 写出的 UTF-8 BOM 文件。"""
    try:
        with open(path, 'r', encoding='utf-8-sig') as f:
            return json.load(f)
    except (OSError, ValueError, TypeError):
        return {} if default is None else default


def save_json_cache(path, data):
    """原子保存 JSON 缓存，不写 BOM，供 Windows/macOS 共用。"""
    temp_path = f'{path}.tmp.{os.getpid()}'
    try:
        with open(temp_path, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(temp_path, path)
    except OSError as e:
        log_message(f'cache save failed: {path}: {e}')
        try:
            os.unlink(temp_path)
        except OSError:
            pass


# ── 3市场成交总额缓存与对应指数 ──────────────────────────────
MARKET_TOTAL_SECIDS = {
    'sh': '1.000001',   # 上证指数 -> 沪市
    'sz': '0.399001',   # 深证成指 -> 深市
    'bj': '0.899050',   # 北证50   -> 北市
}

# ── 上证/深证指数识别正则 ───────────────────────────────────
_RE_SH_INDEX      = re.compile(r'^000(001|688)$')
_RE_SZ_INDEX      = re.compile(r'^(399|930)\d{3}$')
_RE_BJ_INDEX      = re.compile(r'^899\d{3}$')
_RE_BJ_STOCK      = re.compile(r'^(4|8|92)\d{4,5}$')


# ── HTTP 层：证书降级 + curl 兜底 ───────────────────────────
#
# macOS 上通过 python.org 安装的 Python 经常没有安装根证书，直接访问会报
# CERTIFICATE_VERIFY_FAILED；个别网络环境下 urllib 还会被行情服务器
# 直接断开（RemoteDisconnected）。为提高可用性：
#   1. 优先使用系统/ certifi 证书正常校验；
#   2. 证书校验失败时降级为不校验（只警告一次）；
#   3. urllib 失败时，若系统有 curl 则用 curl 重试（它走独立的 TLS 栈）。

_SSL_CONTEXT = None
_SSL_WARNED = False


def _ssl_context():
    global _SSL_CONTEXT
    if _SSL_CONTEXT is None:
        try:
            import certifi  # 可选依赖，装了就优先用
            _SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
        except Exception:
            _SSL_CONTEXT = ssl.create_default_context()
    return _SSL_CONTEXT


def _is_cert_error(exc):
    reason = getattr(exc, 'reason', exc)
    text = str(reason)
    return isinstance(reason, ssl.SSLError) and (
        'CERTIFICATE_VERIFY_FAILED' in text or 'certificate verify failed' in text
    )


def _warn_ssl_once():
    global _SSL_WARNED
    if _SSL_WARNED:
        return
    _SSL_WARNED = True
    log_message('SSL certificate verification failed; retrying without verification')
    if sys.stderr.isatty():
        print('[warn] 系统 SSL 证书校验失败，已临时跳过校验；'
              '建议运行 Python 安装目录下的 Install Certificates.command',
              file=sys.stderr)


def _http_get_bytes(url, timeout=15, extra_headers=None):
    """urllib GET，证书校验失败时自动降级。"""
    headers = {
        'User-Agent': _UA,
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    }
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
            return resp.read()
    except urllib.error.URLError as e:
        if not _is_cert_error(e):
            raise
        _warn_ssl_once()
        context = ssl._create_unverified_context()
        with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:
            return resp.read()


def _curl_bytes(url, timeout=15, extra_headers=None):
    """用系统 curl 兜底取回原始字节；没有 curl 时返回 None。"""
    if os.environ.get('WATCH_DISABLE_CURL') == '1':
        return None
    curl = shutil.which('curl')
    if not curl:
        return None
    args = [
        curl, '-sS', '--compressed', '-L', '--max-time', str(int(timeout)),
        '-A', _UA,
    ]
    if extra_headers:
        for key, value in extra_headers.items():
            args += ['-H', f'{key}: {value}']
    args.append(url)
    kwargs = {}
    if os.name == 'nt':
        kwargs['creationflags'] = 0x08000000  # CREATE_NO_WINDOW
    try:
        proc = subprocess.run(
            args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=timeout + 2, **kwargs
        )
    except (OSError, subprocess.SubprocessError) as e:
        log_message(f'curl fallback failed: {e}')
        return None
    if proc.returncode != 0 or not proc.stdout:
        detail = proc.stderr.decode('utf-8', errors='replace').strip()[:200]
        log_message(f'curl fallback failed ({proc.returncode}): {detail}')
        return None
    return proc.stdout


def _parse_json(raw):
    """解析 JSON，兼容部分接口返回的 JSONP 包装。"""
    text = raw.decode('utf-8-sig', errors='replace')
    try:
        return json.loads(text)
    except ValueError:
        match = re.match(r'^\s*[\w$.]+\s*\((.*)\)\s*;?\s*$', text, re.S)
        if match:
            return json.loads(match.group(1))
        raise


def fetch(url, timeout=15, retries=1, extra_headers=None):
    """HTTP GET 并解析 JSON：urllib 失败时自动改用 curl，支持重试。"""
    last_err = None
    attempts = max(1, int(retries))
    for i in range(attempts):
        try:
            return _parse_json(_http_get_bytes(url, timeout, extra_headers))
        except Exception as e:
            last_err = e
            log_message(f'urllib fetch failed: {url.split("?")[0]}: {e}')
            raw = _curl_bytes(url, timeout, extra_headers)
            if raw is not None:
                try:
                    return _parse_json(raw)
                except Exception as ce:
                    last_err = ce
                    log_message(f'curl response parse failed: {ce}')
        if i < attempts - 1:
            time.sleep(0.25 * (i + 1))
    raise RuntimeError(f'请求失败: {url.split("?")[0]}: {last_err}') from last_err


def fetch_via_curl(url, timeout=15, extra_headers=None):
    """强制通过 curl 取 JSON。

    某些网络环境下 urllib 会被搜索接口返回另一套（无关的）结果，
    但 HTTP 状态仍是 200，只有在解析后校验响应结构才能发现。
    """
    raw = _curl_bytes(url, timeout, extra_headers)
    if raw is None:
        raise RuntimeError(f'curl 不可用或请求失败: {url.split("?")[0]}')
    return _parse_json(raw)


def fetch_text(url, timeout=15, encoding='utf-8', extra_headers=None):
    """HTTP GET 并解码文本；urllib 失败时自动改用 curl。"""
    last_err = None
    try:
        return _http_get_bytes(url, timeout, extra_headers).decode(encoding, errors='replace')
    except Exception as e:
        last_err = e
        log_message(f'urllib text fetch failed: {url.split("?")[0]}: {e}')
    raw = _curl_bytes(url, timeout, extra_headers)
    if raw is not None:
        return raw.decode(encoding, errors='replace')
    raise RuntimeError(f'请求失败: {url.split("?")[0]}: {last_err}') from last_err


# ── 新浪代码转换与解析 ──────────────────────────────────────

def secid_to_sina_code(secid):
    """将东方财富 secid (如 1.600519) 转换为新浪代码 (如 sh600519)。

    北交所实时行情/指数在新浪使用 bj 前缀（如 bj430047、s_bj899050），
    不能沿用深市的 sz 前缀，否则兜底数据源会返回空。
    """
    if '.' in secid:
        market, code = secid.split('.', 1)
    else:
        market, code = '0', secid
    if market == '1':
        prefix = 's_sh' if _RE_SH_INDEX.match(code) else 'sh'
    elif market == '0':
        if _RE_BJ_INDEX.match(code):
            prefix = 's_bj'
        elif _RE_SZ_INDEX.match(code):
            prefix = 's_sz'
        elif _RE_BJ_STOCK.match(code):
            prefix = 'bj'
        else:
            prefix = 'sz'
    else:
        prefix = 'bj'
        if _RE_BJ_INDEX.match(code):
            prefix = 's_bj'
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
            market_num = '0'
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
            except (TypeError, ValueError):
                pass
        else:
            # 个股
            if len(parts) < 6:
                continue
            vol = '-'
            if len(parts) > 8:
                try:
                    vol = str(int(float(parts[8])) // 100)
                except (TypeError, ValueError):
                    vol = '-'
            row.update({
                F_PRICE: parts[3],
                F_PREV_CLOSE: parts[2],
                F_OPEN: parts[1],
                F_HIGH: parts[4],
                F_LOW: parts[5],
                F_VOL: vol,
                F_AMT: parts[9] if len(parts) > 9 else '-'
            })
            try:
                p = float(parts[3])
                pc = float(parts[2])
                row[F_CHG] = round(p - pc, 3)
                row[F_PCT_CHG] = round((p - pc) / pc * 100, 2) if pc != 0 else 0
            except (TypeError, ValueError):
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
    attempts = max(1, int(retries))
    for i in range(attempts):
        try:
            text = fetch_text(
                url, timeout=15, encoding='gbk',
                extra_headers={'Referer': 'https://finance.sina.com.cn/'}
            )
            return parse_sina_quote_response(text)
        except Exception as e:
            last_err = e
            if i < attempts - 1:
                time.sleep(0.3 * (i + 1))
    raise last_err


# ── 格式化工具 ──────────────────────────────────────────────

def fmt_num(v, digits=2, divisor=1):
    """格式化数字：千分位、指定小数位、可除数转换。"""
    if v in (None, '-', ''):
        return '-'
    try:
        f = float(v) / divisor
        if digits == 0:
            return f"{round(f):,}"
        return f"{f:,.{digits}f}"
    except (TypeError, ValueError):
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
    return ' ' * max(0, width - sw) + s


def pad_right(s, width):
    s = str(s)
    sw = display_width(s)
    return s + ' ' * max(0, width - sw)


def truncate_name(s, max_width=8):
    """截断名称至指定显示宽度。"""
    return truncate_to_width(s, max_width)


def truncate_to_width(s, max_width):
    """按终端显示宽度截断任意字符串（中文按 2 列计）。"""
    s = str(s)
    if max_width <= 0:
        return ''
    w = 0
    result = []
    for c in s:
        cw = char_width(c)
        if w + cw > max_width:
            break
        w += cw
        result.append(c)
    return ''.join(result)


def split_name(name):
    """将股票名称拆分为两半（用于两行显示）。"""
    name = str(name)
    mid = (len(name) + 1) // 2
    return name[:mid], name[mid:]


def fmt_open_price(open_val, prev_close_val, digits=2):
    """格式化今开价，附带相对昨收的箭头指示。"""
    price_str = fmt_num(open_val, digits=digits)
    try:
        o = float(open_val)
        p = float(prev_close_val)
        if o > p:
            return price_str, '↑'
        if o < p:
            return price_str, '↓'
        return price_str, '-'
    except (TypeError, ValueError):
        pass
    return price_str, ' '


# ── 名称 -> secid ───────────────────────────────────────────

def search_secids(names, meta=None):
    """通过东方财富搜索 API 将股票名称转为 secid。

    Args:
        names: 股票/指数名称列表
        meta: 可选 dict，用于回填 name/code/market 信息

    Returns:
        secid 列表 (如 ['1.600519', '0.000001'])，顺序与 names 一致。
    """
    cache = load_json_cache(SECID_CACHE_FILE, {})
    if not isinstance(cache, dict):
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
            data = fetch(url, timeout=10, retries=2)
            table = data.get('QuotationCodeTable') if isinstance(data, dict) else None
            if not table:
                # urllib 被代理/接口识别时可能返回 200 但内容不对，改用 curl 再取一次
                data = fetch_via_curl(url, timeout=10)
                table = data.get('QuotationCodeTable') if isinstance(data, dict) else None
            items = (table or {}).get('Data') or []
            item = next((it for it in items if it.get('Name') == name), None)
            if item is None:
                # 个别网络下 urllib 会拿到无关的 200 响应；用 curl 再确认一次
                try:
                    data = fetch_via_curl(url, timeout=10)
                    table = data.get('QuotationCodeTable') if isinstance(data, dict) else None
                    items = (table or {}).get('Data') or []
                    item = next((it for it in items if it.get('Name') == name), None)
                except Exception:
                    item = None
            if item is None:
                log_message(f'security not matched exactly: {name}')
                continue
            secid = item.get('QuoteID')
            if not secid:
                continue
            resolved[name] = secid
            items_by_name[name] = item
            cache[name] = secid
            changed = True
        except Exception as e:
            log_message(f'security search failed: {name}: {e}')

    if changed:
        save_json_cache(SECID_CACHE_FILE, cache)

    secids = [resolved[name] for name in names if name in resolved]
    if meta is not None:
        for name, item in items_by_name.items():
            secid = resolved.get(name)
            if not secid:
                continue
            meta[secid] = {
                'name': item.get('Name', name),
                'code': item.get('Code'),
                'market': '深A' if str(item.get('MktNum')) == '0' else '沪A',
            }
    return secids


# ── 实时行情 ────────────────────────────────────────────────

_QUOTE_FIELDS = ','.join([
    F_CODE, F_MARKET, F_NAME, F_PRICE, F_PCT_CHG, F_CHG,
    F_VOL, F_AMT, F_TURNOVER, F_VOL_RATIO, F_HIGH, F_LOW,
    F_OPEN, F_PREV_CLOSE, F_TOTAL_CAP, F_FLOAT_CAP, F_PE, F_PB,
    F_LATEST_VOL,
])


def _row_secid(row):
    return f"{row.get(F_MARKET, '0')}.{row.get(F_CODE, '')}"


def fetch_quote_rows(secids):
    """获取行情：主用东财 push2，失败或缺失时用新浪补齐。

    Returns:
        list[dict]: 按 secids 顺序返回，字段名见模块顶部常量。
    """
    if not secids:
        return []

    rows = []
    try:
        url = (
            "https://push2.eastmoney.com/api/qt/ulist.np/get?"
            f"fltt=2&invt=2&fields={_QUOTE_FIELDS}&secids={','.join(secids)}"
        )
        data = fetch(url, timeout=15, retries=2)
        diff = data.get('data', {}).get('diff') or []
        if isinstance(diff, dict):
            diff = list(diff.values())
        rows = list(diff)
    except Exception as e:
        log_message(f'EastMoney quote fetch failed: {e}')

    have = {_row_secid(r) for r in rows}
    missing = [s for s in secids if s not in have]
    if missing:
        try:
            sina_rows = fetch_quotes_sina(missing, retries=2)
            if sina_rows:
                rows.extend(sina_rows)
                log_message(f'switched to Sina fallback ({len(sina_rows)}/{len(missing)} symbols)')
        except Exception as e:
            log_message(f'Sina quote fetch failed: {e}')

    if not rows:
        raise RuntimeError('所有行情数据源均不可用（东财、新浪）')

    by_id = {_row_secid(r): r for r in rows}
    ordered = [by_id[s] for s in secids if s in by_id]
    extras = [r for r in rows if _row_secid(r) not in set(secids)]
    return ordered + extras


# ── 五档盘口 ────────────────────────────────────────────────

def fetch_depth_data(secid, retries=1):
    """获取单只股票的五档买卖盘口数据。

    Returns:
        dict 或 None（获取失败时）。字段见 README。
    """
    fields = (
        'f43,f44,f45,f46,f47,f48,f57,f58,f60,f169,f170,f8,'
        'f19,f20,f17,f18,f15,f16,f13,f14,f11,f12,'
        'f39,f40,f37,f38,f35,f36,f33,f34,f31,f32,'
        'f51,f52'
    )
    urls = [
        f'https://push2.eastmoney.com/api/qt/stock/get?ut=fa5fd1943c7b386f172d5113dba1e33d&secid={secid}&fields={fields}',
        f'https://1.push2.eastmoney.com/api/qt/stock/get?ut=fa5fd1943c7b386f172d5113dba1e33d&secid={secid}&fields={fields}',
    ]

    # 东财 stock/get 接口的价格相关字段统一放大 100 倍
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
        max_tries = retries if url_idx == 0 else 1
        for i in range(max(1, max_tries)):
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
                    if val is not None and key in _price_fields:
                        val = val / 100.0
                    return val
                except (TypeError, ValueError):
                    return default

            def _int_from_float(key, default=None):
                v = _float(key, default)
                return int(v) if v is not None else default

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
        text = fetch_text(
            url, timeout=15, encoding='gbk',
            extra_headers={'Referer': 'https://finance.sina.com.cn/'}
        )
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


# ── 终端输入（macOS/Linux raw mode + Windows msvcrt）─────────

_raw_fd = None
_raw_old = None


def is_tty():
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except Exception:
        return False


def enter_raw_mode():
    """进入一次性 raw 模式，持续到 restore_terminal()。"""
    global _raw_fd, _raw_old
    if sys.platform == 'win32':
        return True
    try:
        import termios
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        new = list(old)
        # 0=iflag, 1=oflag, 3=lflag, 6=cc
        new[0] &= ~(termios.BRKINT | termios.ICRNL
                    | termios.INPCK | termios.ISTRIP | termios.IXON)
        new[3] &= ~(termios.ECHO | termios.ICANON | termios.ISIG)
        new[6][termios.VMIN] = 1
        new[6][termios.VTIME] = 0
        # 保留 OPOST/ONLCR，print 的 \n 仍会转换为 \r\n
        new[1] |= termios.OPOST | termios.ONLCR
        termios.tcsetattr(fd, termios.TCSAFLUSH, new)
        _raw_fd = fd
        _raw_old = old
        return True
    except Exception:
        _raw_fd = None
        _raw_old = None
        return False


def restore_terminal():
    """退出交互时恢复终端原先的行模式/回显。"""
    global _raw_fd, _raw_old
    if sys.platform == 'win32' or _raw_fd is None:
        return
    try:
        import termios
        termios.tcsetattr(_raw_fd, termios.TCSADRAIN, _raw_old)
    except Exception:
        pass
    finally:
        _raw_fd = None
        _raw_old = None


def _read_escape_tail(fd, wait=0.06):
    """读取 ESC 之后的转义序列尾巴（最多 5 字节）。"""
    import select
    out = ''
    for _ in range(5):
        r, _, _ = select.select([fd], [], [], wait)
        if not r:
            break
        c = os.read(fd, 1)
        if not c:
            break
        ch = c.decode('utf-8', errors='replace')
        if ch == '\x1b':
            break
        out += ch
        if out in ('[A', '[B', '[C', '[D', '[20~',
                   'OA', 'OB', 'OC', 'OD'):
            break
    return out


def get_key(timeout=0.1):
    """跨平台非阻塞读取单个按键。

    Returns:
        'UP' / 'DOWN' / 'LEFT' / 'RIGHT' / 'ENTER' / 'ESC' / 'F9' /
        'CTRL_C' / 单个字符 / None（超时无输入）
    """
    if sys.platform == 'win32':
        import msvcrt
        if not msvcrt.kbhit():
            return None
        ch = msvcrt.getch()
        if ch in (b'\xe0', b'\x00'):
            ch2 = msvcrt.getch()
            _special = {
                b'H': 'UP', b'P': 'DOWN', b'K': 'LEFT', b'M': 'RIGHT',
                b'C': 'F9', b'S': 'DELETE',
            }
            return _special.get(ch2)
        if ch == b'\r':
            return 'ENTER'
        if ch == b'\x1b':
            return 'ESC'
        if ch == b'\x03':
            return 'CTRL_C'
        try:
            decoded = ch.decode('ascii')
            return decoded if len(decoded) == 1 else None
        except Exception:
            return None

    # Unix：已由 enter_raw_mode() 保持 raw，直接读取字节
    import select
    try:
        fd = sys.stdin.fileno()
    except Exception:
        return None
    r, _, _ = select.select([fd], [], [], timeout)
    if not r:
        return None
    ch = os.read(fd, 1)
    if not ch:
        return None
    ch = ch.decode('utf-8', errors='replace')
    if ch == '\x1b':
        seq = _read_escape_tail(fd)
        if seq[:1] in ('[', 'O'):
            body = seq[1:]
            _arrow = {'A': 'UP', 'B': 'DOWN', 'C': 'RIGHT', 'D': 'LEFT'}
            if body in _arrow:
                return _arrow[body]
            if body == '20~':
                return 'F9'
        return 'ESC'
    if ch in ('\r', '\n'):
        return 'ENTER'
    if ch == '\x03':
        return 'CTRL_C'
    return ch if len(ch) == 1 else None


# ── 三市成交总额 ────────────────────────────────────────────

_market_total_cache = None
_market_history_refresh_date = None
_market_history_ready = False
_market_total_next_retry = 0.0
_market_lock = threading.Lock()


def load_market_total_cache():
    """加载3市场成交额历史缓存。"""
    data = load_json_cache(MARKET_TOTAL_CACHE_FILE, {})
    return data if isinstance(data, dict) else {}


def save_market_total_cache(cache):
    """原子保存3市场成交额历史缓存。"""
    save_json_cache(MARKET_TOTAL_CACHE_FILE, cache)


def get_market_total_cache():
    """获取全局缓存对象（首次调用时从磁盘加载）。"""
    global _market_total_cache
    if _market_total_cache is None:
        _market_total_cache = load_market_total_cache()
    return _market_total_cache


def fetch_index_kline_amounts(secid, days=20):
    """获取指数日线成交额历史，返回 [(date, amount), ...]。"""
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
    global _market_history_refresh_date, _market_history_ready, _market_total_next_retry
    with _market_lock:
        today_str = datetime.date.today().strftime('%Y-%m-%d')
        if _market_history_refresh_date == today_str or time.time() < _market_total_next_retry:
            return False
        _market_history_refresh_date = today_str
        cache = get_market_total_cache()
        # 周末不需要刷新历史成交额；缓存够用就直接标记可用
        if datetime.date.today().weekday() >= 5:
            cached_days = min(
                len(cache.get(secid, {})) for secid in MARKET_TOTAL_SECIDS.values()
            )
            if cached_days >= 6:
                _market_history_ready = True
                return False
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
        # 即使今天拉取失败，只要缓存里三个市场都有足够的历史交易日，
        # 仍可继续显示三市总额，避免网络波动导致整块数据消失。
        cached_days = min(
            len(cache.get(secid, {})) for secid in MARKET_TOTAL_SECIDS.values()
        )
        _market_history_ready = (
            fresh_count == len(MARKET_TOTAL_SECIDS) or cached_days >= 6
        )
        if fresh_count == len(MARKET_TOTAL_SECIDS):
            _market_total_next_retry = 0.0
        elif _market_history_ready:
            # 缓存够用，但今天没刷新成功：半小时后再试，避免频繁请求
            _market_history_refresh_date = None
            _market_total_next_retry = time.time() + 1800
        else:
            _market_history_refresh_date = None
            _market_total_next_retry = time.time() + 60
        return changed


def _trading_elapsed_minutes(time_str):
    """计算 A 股从开盘到指定时刻的累计交易分钟数（09:30-11:30, 13:00-15:00）。"""
    t = datetime.datetime.strptime(time_str, '%H:%M')
    minutes = t.hour * 60 + t.minute
    am_start = 9 * 60 + 30
    am_end = 11 * 60 + 30
    pm_start = 13 * 60
    pm_end = 15 * 60
    if minutes <= am_start:
        return 0
    if minutes <= am_end:
        return minutes - am_start
    if minutes <= pm_start:
        return am_end - am_start
    if minutes <= pm_end:
        return (am_end - am_start) + (minutes - pm_start)
    return (am_end - am_start) + (pm_end - pm_start)


def get_market_total_amounts(index_rows):
    """计算沪深京三市场今日成交总额及量比。

    Returns:
        dict: {'total_yi': 总额(亿), 'ratio': 量比} 或 None（数据不足）。
    """
    cache = get_market_total_cache()
    today = datetime.date.today()
    if not _market_history_ready or today.weekday() >= 5:
        return None
    today_str = today.strftime('%Y-%m-%d')

    today_amounts = {}
    for r in index_rows:
        secid = f"{r.get(F_MARKET, '0')}.{r.get(F_CODE, '')}"
        if secid in MARKET_TOTAL_SECIDS.values():
            try:
                today_amounts[secid] = float(r.get(F_AMT, 0) or 0)
            except (TypeError, ValueError):
                today_amounts[secid] = 0.0

    if len(today_amounts) != 3 or any(v <= 0 for v in today_amounts.values()):
        return None

    date_sets = [
        {d for d in cache.get(secid, {}) if d < today_str}
        for secid in MARKET_TOTAL_SECIDS.values()
    ]
    if not date_sets:
        return None
    dates = sorted(set.intersection(*date_sets), reverse=True)[:5]
    if len(dates) < 5:
        return None

    total_by_day = [
        sum(cache[secid][d] for secid in MARKET_TOTAL_SECIDS.values())
        for d in dates
    ]
    avg = sum(total_by_day) / len(total_by_day)
    today_total = sum(today_amounts.values())

    elapsed = _trading_elapsed_minutes(datetime.datetime.now().strftime('%H:%M'))
    if elapsed <= 0:
        return None

    total_minutes = 240
    ratio = (today_total / elapsed) / (avg / total_minutes) if avg > 0 else None
    return {
        'total_yi': round(today_total / 1e8, 2),
        'ratio': round(ratio, 2) if ratio is not None else None,
    }


def print_market_total(info, table_width=114):
    """在指数方块下方打印三市成交总额与量比。"""
    if not info or info.get('total_yi') is None or info.get('ratio') is None:
        return
    line = f"三市总额/量比 {info['total_yi']} / {info['ratio']}"
    print(pad_right(line, table_width))


def print_indices(rows, table_width=114):
    """以方块形式打印指数行情（--once 兼容接口）。"""
    print('-' * table_width)
    per_row = 5
    sep = ' | '
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
        return name + ' ' * max(0, pad) + price

    def fmt_bottom(right2):
        pad = box_w - display_width(right2)
        return ' ' * max(0, pad) + right2

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
        line1 += ' ' * max(0, table_width - display_width(line1))
        line2 += ' ' * max(0, table_width - display_width(line2))
        print(line1)
        print(line2)


# ── 日线数据：东财失败时用新浪兜底 ─────────────────────────

def _sina_kline_symbol(secid):
    """新浪日线代码：北交所也要用 bj 前缀。"""
    return secid_to_sina_code(secid).replace('s_', '', 1)


def fetch_sina_daily_klines(secid, limit=300):
    """新浪日线兜底，返回 [(date, close), ...]；失败返回 []。"""
    symbol = _sina_kline_symbol(secid)
    url = (
        "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        f"CN_MarketData.getKLineData?symbol={symbol}&scale=240&ma=no&datalen={limit}"
    )
    try:
        data = fetch(url, timeout=15,
                     extra_headers={'Referer': 'https://finance.sina.com.cn/'})
        if isinstance(data, dict):
            data = data.get('result', {}).get('data') or []
        result = []
        for item in data or []:
            if not isinstance(item, dict):
                continue
            day = item.get('day')
            close = item.get('close')
            if day and close not in (None, ''):
                result.append((str(day), float(close)))
        return result
    except Exception as e:
        log_message(f'Sina kline fallback failed for {secid}: {e}')
        return []


def _fetch_daily_klines(secid, limit=300):
    """返回 [(date, close), ...]：优先东财 push2his，失败自动用新浪。"""
    url = (
        "https://push2his.eastmoney.com/api/qt/stock/kline/get?"
        f"secid={secid}&fields1=f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13&"
        "fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61&"
        f"klt=101&fqt=0&end=20500101&lmt={limit}"
    )
    try:
        data = fetch(url, timeout=15,
                     extra_headers={'Referer': 'https://quote.eastmoney.com/'})
        result = []
        for k in data.get('data', {}).get('klines', []):
            parts = k.split(',')
            if len(parts) >= 3:
                result.append((parts[0], float(parts[2])))
        if result:
            return result
    except Exception as e:
        log_message(f'EastMoney kline fetch failed for {secid}: {e}')
    return fetch_sina_daily_klines(secid, limit)


# ── 半年涨跌幅（--hy on） ───────────────────────────────────

_hy_cache = None
_hy_lock = threading.Lock()
_hy_thread = None
_hy_result = None


def load_hy_cache():
    global _hy_cache
    if _hy_cache is None:
        data = load_json_cache(HY_CACHE_FILE, {})
        _hy_cache = data if isinstance(data, dict) else {}
    return _hy_cache


def save_hy_cache():
    save_json_cache(HY_CACHE_FILE, get_hy_cache())


def get_hy_cache():
    return load_hy_cache()


def _minus_months(d, months):
    month = d.month - months
    year = d.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return datetime.date(year, month, day)


def fetch_half_year_pct(secid):
    """获取单只股票相对半年前的涨跌幅（%）。"""
    try:
        klines = _fetch_daily_klines(secid, limit=200)
        if not klines:
            return None
        latest_date_str, current_price = klines[-1]
        latest_date = datetime.datetime.strptime(latest_date_str, '%Y-%m-%d').date()
        target_date = _minus_months(latest_date, 6)
        target_price = None
        for date_str, close in klines:
            try:
                d = datetime.datetime.strptime(date_str, '%Y-%m-%d').date()
            except ValueError:
                continue
            if d >= target_date:
                target_price = close
                break
        if target_price is None or target_price == 0:
            return None
        return round((current_price - target_price) / target_price * 100, 2)
    except Exception:
        return None


def _hy_worker(secids):
    result = {}
    today = datetime.date.today().strftime('%Y-%m-%d')
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        future_to_secid = {ex.submit(fetch_half_year_pct, s): s for s in secids}
        for future in concurrent.futures.as_completed(future_to_secid):
            secid = future_to_secid[future]
            try:
                pct = future.result()
                if pct is not None:
                    result[secid] = {'pct': pct, 'date': today}
            except Exception:
                pass
    return result


def start_half_year_update(secids):
    """如尚未在更新且存在待更新 secid，则在后台启动半年涨跌抓取。"""
    global _hy_thread, _hy_result
    with _hy_lock:
        if _hy_thread is not None and _hy_thread.is_alive():
            return False
        today = datetime.date.today().strftime('%Y-%m-%d')
        cache = get_hy_cache()
        need = []
        for secid in secids:
            cached = cache.get(secid)
            if not isinstance(cached, dict) or cached.get('date') != today:
                need.append(secid)
        if not need:
            return False
        _hy_result = None

        def run():
            global _hy_result
            _hy_result = _hy_worker(need)

        _hy_thread = threading.Thread(target=run, daemon=True)
        _hy_thread.start()
        return True


def complete_half_year_update():
    """将已完成的半年涨跌后台结果合并进缓存并落盘。"""
    global _hy_thread, _hy_result
    with _hy_lock:
        if _hy_thread is None or _hy_thread.is_alive():
            return False
        if _hy_result is not None:
            cache = get_hy_cache()
            cache.update(_hy_result)
            save_hy_cache()
        _hy_thread = None
        _hy_result = None
        return True


def apply_half_year_pct(rows):
    """为行情行附加 'hyPct' 字段（来自缓存），无缓存则为 None。"""
    cache = get_hy_cache()
    for r in rows:
        secid = _row_secid(r)
        cached = cache.get(secid)
        r['hyPct'] = cached.get('pct') if isinstance(cached, dict) else None


# ── 日均线 / PE PB（盘口详情用） ─────────────────────────────

_ma_cache = None
_ma_lock = threading.Lock()
_ma_thread = None
_ma_result = None

_pepb_cache = None
_pepb_lock = threading.Lock()
_pepb_thread = None
_pepb_result = None


def _get_ma_cache():
    global _ma_cache
    if _ma_cache is None:
        data = load_json_cache(MA_CACHE_FILE, {})
        _ma_cache = data if isinstance(data, dict) else {}
    return _ma_cache


def fetch_daily_closes(secid, limit=300):
    """获取日线收盘价序列（f53=收盘），东财失败时用新浪兜底。"""
    return [close for _date, close in _fetch_daily_klines(secid, limit)]


def _compute_mas(closes):
    entry = {'date': datetime.date.today().strftime('%Y-%m-%d')}
    for period in (10, 20, 30, 120, 250):
        if len(closes) >= period:
            entry[f'ma{period}'] = round(sum(closes[-period:]) / period, 2)
    return entry


def _ma_worker(secids):
    result = {}
    for secid in secids:
        closes = fetch_daily_closes(secid)
        if len(closes) >= 10:
            result[secid] = _compute_mas(closes)
        else:
            # 记下当天已尝试，避免每次重绘都重复请求
            result[secid] = {'date': datetime.date.today().strftime('%Y-%m-%d')}
    return result


def start_ma_update(secids):
    global _ma_thread, _ma_result
    with _ma_lock:
        if _ma_thread is not None and _ma_thread.is_alive():
            return False
        today = datetime.date.today().strftime('%Y-%m-%d')
        need = []
        for secid in secids:
            cached = _get_ma_cache().get(secid)
            if not isinstance(cached, dict) or cached.get('date') != today:
                need.append(secid)
        if not need:
            return False
        _ma_result = None

        def run():
            global _ma_result
            _ma_result = _ma_worker(need)

        _ma_thread = threading.Thread(target=run, daemon=True)
        _ma_thread.start()
        return True


def complete_ma_update():
    global _ma_thread, _ma_result
    with _ma_lock:
        if _ma_thread is None or _ma_thread.is_alive():
            return False
        if _ma_result:
            cache = _get_ma_cache()
            cache.update(_ma_result)
            save_json_cache(MA_CACHE_FILE, cache)
        _ma_thread = None
        _ma_result = None
        return True


def get_daily_mas(secid):
    """只读当天缓存；缺失时启动后台补齐并返回 None。"""
    today = datetime.date.today().strftime('%Y-%m-%d')
    cached = _get_ma_cache().get(secid)
    if isinstance(cached, dict) and cached.get('date') == today:
        if any(cached.get(f'ma{p}') is not None for p in (10, 20, 30, 120, 250)):
            return cached
        return None
    start_ma_update([secid])
    return None


def _get_pepb_cache():
    global _pepb_cache
    if _pepb_cache is None:
        data = load_json_cache(PEPB_CACHE_FILE, {})
        _pepb_cache = data if isinstance(data, dict) else {}
    return _pepb_cache


def fetch_pepb_data(secid):
    url = (
        "https://push2.eastmoney.com/api/qt/ulist.np/get?"
        f"fltt=2&invt=2&fields=f9,f23&secids={secid}"
    )
    try:
        data = fetch(url, timeout=15,
                     extra_headers={'Referer': 'https://quote.eastmoney.com/'})
        diff = data.get('data', {}).get('diff') or []
        if isinstance(diff, dict):
            diff = list(diff.values())
        if diff:
            row = diff[0]
            pe, pb = row.get('f9'), row.get('f23')
            if pe not in (None, '-', '') and pb not in (None, '-', ''):
                return {'pe': round(float(pe), 2), 'pb': round(float(pb), 2)}
    except Exception:
        pass
    return None


def _pepb_worker(secids):
    result = {}
    for secid in secids:
        entry = fetch_pepb_data(secid)
        # 失败也写入当天占位，避免每次重绘都重复请求
        result[secid] = entry if entry is not None else {'pe': None, 'pb': None}
        time.sleep(0.3)  # 错时请求，避免被限流
    return result


def start_pepb_update(secids):
    global _pepb_thread, _pepb_result
    with _pepb_lock:
        if _pepb_thread is not None and _pepb_thread.is_alive():
            return False
        today = datetime.date.today().strftime('%Y-%m-%d')
        need = []
        for secid in secids:
            cached = _get_pepb_cache().get(secid)
            if not isinstance(cached, dict) or cached.get('date') != today:
                need.append(secid)
        if not need:
            return False
        _pepb_result = None

        def run():
            global _pepb_result
            _pepb_result = _pepb_worker(need)

        _pepb_thread = threading.Thread(target=run, daemon=True)
        _pepb_thread.start()
        return True


def complete_pepb_update():
    global _pepb_thread, _pepb_result
    with _pepb_lock:
        if _pepb_thread is None or _pepb_thread.is_alive():
            return False
        today = datetime.date.today().strftime('%Y-%m-%d')
        if _pepb_result:
            cache = _get_pepb_cache()
            for secid, entry in _pepb_result.items():
                cache[secid] = {'date': today, 'pe': entry['pe'], 'pb': entry['pb']}
            save_json_cache(PEPB_CACHE_FILE, cache)
        _pepb_thread = None
        _pepb_result = None
        return True


def get_pepb(secid):
    """只读当天缓存；缺失时启动后台补齐并返回 None。"""
    today = datetime.date.today().strftime('%Y-%m-%d')
    cached = _get_pepb_cache().get(secid)
    if isinstance(cached, dict) and cached.get('date') == today:
        if cached.get('pe') is None and cached.get('pb') is None:
            return None
        return {'pe': cached.get('pe'), 'pb': cached.get('pb')}
    start_pepb_update([secid])
    return None
