# -*- coding: utf-8 -*-
"""
run_test_and_log.py - CLI entry for TestLogger
"""

import sys, os, json, argparse

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

if sys.platform == 'win32' and hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

from utils.test_logger import TestLogger, MD_LOG, JSON_LOG
from config.settings import DEFAULT_SYMBOLS, RISK_CONFIG
from utils.proxy import safe_clean_proxy


def list_history():
    if os.path.exists(MD_LOG):
        with open(MD_LOG, 'r', encoding='utf-8') as f:
            print(f.read())
    else:
        print('No history yet.')


def list_summary():
    if not os.path.exists(JSON_LOG):
        print('No history data yet.')
        return
    try:
        with open(JSON_LOG, 'r', encoding='utf-8') as f:
            records = json.load(f)
        if not records:
            print('No records yet.')
            return
        print()
        print('=' * 120)
        print('  Test History Summary')
        print('=' * 120)
        print(f'  {"Run":<6} {"Description":<20} {"Ann.Ret":>9} {"Sharpe":>7} {"MaxDD":>9} {"Trades":>7} {"Final":>12} {"WinRate":>8} {"Vol":>7} {"PFactor":>8}')
        print(f'  {"-"*6} {"-"*20} {"-"*9} {"-"*7} {"-"*9} {"-"*7} {"-"*12} {"-"*8} {"-"*7} {"-"*8}')
        for r in records:
            ann = r.get('annual_return', 0)
            desc = str(r.get('description', '')).replace('"', '').replace("'", '')
            print(f"  {r['run_id']:<6} {desc:<20} {ann:>+8.2%} {r.get('sharpe_ratio',0):>6.2f} {r.get('max_drawdown',0):>8.2%} {r.get('total_trades',0):>7d} {r.get('final_value',0):>12,.0f} {r.get('win_rate',0):>7.2%} {r.get('volatility',0):>6.2%} {r.get('profit_factor',0):>7.2f}")
        print('=' * 120)
    except Exception as e:
        import traceback
        print(f'Read failed: {e}')
        traceback.print_exc()
def main():
    safe_clean_proxy()

    parser = argparse.ArgumentParser(description='Backtest Runner with logging')
    parser.add_argument('--desc', '-d', default='', help='Test description')
    parser.add_argument('--symbols', '-s', nargs='+', default=None, help='Stock symbols')
    parser.add_argument('--config', '-c', type=str, default=None, help='Risk config JSON')
    parser.add_argument('--notes', '-n', default='', help='Notes')
    parser.add_argument('--list', '-l', action='store_true', help='Full history')
    parser.add_argument('--summary', '-S', action='store_true', help='History summary')

    args = parser.parse_args()

    if args.list:
        list_history()
        return
    if args.summary:
        list_summary()
        return

    risk_config = None
    if args.config:
        try:
            user_cfg = json.loads(args.config)
            base = dict(RISK_CONFIG)
            base.update(user_cfg)
            risk_config = base
        except json.JSONDecodeError as e:
            print(f'Config JSON parse failed: {e}')
            return

    symbols = args.symbols
    desc = args.desc or '|'.join(symbols) if symbols else 'default'

    logger = TestLogger()
    logger.run_and_log(
        description=desc,
        symbols=symbols,
        risk_config=risk_config,
        notes=args.notes,
    )

    print('\nHistory summary:')
    list_summary()


if __name__ == '__main__':
    main()

