"""
========================================
  main_live.py — Mock 实盘交易入口
========================================

【用法】
python main_live.py                    # 默认加速回放
python main_live.py --speed 1          # 实时速度
python main_live.py --symbols 000001 000333
python main_live.py --capital 200000
python main_live.py --days 50          # 只跑50天
python main_live.py --max-days 252     # 跑一年

【输出】
- 控制台结构化日志（通过 ops.logger）
- 模拟券商内部记录资金/持仓变动
- 最终总结：收益/回撤/年化
"""
import argparse
import sys
import os

# 确保项目根目录在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from trading.live_engine import LiveEngine
from config.settings import DEFAULT_SYMBOLS, TRADING_CONFIG


def parse_args():
    parser = argparse.ArgumentParser(
        description='定量交易系统 — Mock 实盘模拟'
    )
    parser.add_argument(
        '--symbols', nargs='+', default=DEFAULT_SYMBOLS[:3],
        help='交易标的列表 (默认: 沪深300前3只)'
    )
    parser.add_argument(
        '--capital', type=float, default=TRADING_CONFIG.get('initial_capital', 100000.0),
        help='初始资金 (默认: 100000)'
    )
    parser.add_argument(
        '--speed', type=float, default=50.0,
        help='回放速度倍率 (0=瞬间, 1=实时, 50=加速, 默认: 50)'
    )
    parser.add_argument(
        '--days', type=int, default=None,
        help='运行天数限制 (默认: 全部)'
    )
    parser.add_argument(
        '--start', type=str, default=None,
        help='回测开始日期 (YYYY-MM-DD, 默认: 配置中的 start_date)'
    )
    parser.add_argument(
        '--end', type=str, default=None,
        help='回测结束日期 (YYYY-MM-DD, 默认: 配置中的 end_date)'
    )
    return parser.parse_args()


def main():
    args = parse_args()

    print("=" * 60)
    print("  定量交易系统 v1.0 — Mock 实盘模拟")
    print("=" * 60)
    print(f"  模式: Mock 模拟盘")
    print(f"  标的: {', '.join(args.symbols)}")
    print(f"  资金: ¥{args.capital:,.2f}")
    print(f"  速度: {args.speed}x")
    print(f"  天数: {'全部' if args.days is None else args.days}")
    print("=" * 60)

    engine = LiveEngine(
        symbols=args.symbols,
        speed=args.speed,
        initial_capital=args.capital,
    )

    # 自定义日期范围
    if args.start:
        engine.data.start_date = args.start
    if args.end:
        engine.data.end_date = args.end

    engine.run(max_days=args.days)

    # 输出净值曲线摘要
    curve = engine.get_equity_curve()
    if curve:
        print(f"\n交易日: {len(curve)} 天")
        print(f"初始值: ¥{curve[0]['value']:,.2f}")
        print(f"最终值: ¥{curve[-1]['value']:,.2f}")
        print(f"收益率: {(curve[-1]['value']/curve[0]['value']-1)*100:.2f}%")


if __name__ == '__main__':
    main()
