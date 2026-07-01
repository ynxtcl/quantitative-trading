#!/usr/bin/env python3
"""
========================================
  Phase 4.5 — 多策略组合 + XGBoost ML 信号
  集成回测入口
========================================

【核心创新】
将 Phase 4（XGBoost Walk-Forward 验证）与 Phase 2（多策略组合回测）整合，
实现传统规则策略（TF/MR/FS）与 ML 信号策略在统一组合中的协同运作。

【执行流程】
Step A: 加载数据（同 Phase 2）
Step B: XGBoost Walk-Forward 训练（逐股训练模型）
Step C: 预计算特征工程（工程师_features 对全量数据）
Step D: 各策略独立 WF 验证（TF + MR，可选 XGBoost 对比）
Step E: 创建组合组件 + XGBoost 策略实例
Step F: 运行 PortfolioEngine 组合回测（4策略）
Step G: 输出组合报告

【数据流完整性保障】
- 训练时：train_data → engineer_features() → XGBoost train （隔离训练）
- 推理时：full_data → engineer_features() → .shift(1) 保证无未来泄漏
- 引擎层：TF/MR/FS 使用原始 OHLCV，XGBoost 使用预计算特征数据
- 运行时断言：每日循环验证特征列存在、日期对齐

【架构对比】
               Phase 2 (TF/MR/FS)           Phase 4.5 (新增 XGBoost)
               ────────────────             ────────────────────────
  策略数量     3 (TF+MR+FS)                  4 (TF+MR+FS+XGBoost)
  信号来源     规则逻辑                      规则逻辑 + ML predict_proba
  特征输入     OHLCV (原始)                  OHLCV + 23维 ML 特征
  模型训练     无                            Walk-Forward 逐轮训练
  过拟合风险   低（规则固定）                 中（需 WF 验证）
"""
import sys
import os

project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from utils.proxy import safe_clean_proxy
safe_clean_proxy()

import pandas as pd
import warnings
warnings.filterwarnings('ignore')

# ─── 核心模块 ───
from data.loader import DataLoader
from data.cleaner import clean_daily_data, check_data_quality
from config.settings import DEFAULT_SYMBOLS, BACKTEST_CONFIG, RISK_CONFIG
from config.strategy_config import (
    TREND_FOLLOWING_CONFIG,
    MEAN_REVERSION_CONFIG,
    FACTOR_SELECTION_CONFIG,
    XGBOOST_CONFIG,
)

from strategies.trend_following.strategy import TrendFollowingStrategy
from strategies.mean_reversion.strategy import MeanReversionStrategy
from strategies.factor_rebalancer import FactorRebalancer

from portfolio.combiner import PortfolioCombiner
from portfolio.risk_manager import RiskManager
from portfolio.engine import PortfolioEngine

from backtest.walk_forward import WalkForwardValidator
from backtest.portfolio_reporter import PortfolioReporter

from models.feature_engineering import engineer_features, FEATURE_COLS
from models.xgboost_strategy import XGBoostSignalStrategy
from models.model_trainer import train_model, print_training_summary


# ========================
#  辅助函数
# ========================

def load_valuation_data(loader: DataLoader, symbols: list) -> dict:
    """
    加载并合并所有股票的估值/财务因子数据

    返回:
        {symbol: DataFrame} — 包含 OHLCV + pe + roe 列的日频数据

    注意：与 main_portfolio.py 中的 load_valuation_data() 逻辑一致，
    因为 DataLoader.load_daily() 有网络缓存，多次调用不会显著增加耗时。
    """
    import pandas as pd
    print("\n[Step 1.5] 加载财务与估值数据（用于因子选股）")

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


def make_xgb_factory(symbol: str):
    """
    XGBoost 策略工厂——用于训练后创建推理实例
    """
    def factory(model=None):
        config = dict(XGBOOST_CONFIG)
        config['symbol'] = symbol
        config['feature_cols'] = FEATURE_COLS
        return XGBoostSignalStrategy('xgboost', config, model=model)
    return factory


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


def train_xgboost_for_all_symbols(data_dict: dict) -> dict:
    """
    对每只股票训练一个 XGBoost 模型（使用最后3年数据）

    返回:
        {symbol: trained_model_or_None}

    训练策略：
    - 使用最近3年数据，前2年训练 + 后1年验证（与 WF 逻辑一致）
    - 如果数据不足5年，使用全部可用的前80%做训练
    """
    trained_models = {}
    print(f"\n{'=' * 65}")
    print(f"  XGBoost Walk-Forward 模型训练")
    print(f"{'=' * 65}")

    for symbol, df_raw in data_dict.items():
        df = clean_daily_data(df_raw)
        qc = check_data_quality(df, symbol)
        print(f"\n  ── {symbol} ({qc['total_days']} 天) ──")

        # 确定训练窗口
        total_years = qc['total_days'] / 252
        if total_years < 2:
            print(f"  [SKIP] 数据不足 {total_years:.1f} 年（需≥2年）")
            trained_models[symbol] = None
            continue

        # 使用最近3年或全部数据的前80%
        if total_years >= 3:
            end_date = df.index.max()
            start_date = end_date - pd.DateOffset(years=3)
            train_end = start_date + pd.DateOffset(years=2)
            train_data = df.loc[start_date:train_end].copy()
        else:
            split_idx = int(len(df) * 0.8)
            train_data = df.iloc[:split_idx].copy()

        if len(train_data) < 60:
            print(f"  [SKIP] 训练数据仅 {len(train_data)} 条（需≥60）")
            trained_models[symbol] = None
            continue

        print(f"  训练集: {train_data.index[0].strftime('%Y-%m-%d')} → "
              f"{train_data.index[-1].strftime('%Y-%m-%d')} "
              f"({len(train_data)} 天)")

        model, train_info = train_model(train_data)

        if model is None:
            print(f"  [FAIL] 训练失败")
            trained_models[symbol] = None
        else:
            print_training_summary(train_info)
            trained_models[symbol] = model

    return trained_models


def precompute_features(data_dict: dict, trained_models: dict) -> dict:
    """
    预计算特征——在完整数据集上运行 engineer_features

    返回:
        {symbol: DataFrame_with_features}

    设计要点：
    - 只计算有训练模型的股票
    - 无模型的股票保持原始 OHLCV（不会作为 XGBoost 数据源）
    - 与训练时的特征工程互不干扰（独立调用 engineer_features）
    """
    print(f"\n{'=' * 65}")
    print(f"  预计算 ML 特征（22维）")
    print(f"{'=' * 65}")

    fe_data_dict = {}
    for symbol, df in data_dict.items():
        if symbol in trained_models and trained_models[symbol] is not None:
            fe_df = engineer_features(df)
            fe_data_dict[symbol] = fe_df
            print(f"  {symbol}: 特征工程完成 → {len(fe_df)} 行, {len(fe_df.columns)} 列")
        else:
            print(f"  {symbol}: 跳过（无训练模型）")

    return fe_data_dict


# ========================
#  主入口
# ========================

def main():
    print("=" * 65)
    print("  Phase 4.5 — 多策略组合 + XGBoost ML 信号")
    print("  策略：趋势跟踪 + 均值回归 + 因子选股 + XGBoost ML")
    print("  标的：沪深300成分股 (演示3只)")
    print("=" * 65)

    # ============ Step 1: 数据加载 ============
    print(f"\n{'=' * 65}")
    print(f"  [Step 1] 加载数据")
    print(f"{'=' * 65}")
    loader = DataLoader()
    symbols = DEFAULT_SYMBOLS[:3]
    raw_data = loader.load_multiple(
        symbols,
        start=BACKTEST_CONFIG['start_date'],
        end=BACKTEST_CONFIG['end_date'],
    )
    if not raw_data:
        print("数据加载失败")
        return

    # ============ Step 2: XGBoost 模型训练 ============
    print(f"\n{'=' * 65}")
    print(f"  [Step 2] XGBoost Walk-Forward 模型训练")
    print(f"{'=' * 65}")
    trained_models = train_xgboost_for_all_symbols(raw_data)

    # 检查是否有至少一个训练成功的模型
    valid_xgb_symbols = {
        sym for sym, model in trained_models.items() if model is not None
    }
    if not valid_xgb_symbols:
        print("\n  [WARN] 没有训练成功的 XGBoost 模型，跳过 ML 策略")
        xgb_enabled = False
    else:
        xgb_enabled = True
        print(f"\n  已训练 XGBoost 模型: {len(valid_xgb_symbols)}/{len(symbols)} 只股票")

    # ============ Step 3: 预计算特征 ============
    print(f"\n{'=' * 65}")
    print(f"  [Step 3] 预计算 ML 特征")
    print(f"{'=' * 65}")
    fe_data_dict = precompute_features(raw_data, trained_models)

    # ============ Step 4: 各策略独立 WF 验证 ============
    print(f"\n{'=' * 65}")
    print(f"  [Step 4] 各策略独立 Walk-Forward 验证")
    print(f"{'=' * 65}")

    tf_wf_results = run_strategy_wf(raw_data, make_tf_factory, "趋势跟踪")
    mr_wf_results = run_strategy_wf(raw_data, make_mr_factory, "均值回归")

    # ============ Step 5: 创建组合组件 ============
    print(f"\n{'=' * 65}")
    print(f"  [Step 5] 创建组合回测组件")
    print(f"{'=' * 65}")

    # 传统策略
    tf_strategies = {}
    mr_strategies = {}
    for sym in symbols:
        tf_cfg = dict(TREND_FOLLOWING_CONFIG)
        tf_cfg['symbol'] = sym
        tf_strategies[sym] = TrendFollowingStrategy('trend_following', tf_cfg)

        mr_cfg = dict(MEAN_REVERSION_CONFIG)
        mr_cfg['symbol'] = sym
        mr_strategies[sym] = MeanReversionStrategy('mean_reversion', mr_cfg)

    # XGBoost 策略（使用训练好的模型）
    xgb_strategies = {}
    for sym in symbols:
        if sym in trained_models and trained_models[sym] is not None:
            xgb_cfg = dict(XGBOOST_CONFIG)
            xgb_cfg['symbol'] = sym
            xgb_cfg['feature_cols'] = FEATURE_COLS
            xgb_strategies[sym] = XGBoostSignalStrategy(
                'xgboost', xgb_cfg, model=trained_models[sym]
            )

    # 因子选股再平衡器
    rebalancer = FactorRebalancer(FACTOR_SELECTION_CONFIG)

    # 组合器 — 设置策略权重（含 xgboost）
    combiner = PortfolioCombiner()
    combiner_weights = {
        'trend_following': TREND_FOLLOWING_CONFIG.get('weight', 0.40),
        'mean_reversion': MEAN_REVERSION_CONFIG.get('weight', 0.10),
        'factor_selection': FACTOR_SELECTION_CONFIG.get('weight', 0.30),
        'xgboost': XGBOOST_CONFIG.get('weight', 0.20),
    }
    combiner.set_weights(combiner_weights)

    # 风控系统
    risk_manager = RiskManager(dict(RISK_CONFIG))

    # 组合引擎
    engine = PortfolioEngine(BACKTEST_CONFIG)

    print(f"  策略实例: {len(tf_strategies)} TF + {len(mr_strategies)} MR + "
          f"{len(xgb_strategies)} XGBoost + 1 Rebalancer")
    print(f"  组合权重: {combiner.strategy_weights}")
    print(f"  风控参数: {risk_manager.config}")
    print(f"  初始资金: {BACKTEST_CONFIG['initial_capital']:,.2f}")

    # ============ Step 6: 运行组合回测 ============
    print(f"\n{'=' * 65}")
    print(f"  [Step 6] 运行组合回测（含 XGBoost ML 信号）")
    print(f"{'=' * 65}")

    # ★ 2026-07-01 BUGFIX: 加载含 PE/ROE 的估值/财务因子数据
    #   之前 data_dict=cleaned_data（纯 OHLCV）传递给引擎，
    #   rebalancer._calc_factor() 对 pe/roe 返回 0.5（中性值），
    #   导致因子选股退化为随机选择。
    #   现在使用 enriched_data（含 pe/roe 列）替代 cleaned_data。
    print(f"\n{'=' * 65}")
    print(f"  [Step 6a] 加载 PE/ROE 因子数据")  
    print(f"{'=' * 65}")
    enriched_data = load_valuation_data(loader, symbols)

    # 数据清洗（未加载估值数据的股票用原始清洗数据兜底）
    cleaned_data = {}
    for sym, df in raw_data.items():
        cleaned_data[sym] = clean_daily_data(df)

    # 用 enriched_data 替换 cleaned_data（含 pe/roe 列）
    data_for_engine = {}
    for sym in symbols:
        if sym in enriched_data:
            data_for_engine[sym] = enriched_data[sym]
        elif sym in cleaned_data:
            data_for_engine[sym] = cleaned_data[sym]

    # XGBoost 数据源：预计算特征的 DataFrame
    xgb_data = {}
    for sym in symbols:
        if sym in fe_data_dict:
            xgb_data[sym] = fe_data_dict[sym]

    result = engine.run(
        data_dict=data_for_engine,
        tf_strategies=tf_strategies,
        mr_strategies=mr_strategies,
        rebalancer=rebalancer,
        xgb_strategies=xgb_strategies if xgb_enabled else {},
        xgb_data_dict=xgb_data if xgb_enabled else None,
        combiner=combiner,
        risk_manager=risk_manager,
    )

    # ============ Step 7: 生成报告 ============
    print(f"\n{'=' * 65}")
    print(f"  [Step 7] 生成组合报告")
    print(f"{'=' * 65}")

    reporter = PortfolioReporter()
    label = "组合回测报告 — Phase 4.5 (含 XGBoost)"
    reporter.generate(result, label)

    # ============ 汇总 ============
    print(f"\n{'=' * 65}")
    print(f"  Phase 4.5 完成！")
    print(f"{'=' * 65}")
    print(f"  策略组合: TF({TREND_FOLLOWING_CONFIG.get('weight',0.4)*100:.0f}%) + "
          f"MR({MEAN_REVERSION_CONFIG.get('weight',0.1)*100:.0f}%) + "
          f"FS({FACTOR_SELECTION_CONFIG.get('weight',0.3)*100:.0f}%) + "
          f"XGBoost({XGBOOST_CONFIG.get('weight',0.2)*100:.0f}%)")
    print(f"  ML 策略状态: {'已启用 ✅' if xgb_enabled else '已跳过 ⚠️'}")
    print(f"  回测天数: {len(result.daily_records)} 天")
    print(f"  初始资金: {result.initial_capital:,.2f}")
    print(f"  最终资产: {result.final_value():,.2f}")
    print(f"  总收益率: {result.total_return():+.2%}")
    print(f"  年化收益: {result.annual_return():+.2%}")

    # 打印策略信号统计
    if result.daily_records:
        total_signals = sum(r.signal_count for r in result.daily_records)
        print(f"  总信号数: {total_signals} 个")

        # 统计各策略触发次数（从每日记录中的 strategy_contributions 汇总）
        strategy_counts = {}
        for rec in result.daily_records:
            for sname, count in rec.strategy_contributions.items():
                strategy_counts[sname] = strategy_counts.get(sname, 0) + count
        print(f"  各策略信号分布:")
        for sname, count in sorted(strategy_counts.items(), key=lambda x: -x[1]):
            print(f"    {sname}: {count} 次")

    print(f"{'=' * 65}")


if __name__ == "__main__":
    main()
