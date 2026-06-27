"""
compare — rqalpha 与自研回测引擎对比验证

对每一只股票、每一个 Walk-Forward 轮次，分别在自研引擎和 rqalpha 上
运行同一趋势跟踪策略，对比核心指标并生成报告。
"""

import logging
import sys
from pathlib import Path
from typing import List, Optional, Callable
from dataclasses import dataclass

import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data.loader import DataLoader
from data.cleaner import clean_daily_data
from config.settings import DEFAULT_SYMBOLS, BACKTEST_CONFIG
from config.strategy_config import TREND_FOLLOWING_CONFIG
from strategies.trend_following.strategy import TrendFollowingStrategy
from backtest.engine import BacktestEngine, BacktestResult
from backtest.metrics import MetricsResult, calculate_metrics
from backtest.walk_forward import WalkForwardValidator

from verify.rqalpha_runner import run_on_rqalpha, RQAlphaMetrics
from verify.rqalpha_strategy import make_self_contained_strategy

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 对比报告条目
# ---------------------------------------------------------------------------

@dataclass
class CompareRow:
    """一轮对比的完整数据"""
    symbol: str
    period_label: str
    window_idx: int
    is_train: bool

    ours_total_return: float = 0.0
    ours_annual_return: float = 0.0
    ours_sharpe: float = 0.0
    ours_max_dd: float = 0.0
    ours_volatility: float = 0.0
    ours_trades: int = 0
    ours_win_rate: float = 0.0

    rq_total_return: float = 0.0
    rq_annual_return: float = 0.0
    rq_sharpe: float = 0.0
    rq_max_dd: float = 0.0
    rq_volatility: float = 0.0
    rq_trades: int = 0
    rq_win_rate: float = 0.0

    @property
    def diff_annual_return(self) -> float:
        if abs(self.ours_annual_return) < 1e-8:
            return float("inf")
        return abs(self.rq_annual_return - self.ours_annual_return) / max(abs(self.ours_annual_return), 1e-8)

    @property
    def diff_sharpe(self) -> float:
        if abs(self.ours_sharpe) < 1e-8:
            return float("inf")
        return abs(self.rq_sharpe - self.ours_sharpe) / max(abs(self.ours_sharpe), 1e-8)

    @property
    def diff_max_dd(self) -> float:
        if abs(self.ours_max_dd) < 1e-8:
            return float("inf")
        return abs(self.rq_max_dd - self.ours_max_dd) / max(abs(self.ours_max_dd), 1e-8)


# ---------------------------------------------------------------------------
# 策略工厂 — 与 main.py 的模式一致
# ---------------------------------------------------------------------------

def make_own_strategy_factory(symbol: str, trend_params: dict = None):
    """创建自研引擎策略的工厂函数"""
    def factory():
        config = dict(TREND_FOLLOWING_CONFIG)
        config['symbol'] = symbol
        if trend_params:
            if 'entry_period' in config and trend_params.get('period'):
                config['entry_period'] = trend_params['period']
            if 'adx_threshold' in config and trend_params.get('adx_threshold'):
                config['adx_threshold'] = trend_params['adx_threshold']
        return TrendFollowingStrategy('trend_following', config)
    return factory


# ---------------------------------------------------------------------------
# 单品种对比
# ---------------------------------------------------------------------------

def compare_single_stock(
    loader: DataLoader,
    symbol: str,
    start_date: str = "2020-01-01",
    end_date: str = "2025-01-01",
    trend_params: dict = None,
) -> List[CompareRow]:
    """
    对单个股票执行对比验证。
    用 WalkForwardValidator 的窗口切分逻辑，在每个窗口上分别跑自研和 rqalpha。
    """
    if trend_params is None:
        trend_params = {
            "period": TREND_FOLLOWING_CONFIG.get("entry_period", 20),
            "adx_threshold": TREND_FOLLOWING_CONFIG.get("adx_threshold", 20),
        }

    # --- 加载 + 清洗 ---
    raw = loader.load_daily(symbol, start=start_date, end=end_date)
    if raw is None or raw.empty:
        logger.warning("跳过 %s：无数据", symbol)
        return []

    # 清洗 (返回整数索引，有 'date' 列)
    df = clean_daily_data(raw)
    if df.empty or len(df) < 120:
        logger.warning("跳过 %s：清洗后数据不足 (%d 行)", symbol, len(df))
        return []

    # 确保有日期列作为索引 (WalkForwardValidator.validate 会处理)
    if 'date' not in df.columns:
        logger.warning("跳过 %s：缺少 'date' 列", symbol)
        return []

    # 构造 rqalpha 格式代码
    rq_symbol = f"{symbol}.XSHG" if symbol.startswith(("600", "601", "603", "605", "688", "510")) else f"{symbol}.XSHE"

    # --- 手动划分 Walk-Forward 窗口 ---
    # 使用与 WalkForwardValidator 相同的逻辑
    df_idx = df.set_index('date').sort_index()  # 有日期索引的副本来算窗口
    total_start = df_idx.index.min()
    total_end = df_idx.index.max()
    total_days = (total_end - total_start).days
    total_years = total_days / 365.0

    if total_years < 3:
        logger.warning("跳过 %s：数据长度 %.1f 年不足 3 年", symbol, total_years)
        return []

    n_folds = max(1, int((total_years - 3) / 1) + 1)

    rows = []
    for fold_i in range(n_folds):
        fold_start = total_start + pd.DateOffset(years=fold_i)
        fold_end = fold_start + pd.DateOffset(years=3)

        train_end = fold_start + pd.DateOffset(years=2)
        train_end_idx = df_idx.index.searchsorted(train_end, side='right')
        if train_end_idx >= len(df_idx):
            continue
        test_start = df_idx.index[train_end_idx]

        if fold_end > total_end:
            fold_end = total_end

        # 从原始 df（含 date 列）中提取窗口数据
        train_mask = (df['date'] >= fold_start) & (df['date'] <= train_end)
        test_mask = (df['date'] >= test_start) & (df['date'] <= fold_end)

        train_df = df[train_mask].copy()
        test_df = df[test_mask].copy()

        if len(train_df) < 60 or len(test_df) < 20:
            continue

        # ---- 训练集对比 ----
        train_metrics = _run_both_engines(
            df=train_df,
            symbol=symbol,
            rq_symbol=rq_symbol,
            trend_params=trend_params,
        )
        if train_metrics:
            rows.append(CompareRow(
                symbol=symbol,
                period_label=f"训练 {train_df['date'].iloc[0].strftime('%Y%m')}~{train_df['date'].iloc[-1].strftime('%Y%m')}",
                window_idx=fold_i,
                is_train=True,
                **train_metrics,
            ))

        # ---- 测试集对比 ----
        test_metrics = _run_both_engines(
            df=test_df,
            symbol=symbol,
            rq_symbol=rq_symbol,
            trend_params=trend_params,
        )
        if test_metrics:
            rows.append(CompareRow(
                symbol=symbol,
                period_label=f"测试 {test_df['date'].iloc[0].strftime('%Y%m')}~{test_df['date'].iloc[-1].strftime('%Y%m')}",
                window_idx=fold_i,
                is_train=False,
                **test_metrics,
            ))

    return rows


def _run_both_engines(
    df: pd.DataFrame,
    symbol: str,
    rq_symbol: str,
    trend_params: dict,
    initial_capital: float = None,
) -> Optional[dict]:
    """对单个数据集，同时运行自研引擎和 rqalpha"""
    if df.empty or len(df) < 60:
        return None

    if initial_capital is None:
        initial_capital = BACKTEST_CONFIG['initial_capital']

    start_date = df['date'].iloc[0].strftime("%Y-%m-%d")
    end_date = df['date'].iloc[-1].strftime("%Y-%m-%d")

    # --- 自研引擎 ---
    strategy_factory = make_own_strategy_factory(symbol, trend_params)
    strategy = strategy_factory()

    engine = BacktestEngine(BACKTEST_CONFIG)
    try:
        result: BacktestResult = engine.run(df, strategy)
        trades_paired = WalkForwardValidator._pair_trades(result)
        metrics = calculate_metrics(
            daily_values=[r.total_value for r in result.daily_records],
            trades=trades_paired,
            initial_capital=initial_capital,
        )
        ours = {
            "ours_total_return": metrics.total_return,
            "ours_annual_return": metrics.annual_return,
            "ours_sharpe": metrics.sharpe_ratio,
            "ours_max_dd": metrics.max_drawdown,
            "ours_volatility": metrics.volatility,
            "ours_trades": metrics.total_trades,
            "ours_win_rate": metrics.win_rate,
        }
    except Exception as e:
        logger.error("自研引擎 %s %s~%s 失败: %s", symbol, start_date, end_date, e)
        import traceback; traceback.print_exc()
        return None

    # 将 df 转为日期索引用于 rqalpha
    rq_df = df.set_index('date') if 'date' in df.columns else df

    # --- rqalpha (自包含策略：数据直接传入闭包，不依赖 bundle) ---
    init_func, handle_func = make_self_contained_strategy(
        symbol=rq_symbol,
        ohlcv=rq_df,
        entry_period=trend_params.get("entry_period", 20),
        exit_period=trend_params.get("exit_period", 10),
        ma_filter_period=trend_params.get("ma_filter_period", 60),
        adx_threshold=trend_params.get("adx_threshold", 20),
        adx_period=trend_params.get("adx_period", 14),
        position_weight=trend_params.get("position_weight", 1.0),
    )

    rq_metrics: RQAlphaMetrics = run_on_rqalpha(
        ohlcv=rq_df,
        symbol=rq_symbol,
        start_date=start_date,
        end_date=end_date,
        strategy_funcs={"init": init_func, "handle_bar": handle_func},
        stock_capital=initial_capital,
    )

    return {
        **ours,
        "rq_total_return": rq_metrics.total_return,
        "rq_annual_return": rq_metrics.annual_return,
        "rq_sharpe": rq_metrics.sharpe_ratio,
        "rq_max_dd": rq_metrics.max_drawdown,
        "rq_volatility": rq_metrics.volatility,
        "rq_trades": rq_metrics.total_trades,
        "rq_win_rate": rq_metrics.win_rate,
    }


# ---------------------------------------------------------------------------
# 报告输出
# ---------------------------------------------------------------------------

def print_report(rows: List[CompareRow], threshold: float = 0.05):
    """格式化输出对比报告。"""
    if not rows:
        print("无数据可对比。")
        return

    passed = 0
    failed = 0

    print("=" * 90)
    print("  rqalpha 对比验证报告")
    print("=" * 90)

    for row in rows:
        mark = "训练" if row.is_train else "测试"
        print(f"\n  {row.symbol} | {row.period_label} | {mark} 第{row.window_idx + 1}轮")
        print(f"  {'─' * 85}")
        print(f"  {'指标':<16} {'自研引擎':>12} {'rqalpha':>12} {'差异率':>10} {'':<4}")
        print(f"  {'─' * 85}")

        metrics_def = [
            ("年化收益", row.ours_annual_return, row.rq_annual_return, row.diff_annual_return, "%"),
            ("夏普比率", row.ours_sharpe, row.rq_sharpe, row.diff_sharpe, ""),
            ("最大回撤", row.ours_max_dd, row.rq_max_dd, row.diff_max_dd, "%"),
            ("波动率",   row.ours_volatility, row.rq_volatility, 0.0, "%"),
            ("交易笔数", float(row.ours_trades), float(row.rq_trades), 0.0, ""),
            ("胜率",     row.ours_win_rate, row.rq_win_rate, 0.0, "%"),
        ]

        for name, ours, rq, diff, unit in metrics_def:
            if name in ("波动率", "胜率"):
                print(f"  {name:<16} {ours * 100:>10.2f}{unit} {rq * 100:>10.2f}{unit}")
            elif name == "交易笔数":
                print(f"  {name:<16} {ours:>12.0f} {rq:>12.0f}")
            else:
                flag = ' [WARN]' if diff > threshold else ' [OK]'
                if unit == "%":
                    print(f"  {name:<16} {ours * 100:>10.2f}% {rq * 100:>10.2f}% "
                          f"{'N/A' if diff == float('inf') else f'{diff * 100:.1f}%':>10} {flag}")
                else:
                    print(f"  {name:<16} {ours:>12.4f} {rq:>12.4f} "
                          f"{'N/A' if diff == float('inf') else f'{diff * 100:.1f}%':>10} {flag}")

        annual_ok = row.diff_annual_return <= threshold
        sharpe_ok = row.diff_sharpe <= threshold
        maxdd_ok = row.diff_max_dd <= threshold
        all_ok = annual_ok and sharpe_ok and maxdd_ok
        if all_ok:
            passed += 1
            print(f"  [PASS] 通过（年化夏普回撤差异均 <= {threshold * 100:.0f}%）")
        else:
            failed += 1
            print(f"  [FAIL] 未通过（年化夏普回撤差异存在 > {threshold * 100:.0f}%）")

    print(f"\n{'=' * 90}")
    print(f"  总览: {len(rows)} 个对比 | 通过 {passed} | 未通过 {failed}")
    if failed == 0:
        print("  结论: 自研引擎与 rqalpha 结果一致，验证通过。")
    else:
        print(f"  结论: 存在 {failed} 个偏差，需要进一步分析。")
    print(f"{'=' * 90}\n")


# ---------------------------------------------------------------------------
# CLI 主入口
# ---------------------------------------------------------------------------

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    import warnings
    warnings.filterwarnings('ignore')

    from utils.proxy import safe_clean_proxy
    safe_clean_proxy()

    loader = DataLoader()
    symbols = DEFAULT_SYMBOLS[:2]

    all_rows = []
    for sym in symbols:
        logger.info("开始对比 %s ...", sym)
        rows = compare_single_stock(loader, sym)
        all_rows.extend(rows)
        logger.info("%s 完成，共 %d 个对比窗口", sym, len(rows))

    print_report(all_rows)


if __name__ == "__main__":
    main()
