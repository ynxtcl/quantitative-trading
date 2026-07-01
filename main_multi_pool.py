#!/usr/bin/env python3
"""
========================================
  Phase 2.5 — 多股票池 + 组合回测入口
========================================

【新增功能】
基于 StockScreener P0→P1→P2→P3 全流程的股票池管理，
自动从沪深300筛选高质量的股票池，
然后注入 PortfolioEngine 执行多策略组合回测。

【执行流程】

         PoolManager
  ┌─────────────────────────────────────┐
  │ StockScreener.run_full_pipeline()   │
  │  ├─ P0: fetch_constituents()       │ ← 沪深300全成分股
  │  ├─ P1: prescreen()                │ ← 流动性/波动率/PE/停牌过滤
  │  ├─ P2: stratified_sample()        │ ← 行业分层+综合评分
  │  └─ P3: rebalance()                │ ← 换手率控制
  └──────────┬──────────────────────────┘
             ↓ pool.get_symbols()
       PortfolioEngine.run()
  ┌─────────────────────────────────────┐
  │ TF + MR + FS 策略 + Combiner        │
  │ + RiskManager                       │
  └─────────────────────────────────────┘
             ↓
       PortfolioReporter → 报告

【使用方式】
  python main_multi_pool.py              # 刷新股票池+回测
  python main_multi_pool.py --no-refresh # 仅缓存回测
  python main_multi_pool.py --quick      # 仅刷新+查看统计
"""

import sys
import os

# 将项目根目录添加到 Python 搜索路径
project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from utils.proxy import safe_clean_proxy
safe_clean_proxy()

from data.loader import DataLoader
from data.cleaner import clean_daily_data, check_data_quality
from data.screener import StockScreener
from config.settings import BACKTEST_CONFIG, RISK_CONFIG
from config.strategy_config import (
    TREND_FOLLOWING_CONFIG,
    MEAN_REVERSION_CONFIG,
    FACTOR_SELECTION_CONFIG,
)

from strategies.trend_following.strategy import TrendFollowingStrategy
from strategies.mean_reversion.strategy import MeanReversionStrategy
from strategies.factor_rebalancer import FactorRebalancer

from portfolio.pool_manager import PoolManager
from portfolio.combiner import PortfolioCombiner
from portfolio.risk_manager import RiskManager
from portfolio.engine import PortfolioEngine

from backtest.portfolio_reporter import PortfolioReporter

import warnings
import pandas as pd
warnings.filterwarnings('ignore')


# ========================
#  辅助函数
# ========================

def load_valuation_for_pool(loader: DataLoader, symbols: list) -> dict:
    """
    为池中每只股票加载并合并估值数据（PE/PB/ROE）
    供 PortfolioEngine 中的因子选股（FactorRebalancer）使用

    返回: {symbol: DataFrame} — 含 OHLCV + pe_ttm + roe 列
    """
    print(f"\n[估值数据] 加载 {len(symbols)} 只股票的 PE/PB/ROE...")

    enriched = {}
    ok_count = 0
    for sym in symbols:
        df_daily = loader.load_daily(
            sym,
            start=BACKTEST_CONFIG['start_date'],
            end=BACKTEST_CONFIG['end_date'],
        )
        if df_daily.empty:
            continue

        df_daily = clean_daily_data(df_daily)

        # 1. 日频 PE/PB 估值数据
        val_df = loader.load_valuation(sym)
        if not val_df.empty:
            df_daily = DataLoader.merge_valuation(df_daily, val_df)
        else:
            df_daily['pe_ttm'] = 0.5  # 中性值

        if 'pb' not in df_daily.columns:
            df_daily['pb'] = 0.5

        # 2. 季度 ROE 财务数据
        fin_df = loader.load_financial(sym)
        if not fin_df.empty:
            try:
                extracted = DataLoader._extract_pe_roe_from_financial(fin_df)
                if not extracted.empty and 'roe' in extracted.columns:
                    fin_roe = extracted[['date', 'roe']].copy()
                    df_with_roe = pd.merge(df_daily, fin_roe, on='date', how='left')
                    df_with_roe['roe'] = df_with_roe['roe'].ffill().fillna(0.5)
                    df_daily = df_with_roe
            except Exception:
                df_daily['roe'] = 0.5
        else:
            df_daily['roe'] = 0.5

        enriched[sym] = df_daily
        ok_count += 1

    print(f"  [OK] 估值数据加载完成: {ok_count}/{len(symbols)} 只")
    return enriched


# ========================
#  主入口
# ========================

def main():
    print("=" * 65)
    print("  定量交易系统 — Phase 2.5：多股票池 + 组合回测")
    print("  策略：趋势跟踪(35%) + 均值回归(25%) + 因子选股(40%)")
    print("  股票池：沪深300 → P1初筛 → P2分层抽样 → P3再平衡")
    print("=" * 65)

    # ============ 1. 初始化组件 ============
    loader = DataLoader()
    screener = StockScreener()
    pool_mgr = PoolManager(loader=loader, screener=screener)

    # ============ 2. 加载/刷新股票池 ============
    print(f"\n{'#' * 65}")
    print(f"  # Step 1: 刷新股票池 (StockScreener P0→P1→P2→P3)")
    print(f"{'#' * 65}")

    pipeline_result = pool_mgr.refresh_pool()
    symbols = pool_mgr.get_symbols()

    if not symbols:
        print("[FAIL] 股票池为空，退出")
        return

    print(f"\n  最终股票池 ({len(symbols)} 只):")
    for i, sym in enumerate(symbols, 1):
        print(f"    {i:2d}. {sym}")

    # 检查是否只查看统计
    if "--quick" in sys.argv:
        print(f"\n{'=' * 65}")
        print(f"  [快速模式] 股票池统计：{pool_mgr.get_stats()}")
        print(f"{'=' * 65}")
        return

    # ============ 3. 加载估值数据（因子选股需要）============
    print(f"\n{'#' * 65}")
    print(f"  # Step 2: 加载估值与财务数据")
    print(f"{'#' * 65}")

    enriched_data = load_valuation_for_pool(loader, symbols)
    if not enriched_data:
        print("[FAIL] 估值数据加载失败，退出")
        return

    # ============ 4. 创建组合组件 ============
    print(f"\n{'#' * 65}")
    print(f"  # Step 3: 创建组合回测组件")
    print(f"{'#' * 65}")

    # 创建各股票的策略实例
    tf_strategies = {}
    mr_strategies = {}
    for sym in symbols:
        tf_cfg = dict(TREND_FOLLOWING_CONFIG)
        tf_cfg['symbol'] = sym
        tf_strategies[sym] = TrendFollowingStrategy('trend_following', tf_cfg)

        mr_cfg = dict(MEAN_REVERSION_CONFIG)
        mr_cfg['symbol'] = sym
        mr_strategies[sym] = MeanReversionStrategy('mean_reversion', mr_cfg)

    # 因子选股再平衡器
    rebalancer = FactorRebalancer(FACTOR_SELECTION_CONFIG)

    # 组合器
    combiner = PortfolioCombiner()
    combiner.set_weights({
        'trend_following': TREND_FOLLOWING_CONFIG.get('weight', 0.35),
        'mean_reversion': MEAN_REVERSION_CONFIG.get('weight', 0.25),
        'factor_selection': FACTOR_SELECTION_CONFIG.get('weight', 0.40),
    })

    # 风控系统
    risk_manager = RiskManager(dict(RISK_CONFIG))

    # 组合引擎
    engine = PortfolioEngine(BACKTEST_CONFIG)

    print(f"  策略实例: {len(tf_strategies)} TF + {len(mr_strategies)} MR + 1 Rebalancer")
    print(f"  组合权重: {combiner.strategy_weights}")
    print(f"  风控配置: 止损{risk_manager.config['stop_loss']:.0%}, "
          f"熔断{risk_manager.config['max_drawdown']:.0%}")
    print(f"  初始资金: {BACKTEST_CONFIG['initial_capital']:,.2f}")

    # ============ 5. 运行组合回测 ============
    print(f"\n{'#' * 65}")
    print(f"  # Step 4: 运行组合回测 (PortfolioEngine)")
    print(f"{'#' * 65}")

    result = engine.run(
        data_dict=enriched_data,
        tf_strategies=tf_strategies,
        mr_strategies=mr_strategies,
        rebalancer=rebalancer,
        combiner=combiner,
        risk_manager=risk_manager,
    )

    # ============ 6. 生成报告 ============
    print(f"\n{'#' * 65}")
    print(f"  # Step 5: 生成组合报告")
    print(f"{'#' * 65}")

    reporter = PortfolioReporter()
    reporter_title = f"多股票池组合回测 (Phase 2.5) — {len(symbols)}只池"
    reporter.generate(result, reporter_title)

    # ============ 7. 打印摘要 ============
    print(f"\n{'=' * 65}")
    print(f"  Phase 2.5 完成！")
    print(f"  初始资产: {BACKTEST_CONFIG['initial_capital']:,.2f}")
    print(f"  最终资产: {result.final_value():,.2f}")
    print(f"  总收益率: {result.total_return():+.2%}")
    print(f"  年化收益: {result.annual_return():+.2%}")
    print(f"  股票池数: {len(symbols)} 只")
    print(f"{'=' * 65}")


if __name__ == "__main__":
    main()
