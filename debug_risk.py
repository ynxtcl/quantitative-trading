# -*- coding: utf-8 -*-
"""
Diagnostic - count risk rule triggers
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')

from typing import Dict, List
from strategies.base import Signal
from portfolio.risk_manager import RiskManager
from data.loader import DataLoader
from data.cleaner import clean_daily_data
from config.settings import DEFAULT_SYMBOLS, BACKTEST_CONFIG
from config.strategy_config import TREND_FOLLOWING_CONFIG as TFC
from config.strategy_config import MEAN_REVERSION_CONFIG as MRC
from config.strategy_config import FACTOR_SELECTION_CONFIG as FSC
from strategies.trend_following.strategy import TrendFollowingStrategy
from strategies.mean_reversion.strategy import MeanReversionStrategy
from strategies.factor_rebalancer import FactorRebalancer
from portfolio.combiner import PortfolioCombiner
from portfolio.engine import PortfolioEngine
from utils.proxy import safe_clean_proxy; safe_clean_proxy()


class InstrumentedRiskManager(RiskManager):
    def __init__(self, config=None):
        super().__init__(config)
        self.stats = {
            "total_signals_in": 0,
            "single_weight_clipped": 0,
            "daily_symbol_rejected": 0,
            "total_position_clipped": 0,
            "total_position_rejected": 0,
            "drawdown_breaker_triggered": 0,
            "industry_clipped": 0,
            "signals_out": 0,
            "max_daily_ratio_used": 0.0,
        }
        self.daily_symbol_log = []
        self.drawdown_log = []

    def filter_signals(self, signals, current_position_ratio=0, current_positions=None, current_drawdown=0.0, current_position_ratios=None, annualized_volatility=0.0):
        self.stats["total_signals_in"] += len(signals)
        if not signals:
            return []

        # Rule 0: drawdown circuit breaker
        max_dd = self.config.get("max_drawdown", 0.25)
        if current_drawdown > max_dd:
            self.stats["drawdown_breaker_triggered"] += 1
            return []

        max_symbols = self.config["max_daily_symbols"]
        max_single = self.config["max_single_weight"]
        max_total = self.config["max_total_position"]

        existing_positions = set()
        if current_positions:
            existing_positions = {sym for sym, shares in current_positions.items() if shares > 0}

        # Rule 1: sort by confidence
        signals = sorted(signals, key=lambda s: s.confidence, reverse=True)

        filtered = []
        symbols_today = set()
        used_ratio = current_position_ratio

        for signal in signals:
            sym = signal.symbol
            target_weight = signal.weight

            # Rule 1: single symbol clip (only for buy signals, cumulative)
            #         + C2: industry concentration check
            if signal.direction == 1:
                current_sym_ratio = current_position_ratios.get(sym, 0) if current_position_ratios else 0
                if current_sym_ratio + target_weight > max_single:
                    self.stats["single_weight_clipped"] += 1
                    target_weight = max(max_single - current_sym_ratio, 0)
                    if target_weight <= 0:
                        continue
                # C2: industry concentration
                industry_map = self.config.get("industry_map", {})
                if industry_map:
                    ind = industry_map.get(sym, "其他")
                    ind_exposure = {}
                    if current_position_ratios:
                        for s, r in current_position_ratios.items():
                            ind_name = industry_map.get(s, "其他")
                            ind_exposure[ind_name] = ind_exposure.get(ind_name, 0.0) + r
                    current_ind_ratio = ind_exposure.get(ind, 0.0)
                    max_ind = self.config.get("max_industry_weight", 0.50)
                    if current_ind_ratio + target_weight > max_ind:
                        self.stats["industry_clipped"] += 1
                        target_weight = max(max_ind - current_ind_ratio, 0)
                        if target_weight <= 0:
                            continue

            # Rule 2: daily new symbols limit
            if sym not in existing_positions:
                if len(symbols_today) >= max_symbols:
                    self.stats["daily_symbol_rejected"] += 1
                    continue
                symbols_today.add(sym)

            # Rule 3: total position limit (only for buys, sells reduce exposure)
            if signal.direction == 1:
                if used_ratio + target_weight > max_total:
                    target_weight = max_total - used_ratio
                    if target_weight <= 0:
                        self.stats["total_position_rejected"] += 1
                        continue
                    self.stats["total_position_clipped"] += 1
                used_ratio += target_weight
            signal.weight = round(target_weight, 4)
            filtered.append(signal)

        self.stats["signals_out"] += len(filtered)
        if len(symbols_today) + len(existing_positions) > 0:
            self.daily_symbol_log.append(len(symbols_today) + len(existing_positions))
        if used_ratio > self.stats["max_daily_ratio_used"]:
            self.stats["max_daily_ratio_used"] = used_ratio
        self.drawdown_log.append(current_drawdown)

        return filtered

# Top-level code moved under __main__ guard
if __name__ == "__main__":
    engine = PortfolioEngine(BACKTEST_CONFIG)
    result = engine.run(cleaned, tf_strategies, mr_strategies, rebalancer, combiner, risk_mgr)

    print(f"\n{'=' * 60}")
    print(f"  BACKTEST OVERVIEW")
    print(f"  Days:       {len(result.daily_records)}")
    print(f"  Trades:     {len(result.trades)}")
    print(f"  Final:      {result.final_value():,.2f}")
    print(f"  Annual Ret: {result.annual_return():+.2%}")

    total = risk_mgr.stats["total_signals_in"]
    out = risk_mgr.stats["signals_out"]
    rejected = total - out

    print(f"\n{'=' * 60}")
    print(f"  RISK RULE TRIGGER COUNTS")
    print(f"{'Rule':<50} {'Count':>8} {'% of In':>8}")
    print(f"{'-' * 66}")

    r0 = risk_mgr.stats["drawdown_breaker_triggered"]
    if total:
        print(f"  0. drawdown circuit breaker triggered")
        print(f"     all signals rejected (max_drawdown={risk_mgr.config.get('max_drawdown', 0.25):.0%}) {'':>11} {r0:>8d}  {r0/total*100:>7.1f}%")
    else:
        print("  0. drawdown circuit breaker triggered: N/A")

    r1 = risk_mgr.stats["single_weight_clipped"]
    print(f"  1. single symbol clip (>30%, buy only)")
    print(f"     weight truncated to 30%                 {r1:>8d}  {r1/total*100:>7.1f}%" if total else "N/A")

    r2 = risk_mgr.stats["daily_symbol_rejected"]
    print(f"  2. daily new symbols limit (>5)")
    print(f"     signal completely rejected              {r2:>8d}  {r2/total*100:>7.1f}%" if total else "N/A")

    r3a = risk_mgr.stats["total_position_clipped"]
    r3b = risk_mgr.stats["total_position_rejected"]
    r3 = r3a + r3b
    print(f"  3a. total position limit (>95%)")
    print(f"      weight truncated                        {r3a:>8d}  {r3a/total*100:>7.1f}%" if total else "N/A")
    print(f"  3b. no available capacity")
    print(f"      signal completely rejected              {r3b:>8d}  {r3b/total*100:>7.1f}%" if total else "N/A")

    print(f"{'-' * 66}")
    print(f"  Total signals in        : {total:>8d}")
    print(f"  Signals passed risk     : {out:>8d}  ({out/total*100:.1f}%)" if total else "N/A")
    print(f"  Signals rejected by risk: {rejected:>8d}  ({rejected/total*100:.1f}%)" if total else "N/A")

    print(f"\n{'=' * 60}")
    print(f"  POSITION USAGE ANALYSIS")
    avg_pos = sum(risk_mgr.daily_symbol_log) / len(risk_mgr.daily_symbol_log) if risk_mgr.daily_symbol_log else 0
    max_dd_log = max(risk_mgr.drawdown_log) if risk_mgr.drawdown_log else 0
    print(f"  Avg positions held per day:   {avg_pos:.1f}")
    print(f"  Max daily ratio used:         {risk_mgr.stats['max_daily_ratio_used']*100:.1f}%")
    print(f"  Allowed max:                  {risk_mgr.config['max_total_position']*100:.0f}%")
    print(f"  Max drawdown encountered:     {max_dd_log*100:.2f}%")
    print(f"  Drawdown breaker threshold:   {risk_mgr.config.get('max_drawdown', 0.25)*100:.0f}%")
    print(f"  Drawdown breaker was hit:     {'YES' if r0 > 0 else 'NO'}")
    print(f"  Position limit was hit:       {'YES' if r3a + r3b > 0 else 'NO'}")
    print(f"  Single symbol limit was hit:  {'YES' if r1 > 0 else 'NO'}")
    print(f"  Daily symbol limit was hit:   {'YES' if r2 > 0 else 'NO'}")

    print(f"\n{'=' * 60}")
    # 引擎会在进入 risk filter 之前执行 stop_loss 检查
    print(f"  Stop loss triggered (engine level): {engine.stop_loss_triggered}")
    print(f"  NOTE: stop_loss is now ENFORCED at the PortfolioEngine level.")
    print(f"  Positions exceeding -{risk_mgr.config['stop_loss']:.0%} from cost basis")
    print(f"  will generate forced sell signals before risk filter.")
    print(f"  max_drawdown ({risk_mgr.config.get('max_drawdown', 0.25):.0%}) is ENFORCED as circuit breaker.")
    print(f"  C1 volatility adaptive: {risk_mgr.config.get('vol_adaptive', True)}")
    print(f"     final volatility: {getattr(engine, 'final_volatility', 0.0):.1%}")
    print(f"  C2 industry clipped: {risk_mgr.stats['industry_clipped']}")
    print(f"{'=' * 60}")
