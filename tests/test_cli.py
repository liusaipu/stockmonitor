import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import quotes_core  # noqa: E402
import watch_quotes  # noqa: E402


class ArgumentTests(unittest.TestCase):
    def test_defaults_match_windows_version(self):
        cfg = watch_quotes.parse_args(['watch'])
        self.assertEqual(cfg['interval'], 15)
        self.assertFalse(cfg['show_index'])
        self.assertTrue(cfg['stealth'])
        self.assertFalse(cfg['fetch_hy'])
        self.assertFalse(cfg['once'])

    def test_all_options(self):
        cfg = watch_quotes.parse_args([
            'watch', '30', '--index', 'on', '--stealth', 'off',
            '--hy', 'on', '--once',
        ])
        self.assertEqual(cfg['interval'], 30)
        self.assertTrue(cfg['show_index'])
        self.assertFalse(cfg['stealth'])
        self.assertTrue(cfg['fetch_hy'])
        self.assertTrue(cfg['once'])

    def test_equals_form_and_single_dash(self):
        cfg = watch_quotes.parse_args(['watch', '--index=on', '-stealth=off'])
        self.assertTrue(cfg['show_index'])
        self.assertFalse(cfg['stealth'])

    def test_help(self):
        self.assertTrue(watch_quotes.parse_args(['watch', '--help'])['help'])
        self.assertTrue(watch_quotes.parse_args(['watch', '-h'])['help'])

    def test_invalid_arguments(self):
        cases = [
            ['watch', '--index'],
            ['watch', '--index', 'maybe'],
            ['watch', '--index', '--once'],
            ['watch', '--bogus'],
            ['watch', '0'],
            ['watch', 'abc'],
        ]
        for argv in cases:
            with self.subTest(argv=argv):
                with self.assertRaises(watch_quotes.UsageError):
                    watch_quotes.parse_args(argv)


class CliProcessTests(unittest.TestCase):
    def _run(self, args, timeout=10):
        return subprocess.run(
            [sys.executable, os.path.join(ROOT, 'watch_quotes.py')] + args,
            cwd=ROOT, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=timeout,
        )

    def test_help_exit_code(self):
        proc = self._run(['--help'])
        self.assertEqual(proc.returncode, 0)
        self.assertIn('用法: watch', proc.stdout.decode('utf-8'))

    def test_usage_error_is_not_a_traceback(self):
        proc = self._run(['--index'])
        self.assertEqual(proc.returncode, 2)
        stderr = proc.stderr.decode('utf-8')
        self.assertIn('错误:', stderr)
        self.assertNotIn('Traceback', stderr)

    def test_interactive_requires_a_terminal(self):
        proc = self._run([])
        self.assertEqual(proc.returncode, 2)
        self.assertIn('需要在终端', proc.stderr.decode('utf-8'))


class FormatTests(unittest.TestCase):
    def test_display_width_and_truncate(self):
        self.assertEqual(quotes_core.display_width('中芯国际'), 8)
        self.assertEqual(quotes_core.display_width('ABC'), 3)
        self.assertEqual(quotes_core.truncate_to_width('瑞联新材股份', 8), '瑞联新材')
        self.assertEqual(quotes_core.pad_left('1', 3), '  1')
        self.assertEqual(quotes_core.pad_right('1', 3), '1  ')

    def test_sina_symbols(self):
        cases = {
            '1.600519': 'sh600519',
            '0.000001': 'sz000001',
            '1.000001': 's_sh000001',
            '0.399001': 's_sz399001',
            '0.899050': 's_bj899050',
            '0.430047': 'bj430047',
        }
        for secid, expected in cases.items():
            with self.subTest(secid=secid):
                self.assertEqual(quotes_core.secid_to_sina_code(secid), expected)

    def test_sina_kline_symbols(self):
        self.assertEqual(quotes_core._sina_kline_symbol('1.000001'), 'sh000001')
        self.assertEqual(quotes_core._sina_kline_symbol('0.399001'), 'sz399001')
        self.assertEqual(quotes_core._sina_kline_symbol('0.899050'), 'bj899050')
        self.assertEqual(quotes_core._sina_kline_symbol('0.430047'), 'bj430047')

    def test_sina_quote_parser(self):
        text = (
            'var hq_str_sh600519="贵州茅台,1450.000,1452.000,1453.000,'
            '1457.720,1436.000,1452.000,1453.000,4284772,6199528289.000";\n'
            'var hq_str_s_sh000001="上证指数,3649.18,-40.73,-1.10,'
            '123456,789012";\n'
        )
        rows = quotes_core.parse_sina_quote_response(text)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][quotes_core.F_NAME], '贵州茅台')
        self.assertEqual(rows[0][quotes_core.F_CODE], '600519')
        self.assertEqual(rows[0][quotes_core.F_VOL], '42847')
        self.assertEqual(rows[1][quotes_core.F_CODE], '000001')
        self.assertEqual(rows[1][quotes_core.F_NAME], '上证指数')


class CacheTests(unittest.TestCase):
    def test_loads_bom_and_saves_without_bom(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'cache.json')
            with open(path, 'w', encoding='utf-8-sig') as f:
                json.dump({'中芯国际': '1.688981'}, f, ensure_ascii=False)
            data = quotes_core.load_json_cache(path)
            self.assertEqual(data['中芯国际'], '1.688981')

            quotes_core.save_json_cache(path, data)
            with open(path, 'rb') as f:
                raw = f.read()
            self.assertFalse(raw.startswith(b'\xef\xbb\xbf'))
            self.assertEqual(quotes_core.load_json_cache(path)['中芯国际'], '1.688981')

    def test_corrupt_cache_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'cache.json')
            with open(path, 'w', encoding='utf-8') as f:
                f.write('{not json')
            self.assertEqual(quotes_core.load_json_cache(path, {}), {})


class RenderTests(unittest.TestCase):
    def _state(self):
        state = watch_quotes.AppState()
        state.stock_ids = ['1.600519']
        state.index_ids = []
        state.rows['1.600519'] = {
            quotes_core.F_CODE: '600519',
            quotes_core.F_MARKET: '1',
            quotes_core.F_NAME: '贵州茅台',
            quotes_core.F_PRICE: '153.00',
            quotes_core.F_PCT_CHG: '0.07',
            quotes_core.F_CHG: '1.00',
            quotes_core.F_PREV_CLOSE: '152.00',
            quotes_core.F_OPEN: '150.00',
            quotes_core.F_HIGH: '157.72',
            quotes_core.F_LOW: '136.00',
            quotes_core.F_VOL: '42847',
            quotes_core.F_AMT: '6199528289',
            quotes_core.F_TURNOVER: '0.34',
            quotes_core.F_VOL_RATIO: '1.02',
            'hyPct': 5.0,
        }
        state.loaded = True
        return state

    def test_stealth_list_layout(self):
        cfg = {'interval': 15, 'show_index': False, 'stealth': True,
               'fetch_hy': False, 'once': True}
        lines = watch_quotes.build_list_lines(
            self._state(), cfg, {'c': 0, 'v': 0}, cursor=0,
            term_height=0, include_buttons=False,
        )
        text = '\n'.join(line for line, _ in lines)
        self.assertIn('System Monitor', text)
        self.assertIn('600519', text)
        self.assertIn('贵州茅台', text)
        self.assertIn('0.07%', text)

    def test_normal_list_layout(self):
        cfg = {'interval': 15, 'show_index': True, 'stealth': False,
               'fetch_hy': False, 'once': True}
        state = self._state()
        old_cache = quotes_core._hy_cache
        quotes_core._hy_cache = {'1.600519': {'pct': 5.0, 'date': '2099-01-01'}}
        try:
            lines = watch_quotes.build_list_lines(
                state, cfg, {'c': 0, 'v': 0}, cursor=0,
                term_height=0, include_buttons=False,
            )
        finally:
            quotes_core._hy_cache = old_cache
        text = '\n'.join(line for line, _ in lines)
        self.assertIn('自选股行情', text)
        self.assertIn('半年涨跌', text)
        self.assertIn('5.00%', text)

    def test_row_widths_match_powershell_layout(self):
        state = self._state()
        row = state.rows['1.600519']
        self.assertEqual(quotes_core.display_width(
            watch_quotes._fmt_row_stealth(row, True, False)), 64)
        self.assertEqual(quotes_core.display_width(
            watch_quotes._fmt_row_normal(row, True, False)), 114)

    def test_depth_layout(self):
        state = self._state()
        row = state.rows['1.600519']
        depth = watch_quotes._build_depth_skeleton(row)
        depth.update({
            'price': 1453.0,
            'change': 1.0,
            'pct_chg': 0.07,
            'limit_up': 1597.2,
            'limit_down': 1306.8,
            'amount': 6199528289.0,
            'volume': 42847.0,
            'asks': [(1453.1 + i * 0.1, 100 + i) for i in range(5)],
            'bids': [(1452.9 - i * 0.1, 200 + i) for i in range(5)],
            '_fetched_at': '2026-11-07 10:00:00',
        })
        cfg = {'interval': 15, 'show_index': False, 'stealth': True,
               'fetch_hy': False, 'once': True}
        old_mas, old_pepb = watch_quotes.get_daily_mas, watch_quotes.get_pepb
        watch_quotes.get_daily_mas = lambda secid: {
            'date': '2099-01-01', 'ma10': 1, 'ma20': 2, 'ma30': 3,
            'ma120': 4, 'ma250': 5,
        }
        watch_quotes.get_pepb = lambda secid: {'pe': 20.5, 'pb': 5.5}
        try:
            lines = watch_quotes.build_depth_lines(
                state, cfg, depth, row, status='', include_buttons=False)
        finally:
            watch_quotes.get_daily_mas, watch_quotes.get_pepb = old_mas, old_pepb
        text = '\n'.join(line for line, _ in lines)
        self.assertIn('卖5', text)
        self.assertIn('买1', text)
        self.assertIn('均价', text)
        self.assertIn('PE', text)
        self.assertIn('盘口数据时间', text)


if __name__ == '__main__':
    unittest.main()
