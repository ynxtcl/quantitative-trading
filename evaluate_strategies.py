#!/usr/bin/env python3
"""
========================================
 策略独立年化评估 — 用于数据驱动定权重
========================================

【为什么要做独立评估？】
组合中三个策略互相影响（资金争夺），无法从组合结果倒推单个策略贡献。
本脚本让每个策略"单独跑"——其他策略不产生信号，只看它自己的年化表现。

【输出】
每个策略的独立年化收益率 → 按年化比例算权重
"""
import sys, os
project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from utils.proxy import safe_clean_proxy
safe_clean_proxy()

from data.loader import DataLoader
from data.cleaner import clean_daily_data
from config.settings import DEFAULT_SYMBOLS, BACKTEST_CONFIG
from config.strategy_config import (
    TREND_FOLLOWING_CONFIG,
    MEAN_REVERSION_CONFIG,
    FACTOR_SELECTION_CONFIG,
)
from strategies.trend_following.strategy import TrendFollowingStrategy
from strategies.mean_reversion.strategy import MeanReversionStrategy
from strategies.factor_rebalancer import FactorRebalancer
from portfolio.engine import PortfolioEngine
from backtest.portfolio_reporter import PortfolioReporter

import warnings
warnings.filterwarnings('ignore')


def run_single_strategy(data_dict: dict, label: str,
                        tf_strategies=None, mr_strategies=None,
                        rebalancer=None):
    """
    只跑一个策略的组合回测（其他策略不产生信号）
    
    返回: (final_value, total_return, annual_return)
    """
    engine = PortfolioEngine(BACKTEST_CONFIG)
    result = engine.run(
        data_dict=data_dict,
        tf_strategies=tf_strategies or {},
        mr_strategies=mr_strategies or {},
        rebalancer=rebalancer,
        combiner=None,      # 单策略不需要权重求和
        risk_manager=None,  # 单策略不需要组合风控
    )
    return result


def main():
    print("=" * 65)
    print("  策略独立年化评估")
    print("  目的：每个策略单独跑，看各自的年化收益率")
    print("=" * 65)

    # ============ 加载数据 ============
    loader = DataLoader()
    symbols = DEFAULT_SYMBOLS[:3]
    data_dict = loader.load_multiple(
        symbols,
        start=BACKTEST_CONFIG['start_date'],
        end=BACKTEST_CONFIG['end_date'],
    )
    if not data_dict:
        print("数据加载失败")
        return
    
    cleaned_data = {}
    for sym, df in data_dict.items():
        cleaned_data[sym] = clean_daily_data(df)

    # ============ 创建策略实例 ============
    # 趋势跟踪
    tf_strategies = {}
    for sym in symbols:
        cfg = dict(TREND_FOLLOWING_CONFIG)
        cfg['symbol'] = sym
        tf_strategies[sym] = TrendFollowingStrategy('trend_following', cfg)

    # 均值回归
    mr_strategies = {}
    for sym in symbols:
        cfg = dict(MEAN_REVERSION_CONFIG)
        cfg['symbol'] = sym
        mr_strategies[sym] = MeanReversionStrategy('mean_reversion', cfg)

    # 因子选股
    rebalancer = FactorRebalancer(FACTOR_SELECTION_CONFIG)

    # ============ 逐个策略独立回测 ============
    results = {}

    print(f"\n{'─' * 50}")
    print("  [1/3] 趋势跟踪（独立运行）")
    print(f"{'─' * 50}")
    r = run_single_strategy(cleaned_data, 'TF',
                            tf_strategies=tf_strategies)
    results['trend_following'] = {
        'final': r.final_value(),
        'total_return': r.total_return(),
        'annual_return': r.annual_return(),
        'trades': len(r.trades),
    }
    print(f"  最终资产: {r.final_value():>10,.2f}")
    print(f"  总收益率: {r.total_return():>+8.2%}")
    print(f"  年化收益: {r.annual_return():>+8.2%}")
    print(f"  交易笔数: {len(r.trades)}")

    print(f"\n{'─' * 50}")
    print("  [2/3] 均值回归（独立运行）")
    print(f"{'─' * 50}")
    r = run_single_strategy(cleaned_data, 'MR',
                            mr_strategies=mr_strategies)
    results['mean_reversion'] = {
        'final': r.final_value(),
        'total_return': r.total_return(),
        'annual_return': r.annual_return(),
        'trades': len(r.trades),
    }
    print(f"  最终资产: {r.final_value():>10,.2f}")
    print(f"  总收益率: {r.total_return():>+8.2%}")
    print(f"  年化收益: {r.annual_return():>+8.2%}")
    print(f"  交易笔数: {len(r.trades)}")

    print(f"\n{'─' * 50}")
    print("  [3/3] 因子选股（独立运行）")
    print(f"{'─' * 50}")
    r = run_single_strategy(cleaned_data, 'FS',
                            rebalancer=rebalancer)
    results['factor_selection'] = {
        'final': r.final_value(),
        'total_return': r.total_return(),
        'annual_return': r.annual_return(),
        'trades': len(r.trades),
    }
    print(f"  最终资产: {r.final_value():>10,.2f}")
    print(f"  总收益率: {r.total_return():>+8.2%}")
    print(f"  年化收益: {r.annual_return():>+8.2%}")
    print(f"  交易笔数: {len(r.trades)}")

    # ============ 按年化计算最优权重 ============
    print(f"\n{'=' * 65}")
    print(f"  按年化收益率比例定权重")
    print(f"{'=' * 65}")

    # 取年化收益率
    ann_returns = {
        name: max(res['annual_return'], 0)  # 负年化按0处理（不放钱）
        for name, res in results.items()
    }
    total_ann = sum(ann_returns.values())

    if total_ann > 0:
        weights = {
            name: ann / total_ann
            for name, ann in ann_returns.items()
        }
    else:
        # 全都负收益 → 均等分配
        weights = {name: 1/3 for name in results}

    print(f"\n  策略        年化收益率    计算权重")
    print(f"  {'─' * 40}")
    for name in ['trend_following', 'mean_reversion', 'factor_selection']:
        res = results[name]
        w = weights[name]
        print(f"  {name:<18} {res['annual_return']:>+7.2%}    →  {w:.2f}")

    print(f"\n  推荐组合权重分配:")
    print(f"    趋势跟踪 (TF): {weights.get('trend_following', 0):.2f}")
    print(f"    均值回归 (MR): {weights.get('mean_reversion', 0):.2f}")
    print(f"    因子选股 (FS): {weights.get('factor_selection', 0):.2f}")
    print(f"    ─────────────────")
    print(f"    合计:          {sum(weights.values()):.2f}")

    print(f"\n{'=' * 65}")
    print(f"  评估完成！可更新 config/strategy_config.py 中的 weight 字段")
    print(f"{'=' * 65}")


if __name__ == "__main__":
    main()
