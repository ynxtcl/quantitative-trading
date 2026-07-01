#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Diagnostic script - quickly verify risk control parameter impact on portfolio backtest.

Run: python diagnose_risk_impact.py

Tests 5 scenarios:
1. No risk control (bare strategy)
2. Current risk config (stop-loss 8%/drawdown 25%)
3. Loose risk (stop-loss 15%/drawdown 35%)
4. Mid risk (stop-loss 12%/drawdown 30%)
5. Minimal risk (only stop-loss 20%)
"""
import sys
import os
project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Force UTF-8 for stdout
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
elif hasattr(sys.stdout, 'buffer'):
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from data.loader import DataLoader
from data.cleaner import clean_daily_data
from config.settings import DEFAULT_SYMBOLS, BACKTEST_CONFIG, RISK_CONFIG
from config.strategy_config import (
    TREND_FOLLOWING_CONFIG, MEAN_REVERSION_CONFIG, FACTOR_SELECTION_CONFIG,
)
from strategies.trend_following.strategy import TrendFollowingStrategy
from strategies.mean_reversion.strategy import MeanReversionStrategy
from strategies.factor_rebalancer import FactorRebalancer
from portfolio.combiner import PortfolioCombiner
from portfolio.risk_manager import RiskManager
from portfolio.engine import PortfolioEngine
from backtest.metrics import calculate_metrics

import warnings
warnings.filterwarnings('ignore')


def load_and_prepare_data(symbols):
    """Load and clean data"""
    from utils.proxy import safe_clean_proxy
    safe_clean_proxy()

    loader = DataLoader()
    print("\n[Data Loading]")
    data_dict = loader.load_multiple(
        symbols,
        start=BACKTEST_CONFIG['start_date'],
        end=BACKTEST_CONFIG['end_date'],
    )
    if not data_dict:
        print("Data loading failed!")
        return None

    cleaned = {}
    for sym, df in data_dict.items():
        cleaned[sym] = clean_daily_data(df)
    first_df = list(cleaned.values())[0] if cleaned else None
    days = len(first_df) if first_df is not None else 0
    print(f"  Stocks: {len(cleaned)}, ~{days} days each")
    return cleaned


def create_strategies(symbols):
    """Create strategy and portfolio components"""
    tf_strategies = {}
    mr_strategies = {}
    for sym in symbols:
        tf_cfg = dict(TREND_FOLLOWING_CONFIG)
        tf_cfg['symbol'] = sym
        tf_strategies[sym] = TrendFollowingStrategy('trend_following', tf_cfg)

        mr_cfg = dict(MEAN_REVERSION_CONFIG)
        mr_cfg['symbol'] = sym
        mr_strategies[sym] = MeanReversionStrategy('mean_reversion', mr_cfg)

    rebalancer = FactorRebalancer(FACTOR_SELECTION_CONFIG)

    combiner = PortfolioCombiner()
    combiner.set_weights({
        'trend_following': TREND_FOLLOWING_CONFIG.get('weight', 0.4),
        'mean_reversion': MEAN_REVERSION_CONFIG.get('weight', 0.3),
        'factor_selection': FACTOR_SELECTION_CONFIG.get('weight', 0.3),
    })

    return tf_strategies, mr_strategies, rebalancer, combiner


def run_backtest(data_dict, risk_manager, scenario_name):
    """Run a single backtest and print key metrics"""
    symbols = list(data_dict.keys())
    tf, mr, rebalancer, combiner = create_strategies(symbols)

    engine = PortfolioEngine(BACKTEST_CONFIG)

    print(f"\n{'=' * 65}")
    print(f"  Scenario: {scenario_name}")
    print(f"{'=' * 65}")

    result = engine.run(
        data_dict=data_dict,
        tf_strategies=tf,
        mr_strategies=mr,
        rebalancer=rebalancer,
        combiner=combiner,
        risk_manager=risk_manager,
    )

    # Prepare data for metrics calculation
    df = result.to_dataframe()
    daily_values = df['total_value'].tolist()
    trade_dicts = []
    for t in result.trades:
        trade_dicts.append({
            'pnl': t.value - t.cost if t.direction == 1 else -(t.value + t.cost),
            'direction': t.direction,
            'symbol': t.symbol,
            'price': t.price,
            'quantity': t.quantity,
        })

    metrics = calculate_metrics(daily_values, trade_dicts, result.initial_capital)
    total_trades = len(result.trades)

    print(f"  Final Value: {result.final_value():>12,.2f}")
    print(f"  Total Return: {result.total_return():>+9.2%}")
    print(f"  Annual Return: {metrics.annual_return:>+9.2%}")
    print(f"  Sharpe Ratio: {metrics.sharpe_ratio:>8.2f}")
    print(f"  Max Drawdown: {metrics.max_drawdown:>9.2%}")
    print(f"  Annual Volatility: {metrics.volatility:>8.2%}")
    print(f"  Total Trades: {total_trades}")
    print(f"  Stop-Loss Triggered: {engine.stop_loss_triggered} times")
    print(f"  Win Rate: {metrics.win_rate:>6.2%}")
    print(f"  Profit Factor: {metrics.profit_factor:>6.2f}")

    return result, metrics, engine


def main():
    print("=" * 65)
    print("  Quantitative Trading - Risk Control Impact Diagnosis")
    print("=" * 65)
    print("  Goal: Isolate risk control vs strategy impact")
    print("=" * 65)

    # Use 3 stocks (same as main_portfolio.py)
    symbols = DEFAULT_SYMBOLS[:3]
    data_dict = load_and_prepare_data(symbols)
    if data_dict is None:
        print("ERROR: Data loading failed. Check network.")
        return

    results = []

    # ===== Scenario 1: No risk control (bare strategy) =====
    print(f"\n{'#' * 65}")
    print(f"  # Scenario 1: BARE STRATEGY - No Risk Control")
    print(f"{'#' * 65}")
    r1, m1, e1 = run_backtest(data_dict, risk_manager=None, scenario_name="No Risk Control (Bare)")
    results.append(("No Risk Control (Bare)", m1, e1))

    # ===== Scenario 2: Current risk config (baseline) =====
    print(f"\n{'#' * 65}")
    print(f"  # Scenario 2: CURRENT RISK CONFIG")
    print(f"{'#' * 65}")
    rm_default = RiskManager(dict(RISK_CONFIG))
    print(f"  stop_loss={RISK_CONFIG.get('stop_loss')}, "
          f"max_drawdown={RISK_CONFIG.get('max_drawdown')}, "
          f"max_single_weight={RISK_CONFIG.get('max_single_weight')}, "
          f"vol_adaptive={RISK_CONFIG.get('vol_adaptive')}")
    r2, m2, e2 = run_backtest(data_dict, rm_default, "Current Risk (SL8%/DD25%)")
    results.append(("Current Risk (SL8%/DD25%)", m2, e2))

    # ===== Scenario 3: Loose risk =====
    print(f"\n{'#' * 65}")
    print(f"  # Scenario 3: LOOSE RISK")
    print(f"{'#' * 65}")
    loose_cfg = dict(RISK_CONFIG)
    loose_cfg['stop_loss'] = 0.15
    loose_cfg['max_drawdown'] = 0.35
    loose_cfg['max_single_weight'] = 0.40
    loose_cfg['vol_adaptive'] = True
    rm_loose = RiskManager(loose_cfg)
    print(f"  stop_loss=15%, max_drawdown=35%, max_single_weight=40%, vol_adaptive=True")
    r3, m3, e3 = run_backtest(data_dict, rm_loose, "Loose Risk (SL15%/DD35%)")
    results.append(("Loose Risk (SL15%/DD35%)", m3, e3))

    # ===== Scenario 4: Mid risk =====
    print(f"\n{'#' * 65}")
    print(f"  # Scenario 4: MID RISK")
    print(f"{'#' * 65}")
    mid_cfg = dict(RISK_CONFIG)
    mid_cfg['stop_loss'] = 0.12
    mid_cfg['max_drawdown'] = 0.30
    mid_cfg['max_single_weight'] = 0.35
    mid_cfg['vol_adaptive'] = True
    rm_mid = RiskManager(mid_cfg)
    print(f"  stop_loss=12%, max_drawdown=30%, max_single_weight=35%, vol_adaptive=True")
    r4, m4, e4 = run_backtest(data_dict, rm_mid, "Mid Risk (SL12%/DD30%)")
    results.append(("Mid Risk (SL12%/DD30%)", m4, e4))

    # ===== Scenario 5: Minimal risk (only stop-loss 20%) =====
    print(f"\n{'#' * 65}")
    print(f"  # Scenario 5: MINIMAL RISK")
    print(f"{'#' * 65}")
    min_cfg = dict(RISK_CONFIG)
    min_cfg['stop_loss'] = 0.20
    min_cfg['max_drawdown'] = 0.50  # basically never triggers
    min_cfg['max_single_weight'] = 0.50
    min_cfg['vol_adaptive'] = False
    min_cfg['enforce_stop_loss'] = True
    rm_min = RiskManager(min_cfg)
    print(f"  stop_loss=20%, max_drawdown=50%, max_single_weight=50%, vol_adaptive=False")
    r5, m5, e5 = run_backtest(data_dict, rm_min, "Min Risk (SL20% only)")
    results.append(("Min Risk (SL20% only)", m5, e5))

    # ===== Summary comparison table =====
    print(f"\n\n{'=' * 90}")
    print(f"  SUMMARY: RISK CONTROL IMPACT COMPARISON")
    print(f"{'=' * 90}")
    header = f"{'Scenario':<30} {'Ann Return':>10} {'Sharpe':>6} {'Max DD':>10} {'Vol':>8} {'Trades':>6} {'SL Trig':>8} {'WinRate':>6}"
    print(header)
    print(f"{'-'*30} {'-'*10} {'-'*6} {'-'*10} {'-'*8} {'-'*6} {'-'*8} {'-'*6}")
    for name, metrics, engine in results:
        print(f"{name:<30} "
              f"{metrics.annual_return:>+9.2%} "
              f"{metrics.sharpe_ratio:>6.2f} "
              f"{metrics.max_drawdown:>9.2%} "
              f"{metrics.volatility:>7.2%} "
              f"{metrics.total_trades:>6} "
              f"{engine.stop_loss_triggered:>8} "
              f"{metrics.win_rate:>5.1%}")

    # ===== Diagnosis =====
    bare_ar = results[0][1].annual_return  # bare strategy annual return
    default_ar = results[1][1].annual_return  # current risk annual return

    print(f"\n{'=' * 60}")
    print(f"  DIAGNOSIS")
    print(f"{'=' * 60}")

    if bare_ar > 0.10:
        print(f"  [PASS] Bare strategy annual return {bare_ar:+.2%} > 10% -> Strategy logic is valid")
    elif bare_ar > 0.05:
        print(f"  [WARN] Bare strategy annual return {bare_ar:+.2%}, mediocre, strategy acceptable")
    else:
        print(f"  [FAIL] Bare strategy annual return {bare_ar:+.2%} < 5% -> Strategy needs redesign")

    impact = (default_ar - bare_ar) / bare_ar if bare_ar != 0 else 0
    if abs(impact) > 0.3:
        print(f"  [RED] Risk control impact on returns: {impact:+.0%} -> Significantly distorts strategy")
    elif abs(impact) > 0.1:
        print(f"  [AMBER] Risk control impact on returns: {impact:+.0%} -> Moderate impact, can optimize")
    else:
        print(f"  [GREEN] Risk control impact on returns: {impact:+.0%} -> Minimal interference")

    if e2.stop_loss_triggered > 100:
        print(f"  [RED] Stop-loss triggered {e2.stop_loss_triggered} times -> TOO TIGHT! Recommend 12-15%")
    if e2.stop_loss_triggered > 300:
        print(f"  [CRITICAL] Stop-loss triggered {e2.stop_loss_triggered} times -> Triggering almost daily!")

    print(f"\n{'=' * 60}")
    print(f"  Recommendation:")
    best_sharpe = -999
    best_scenario = ""
    best_idx = 1
    for i in range(1, len(results)):
        if results[i][1].sharpe_ratio > best_sharpe:
            best_sharpe = results[i][1].sharpe_ratio
            best_scenario = results[i][0]
            best_idx = i
    print(f"  [BEST] Scenario: {best_scenario}")
    print(f"     (Sharpe {best_sharpe:.2f}, "
          f"AnnRet {results[best_idx][1].annual_return:+.2%}, "
          f"MaxDD {results[best_idx][1].max_drawdown:.2%})")
    print(f"  -> Update RISK_CONFIG in config/settings.py with these parameters")
    print(f"  -> Re-run main_portfolio.py to verify")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
