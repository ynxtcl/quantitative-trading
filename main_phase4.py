#!/usr/bin/env python3
"""
========================================
  Phase 4 — AI 增强入口
  XGBoost Walk-Forward 交叉验证
========================================

【执行流程】
1. 加载数据（同 Phase 1，复用 DataLoader + DataCleaner）
2. 对每只股票执行 Walk-Forward 验证
   每轮：
     a. 训练集 → 特征工程 → 训练 XGBoost 模型
     b. 测试集 → 特征工程 → ML 推理 → 生成信号 → 回测
     c. 对比 XGBoost vs TrendFollowing 的测试集夏普
3. 汇总多轮结果，输出对比表格

【与 Phase 1 的区别】
  Phase 1（main.py）：规则策略（唐奇安通道+ADX+MA60）
  Phase 4（本文件）：ML 策略（XGBoost 二分类）
  两者共享完全相同的回测引擎和数据管道——公平对比。

【预期输出示例】
  ┌─────────────────────────────────────────────────────────┐
  │  000001 XGBoost vs TrendFollowing                      │
  │  ├─ 轮1: XGBoost Sharpe 0.72 vs TF Sharpe 0.62 (ML 胜) │
  │  ├─ 轮2: XGBoost Sharpe 0.31 vs TF Sharpe 0.45 (TF 胜) │
  │  └─ 轮3: XGBoost Sharpe 0.55 vs TF Sharpe 0.51 (ML 胜) │
  │  结论: XGBoost 在 N/M 轮中优于 TrendFollowing          │
  └─────────────────────────────────────────────────────────┘
"""
import sys
import os

project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from utils.proxy import safe_clean_proxy
safe_clean_proxy()

from data.loader import DataLoader
from data.cleaner import clean_daily_data, check_data_quality
from config.settings import DEFAULT_SYMBOLS, BACKTEST_CONFIG
from config.strategy_config import TREND_FOLLOWING_CONFIG
from strategies.trend_following.strategy import TrendFollowingStrategy
from backtest.walk_forward import WalkForwardValidator, FoldResult
from backtest.engine import BacktestEngine
from backtest.metrics import calculate_metrics

from models.feature_engineering import engineer_features, FEATURE_COLS
from models.xgboost_strategy import XGBoostSignalStrategy
from models.model_trainer import train_model, print_training_summary

import warnings
warnings.filterwarnings('ignore')


def make_xgboost_factory(symbol: str):
    """
    XGBoost 策略工厂闭包

    与 main.py 的 make_trend_factory 不同，
    XGBoost 工厂需要额外传入 precomputed_data，
    因为 BacktestEngine.run() 会逐日传入原始 OHLCV data，
    而 XGBoost 策略需要特征列才能推理。

    解决方案：让策略自己调用 engineer_features() 做特征工程。
    """
    def factory(model=None):
        config = {
            'symbol': symbol,
            'feature_cols': FEATURE_COLS,
            'threshold_buy': 0.55,
            'threshold_sell': 0.45,
            'position_weight': 1.0,
        }
        return XGBoostSignalStrategy('xgboost', config, model=model)
    return factory


def run_walk_forward_xgboost(df, symbol) -> list:
    import pandas as pd  # 本地化导入（避免模块级冲突）
    """
    对单只股票执行 XGBoost Walk-Forward 验证

    每轮流程：
    1. 训练集数据 → engineer_features → XGBoost train
    2. 训练完成的模型注入 XGBoostSignalStrategy
    3. 测试集数据 → 带特征的回测
    4. 计算测试集指标

    返回:
        [{fold_id, train_sharpe, test_sharpe, train_info}, ...]
    """
    validator = WalkForwardValidator(
        window_years=3, train_ratio=2/3, step_years=1,
    )

    # 手动执行 Walk-Forward 的每轮（因为需要模型训练）
    total_start = df.index.min()
    total_end = df.index.max()
    total_days = (total_end - total_start).days
    total_years = total_days / 365.0

    if total_years < 3:
        print(f"  [WARN] 数据不足 {total_years:.1f} 年，跳过")
        return []

    n_folds = max(1, int((total_years - 3) / 1) + 1)
    results = []

    for fold_i in range(n_folds):
        fold_start = total_start + pd.DateOffset(years=fold_i * 1)
        fold_end = fold_start + pd.DateOffset(years=3)

        # 切分训练/测试
        train_end = fold_start + pd.DateOffset(years=2)
        train_end_idx = df.index.searchsorted(train_end, side='right')
        if train_end_idx >= len(df):
            continue
        test_start = df.index[train_end_idx]
        if fold_end > total_end:
            fold_end = total_end

        train_data = df.loc[fold_start:train_end].copy()
        test_data = df.loc[test_start:fold_end].copy()

        if len(train_data) < 60 or len(test_data) < 20:
            continue

        print(f"\n  ─── [ML] 第{fold_i+1}轮 "
              f"训练 {train_data.index[0].year}-{train_data.index[-1].year} → "
              f"测试 {test_data.index[0].year}-{test_data.index[-1].year} ───")

        # ===== Step A: 训练 XGBoost =====
        model, train_info = train_model(train_data)

        if model is None:
            print("  [SKIP] 模型训练失败，跳过本轮")
            continue

        print_training_summary(train_info)

        # ===== Step B: 训练集回测（验证过拟合程度）=====
        train_strategy = make_xgboost_factory(symbol)(model=model)
        # 为训练数据增加特征列
        train_data_fe = engineer_features(train_data)
        train_engine = BacktestEngine(BACKTEST_CONFIG)
        train_result = train_engine.run(train_data_fe, train_strategy)
        train_trades_paired = _pair_trades(train_result)
        train_metrics = calculate_metrics(
            daily_values=[r.total_value for r in train_result.daily_records],
            trades=train_trades_paired,
            initial_capital=BACKTEST_CONFIG['initial_capital'],
        )

        # ===== Step C: 测试集回测 =====
        test_strategy = make_xgboost_factory(symbol)(model=model)
        test_data_fe = engineer_features(test_data)
        test_engine = BacktestEngine(BACKTEST_CONFIG)
        test_result = test_engine.run(test_data_fe, test_strategy)
        test_trades_paired = _pair_trades(test_result)
        test_metrics = calculate_metrics(
            daily_values=[r.total_value for r in test_result.daily_records],
            trades=test_trades_paired,
            initial_capital=BACKTEST_CONFIG['initial_capital'],
        )

        # ===== Step D: 对比 TrendFollowing =====
        tf_config = dict(TREND_FOLLOWING_CONFIG)
        tf_config['symbol'] = symbol
        tf_strategy = TrendFollowingStrategy('trend_following', tf_config)
        tf_test_engine = BacktestEngine(BACKTEST_CONFIG)
        tf_test_result = tf_test_engine.run(test_data, tf_strategy)
        tf_trades_paired = _pair_trades(tf_test_result)
        tf_test_metrics = calculate_metrics(
            daily_values=[r.total_value for r in tf_test_result.daily_records],
            trades=tf_trades_paired,
            initial_capital=BACKTEST_CONFIG['initial_capital'],
        )

        # 打印对比
        ml_sharpe = test_metrics.sharpe_ratio
        tf_sharpe = tf_test_metrics.sharpe_ratio
        winner = "ML" if ml_sharpe > tf_sharpe else "TF"
        print(f"  [对比] XGBoost Sharpe: {ml_sharpe:.4f} | "
              f"TrendFollowing Sharpe: {tf_sharpe:.4f} | {winner}")

        results.append({
            'fold_id': fold_i + 1,
            'train_start': train_data.index[0],
            'train_end': train_data.index[-1],
            'test_start': test_data.index[0],
            'test_end': test_data.index[-1],
            'train_sharpe': train_metrics.sharpe_ratio,
            'test_sharpe': test_metrics.sharpe_ratio,
            'train_return': train_metrics.annual_return,
            'test_return': test_metrics.annual_return,
            'tf_test_sharpe': tf_test_metrics.sharpe_ratio,
            'tf_test_return': tf_test_metrics.annual_return,
            'ml_won': ml_sharpe > tf_sharpe,
            'n_train_trades': len(train_result.trades),
            'n_test_trades': len(test_result.trades),
            'train_info': train_info,
        })

    return results


def _pair_trades(result) -> list:
    """将买卖交易配对（同 backtest/walk_forward.py 中的逻辑）"""
    buy_trades = [t for t in result.trades if t.direction == 1]
    sell_trades = [t for t in result.trades if t.direction == -1]
    paired = []
    for sell in sell_trades:
        matching_buys = [b for b in buy_trades if b.date <= sell.date]
        if matching_buys:
            buy = matching_buys[-1]
            cost_basis = buy.value + buy.cost
            sell_proceeds = sell.value - sell.cost
            pnl = sell_proceeds - cost_basis
            paired.append({
                'date': sell.date, 'symbol': sell.symbol,
                'direction': -1, 'price': sell.price,
                'quantity': sell.quantity, 'value': sell.value,
                'cost': sell.cost, 'strategy': sell.strategy,
                'pnl': pnl,
            })
    return paired


def print_comparison_summary(all_results, symbol):
    """打印 XGBoost vs TrendFollowing 对比汇总"""
    if not all_results:
        print("  无有效结果")
        return

    print(f"\n{'=' * 60}")
    print(f"  Phase 4 — XGBoost vs TrendFollowing 对比报告")
    print(f"  标的: {symbol}")
    print(f"{'=' * 60}")

    ml_sharpes = [r['test_sharpe'] for r in all_results]
    tf_sharpes = [r['tf_test_sharpe'] for r in all_results]
    wins = sum(1 for r in all_results if r['ml_won'])

    # 每轮详情
    for r in all_results:
        test_years = f"{r['test_start'].year}-{r['test_end'].year}"
        winner = "ML" if r['ml_won'] else "TF"
        print(f"  轮{r['fold_id']} ({test_years}): "
              f"XGBoost Sharpe={r['test_sharpe']:.2f} | "
              f"TF Sharpe={r['tf_test_sharpe']:.2f} | {winner}")
        # 特征重要性
        top_feats = r['train_info'].get('top_features', [])
        if top_feats:
            print(f"     Top 特征: {', '.join(f'{n}({imp})' for n, imp in top_feats[:5])}")

    # 汇总
    print(f"\n  ─── 汇总 ───")
    print(f"  XGBoost 平均 Sharpe: {sum(ml_sharpes)/len(ml_sharpes):.3f}")
    print(f"  TF 平均 Sharpe: {sum(tf_sharpes)/len(tf_sharpes):.3f}")
    print(f"  XGBoost 胜率: {wins}/{len(all_results)} "
          f"({wins/len(all_results)*100:.0f}%)")

    winner = "XGBoost" if wins > len(all_results)/2 else "TrendFollowing"
    print(f"  结论: {winner} 综合表现更优")
    print(f"{'=' * 60}")


def main():
    print("=" * 60)
    print("  Phase 4 — AI 增强交叉验证")
    print("  策略A: XGBoost 二分类器 (22维特征 v2)")
    print("  策略B: TrendFollowing 规则策略（对比基准）")
    print("  Window: 3年(2训练+1测试) | 滑动: 1年")
    print("  标的: 沪深300成分股")
    print("=" * 60)

    # 加载数据
    loader = DataLoader()
    data_dict = loader.load_multiple(
        DEFAULT_SYMBOLS[:3],
        start=BACKTEST_CONFIG['start_date'],
        end=BACKTEST_CONFIG['end_date']
    )

    if not data_dict:
        print("数据加载失败")
        return

    # 逐只股票验证
    for symbol, df in data_dict.items():
        print(f"\n{'=' * 60}")
        print(f"  标的: {symbol} — XGBoost WF 验证")
        print(f"{'=' * 60}")

        df = clean_daily_data(df)
        qc = check_data_quality(df, symbol)
        print(f"  数据质量: {qc['total_days']} 天 | {qc['date_range']}")

        if isinstance(df.index, pd.DatetimeIndex):
            pass
        elif 'date' in df.columns:
            df = df.set_index('date')

        results = run_walk_forward_xgboost(df, symbol)

        if results:
            print_comparison_summary(results, symbol)

    print("\nPhase 4 验证完成！")
    print("如 XGBoost 在多数轮次中优于 TrendFollowing，")
    print("则说明 ML 辅助信号在 A 股沪深300上有效。")


if __name__ == "__main__":
    import pandas as pd
    main()
