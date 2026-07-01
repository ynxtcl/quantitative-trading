#!/usr/bin/env python3
"""
========================================
  Phase 2 — 多策略组合回测入口
========================================

【执行流程】
1. 清理系统代理 → 加载数据
2. 各策略独立 WF 验证（检查过拟合程度）
3. 创建组合所需所有组件
4. 运行 PortfolioEngine 组合回测
5. 输出组合报告（含净值曲线 + 持仓分布）

【设计架构】
main_portfolio.py
  ├─ DataLoader（数据加载）
  │   ├─ load_daily → 日K线
  │   ├─ load_valuation → PE/PB 日频数据（百度股市通）
  │   └─ load_financial → ROE 季度数据（东方财富）
  ├─ DataCleaner（数据清洗）
  ├─ 各策略 WF 验证（独立检查过拟合）
  │   ├─ TrendFollowingStrategy → WF for each stock
  │   ├─ MeanReversionStrategy → WF for each stock
  │   └─ FactorRebalancer → 月度再平衡（单独路径）
  ├─ PortfolioCombiner（净权重求和）
  ├─ RiskManager（无状态风控）
  ├─ PortfolioEngine（多策略组合回测）
  └─ PortfolioReporter（组合报告）
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
from config.settings import DEFAULT_SYMBOLS, BACKTEST_CONFIG, RISK_CONFIG
from config.strategy_config import (
    TREND_FOLLOWING_CONFIG,
    MEAN_REVERSION_CONFIG,
    FACTOR_SELECTION_CONFIG,
)

from strategies.trend_following.strategy import TrendFollowingStrategy
from strategies.mean_reversion.strategy import MeanReversionStrategy
from strategies.factor_rebalancer import FactorRebalancer

from portfolio.combiner import PortfolioCombiner
from portfolio.risk_manager import RiskManager
from portfolio.engine import PortfolioEngine

from backtest.walk_forward import WalkForwardValidator
from backtest.portfolio_reporter import PortfolioReporter

import warnings
import pandas as pd
warnings.filterwarnings('ignore')


# ========================
#  辅助函数
# ========================

def make_tf_factory(symbol: str):
    """趋势跟踪策略工厂"""
    def factory():
        config = dict(TREND_FOLLOWING_CONFIG)
        config['symbol'] = symbol
        return TrendFollowingStrategy('trend_following', config)
    return factory


def make_mr_factory(symbol: str):
    """均值回归策略工厂"""
    def factory():
        config = dict(MEAN_REVERSION_CONFIG)
        config['symbol'] = symbol
        return MeanReversionStrategy('mean_reversion', config)
    return factory


def print_wf_header(label: str):
    """打印 WF 验证标题"""
    print(f"\n{'=' * 65}")
    print(f"  Walk-Forward 独立验证 — {label}")
    print(f"{'=' * 65}")


def run_strategy_wf(data_dict: dict, factory_fn, label: str):
    """
    对多只股票运行单个策略的 WF 独立验证
    用于检查该策略的过拟合程度

    返回:
        {symbol: WalkForwardResult}
    """
    print_wf_header(label)

    validator = WalkForwardValidator(
        window_years=3,
        train_ratio=2/3,
        step_years=1,
    )

    results = {}
    for symbol, df in data_dict.items():
        print(f"\n  ── {symbol} ──")
        df = clean_daily_data(df)
        qc = check_data_quality(df, symbol)
        print(f"  数据: {qc['total_days']} 天")

        try:
            wf_result = validator.validate(
                data=df,
                strategy_factory=factory_fn(symbol),
                engine_config=BACKTEST_CONFIG,
            )
            if wf_result.folds:
                summary = wf_result.summary_dict()
                oos_sharpe = wf_result.oos_sharpe_ratio
                if oos_sharpe > 0.7:
                    status = "稳健"
                elif oos_sharpe > 0.3:
                    status = "中度过拟合"
                else:
                    status = "严重过拟合"
                print(f"  OOS Sharpe: {oos_sharpe:.2%} -> {status}")
                results[symbol] = wf_result
            else:
                print(f"  [WARN] 数据不足")
        except Exception as e:
            print(f"  [FAIL] {e}")

    return results


def load_valuation_data(loader: DataLoader, symbols: list) -> dict:
    """
    加载并合并所有股票的估值/财务因子数据
    
    返回:
        {symbol: DataFrame} — 包含 OHLCV + pe + roe 列的日频数据
    """
    print("\n[Step 1.5] 加载财务与估值数据（用于因子选股）")
    
    # 为每只股票加载估值和财务数据
    enriched_data = {}
    
    for sym in symbols:
        df_daily = loader.load_daily(sym, 
                                      start=BACKTEST_CONFIG['start_date'],
                                      end=BACKTEST_CONFIG['end_date'])
        if df_daily.empty:
            continue
        
        df_daily = clean_daily_data(df_daily)
        
        # 1. 加载日频 PE/PB 估值数据（百度股市通）
        val_df = loader.load_valuation(sym)
        if not val_df.empty:
            df_daily = DataLoader.merge_valuation(df_daily, val_df)
            # 重命名 pe_ttm → pe 以匹配 factor_rebalancer 期望的列名
            if 'pe_ttm' in df_daily.columns:
                df_daily = df_daily.rename(columns={'pe_ttm': 'pe'})
        else:
            df_daily['pe'] = 0.5
            print(f"  [WARN] {sym}: 无估值数据，PE=0.5")
        
        if 'pb' not in df_daily.columns:
            df_daily['pb'] = 0.5
        
        # 2. 加载季度 ROE 财务数据（东方财富）
        fin_df = loader.load_financial(sym)
        if not fin_df.empty:
            extracted = DataLoader._extract_pe_roe_from_financial(fin_df)
            if not extracted.empty and 'roe' in extracted.columns:
                fin_roe = extracted[['date', 'roe']].copy()
                df_with_roe = pd.merge(df_daily, fin_roe, on='date', how='left')
                df_with_roe['roe'] = df_with_roe['roe'].ffill().fillna(0.5)
                df_daily = df_with_roe
                print(f"  [OK] {sym}: ROE 合并完成")
            else:
                df_daily['roe'] = 0.5
                print(f"  [WARN] {sym}: 无 ROE 数据，ROE=0.5")
        else:
            df_daily['roe'] = 0.5
            print(f"  [WARN] {sym}: 无财务数据，ROE=0.5")
        
        enriched_data[sym] = df_daily
        print(f"  [OK] {sym}: {len(df_daily)} 行数据，含 pe/roe 因子列")
    
    return enriched_data


# ========================
#  主入口
# ========================

def main():
    print("=" * 65)
    print("  定量交易系统 — Phase 2：多策略组合回测")
    print("  策略：趋势跟踪(35%) + 均值回归(25%) + 因子选股(40%)")
    print("  标的：A股10只（银行/家电/白酒/科技/新能源/保险/化工）")
    print("=" * 65)

    # ============ 1. 数据加载 ============
    print("\n[Step 1] 加载日K线数据")
    loader = DataLoader()
    symbols = DEFAULT_SYMBOLS
    data_dict = loader.load_multiple(
        symbols,
        start=BACKTEST_CONFIG['start_date'],
        end=BACKTEST_CONFIG['end_date'],
    )
    if not data_dict:
        print("数据加载失败")
        return

    # ============ 1.5 加载估值 + 财务因子数据 ============
    # ★ 2026-07-01 BUGFIX: 因子选股依赖的 PE/ROE 从未被加载
    #   之前 FactorRebalancer._calc_factor() 对 pe/roe 返回 0.5（中性）
    #   导致所有股票得分相同 → 选股退化为随机选择
    #   现在加载百度股市通日频 PE/PB + 东方财富季度 ROE
    enriched_data = load_valuation_data(loader, symbols)
    if not enriched_data:
        print("估值数据加载失败")
        return

    # ============ 2. 各策略独立 WF 验证 ============
    print(f"\n{'#' * 65}")
    print(f"  # Step 2: 各策略独立 Walk-Forward 验证")
    print(f"  # 目的：检查每个策略的过拟合程度")
    print(f"{'#' * 65}")

    # 趋势跟踪 WF
    tf_wf_results = run_strategy_wf(data_dict, make_tf_factory, "趋势跟踪")

    # 均值回归 WF
    mr_wf_results = run_strategy_wf(data_dict, make_mr_factory, "均值回归")

    # 打印 WF 验证结论
    print(f"\n{'=' * 65}")
    print(f"  各策略过拟合评估")
    print(f"{'=' * 65}")
    for label, results in [("趋势跟踪", tf_wf_results), ("均值回归", mr_wf_results)]:
        avg_oos = 0
        n = 0
        for sym, r in results.items():
            avg_oos += r.oos_sharpe_ratio
            n += 1
        if n > 0:
            avg_oos /= n
            if avg_oos > 0.5:
                status = "组合可接受"
            elif avg_oos > 0.3:
                status = "需要调参"
            else:
                status = "不建议使用"
            print(f"  {label:>10}: 平均 OOS Sharpe = {avg_oos:.2%} -> {status}")
    print(f"  (注意：因子选股为月度再平衡策略，不参与 WF 独立验证)")
    print(f"{'=' * 65}")

    # ============ 3. 创建组合组件 ============
    print(f"\n{'#' * 65}")
    print(f"  # Step 3: 创建组合回测组件")
    print(f"{'#' * 65}")

    # 创建各股票的策略实例
    tf_strategies = {}
    mr_strategies = {}
    for sym in symbols:
        # 趋势跟踪
        tf_cfg = dict(TREND_FOLLOWING_CONFIG)
        tf_cfg['symbol'] = sym
        tf_strategies[sym] = TrendFollowingStrategy('trend_following', tf_cfg)

        # 均值回归
        mr_cfg = dict(MEAN_REVERSION_CONFIG)
        mr_cfg['symbol'] = sym
        mr_strategies[sym] = MeanReversionStrategy('mean_reversion', mr_cfg)

    # 因子选股再平衡器
    rebalancer = FactorRebalancer(FACTOR_SELECTION_CONFIG)

    # 组合器 — 设置策略权重
    combiner = PortfolioCombiner()
    combiner.set_weights({
        'trend_following': TREND_FOLLOWING_CONFIG.get('weight', 0.4),
        'mean_reversion': MEAN_REVERSION_CONFIG.get('weight', 0.2),
        'factor_selection': FACTOR_SELECTION_CONFIG.get('weight', 0.4),
    })

    # 风控系统（从外部配置读取参数）
    risk_manager = RiskManager(dict(RISK_CONFIG))

    # 组合引擎
    engine = PortfolioEngine(BACKTEST_CONFIG)

    print(f"  策略实例: {len(tf_strategies)} TF + {len(mr_strategies)} MR + 1 Rebalancer")
    print(f"  组合权重: {combiner.strategy_weights}")
    print(f"  风控配置: {risk_manager.config}")
    print(f"  初始资金: {BACKTEST_CONFIG['initial_capital']:,.2f}")

    # ============ 4. 运行组合回测 ============
    print(f"\n{'#' * 65}")
    print(f"  # Step 4: 运行组合回测")
    print(f"{'#' * 65}")

    # ★ 使用 enriched_data（含 PE/ROE 列）传递给引擎
    #   engine.run() 内部将 data_dict 传给 rebalancer.generate_rebalance_signals()
    #   rebalancer._calc_factor() 会在每只股票的 df 中查找 'pe'/'roe' 列
    result = engine.run(
        data_dict=enriched_data,
        tf_strategies=tf_strategies,
        mr_strategies=mr_strategies,
        rebalancer=rebalancer,
        combiner=combiner,
        risk_manager=risk_manager,
    )

    # ============ 5. 生成报告 ============
    print(f"\n{'#' * 65}")
    print(f"  # Step 5: 生成组合报告")
    print(f"{'#' * 65}")

    reporter = PortfolioReporter()
    reporter.generate(result, "组合回测报告 — Phase 2")

    print(f"\n{'=' * 65}")
    print(f"  Phase 2 完成！")
    print(f"  最终资产: {result.final_value():,.2f}")
    print(f"  总收益率: {result.total_return():+.2%}")
    print(f"{'=' * 65}")


if __name__ == "__main__":
    main()
