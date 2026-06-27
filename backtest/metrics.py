"""
========================================
 回测指标 — 评估策略表现
 核心指标：年化收益/夏普比率/最大回撤/胜率
========================================
"""

import numpy as np
import pandas as pd
from typing import List
from dataclasses import dataclass


@dataclass
class MetricsResult:
    """回测指标结果"""
    total_return: float         # 总收益率
    annual_return: float        # 年化收益率
    sharpe_ratio: float         # 夏普比率
    max_drawdown: float         # 最大回撤
    volatility: float           # 年化波动率
    total_trades: int           # 总交易次数
    win_rate: float             # 胜率
    avg_win: float              # 平均盈利
    avg_loss: float             # 平均亏损
    profit_factor: float        # 盈亏比
    calmar_ratio: float         # 卡玛比率


def calculate_metrics(daily_values: List[float],
                      trades: List[dict],
                      initial_capital: float,
                      risk_free_rate: float = 0.02) -> MetricsResult:
    """
    计算回测指标

    参数:
        daily_values: 每日总资产序列
        trades: 交易记录列表
        initial_capital: 初始资金
        risk_free_rate: 无风险利率
    """
    values = pd.Series(daily_values)
    returns = values.pct_change().dropna()

    # ──── 收益率 ────
    total_return = (values.iloc[-1] - initial_capital) / initial_capital
    n_days = len(values)
    n_years = n_days / 252
    annual_return = (1 + total_return) ** (1 / n_years) - 1 if n_years > 0 else 0

    # ──── 风险指标 ────
    daily_risk_free = risk_free_rate / 252
    excess_returns = returns - daily_risk_free
    volatility = returns.std() * np.sqrt(252)
    sharpe = (excess_returns.mean() / excess_returns.std() * np.sqrt(252)
              if excess_returns.std() > 0 else 0)

    # ──── 最大回撤 ────
    rolling_max = values.expanding().max()
    drawdown = (values - rolling_max) / rolling_max
    max_dd = drawdown.min()

    # ──── 卡玛比率 ────
    calmar = annual_return / abs(max_dd) if max_dd != 0 else 0

    # ──── 交易统计 ────
    if trades:
        wins = [t for t in trades if t.get('pnl', 0) > 0]
        losses = [t for t in trades if t.get('pnl', 0) < 0]
        win_rate = len(wins) / len(trades) if trades else 0

        avg_win = np.mean([t['pnl'] for t in wins]) if wins else 0
        avg_loss = abs(np.mean([t['pnl'] for t in losses])) if losses else 0
        profit_factor = (sum(t['pnl'] for t in wins) /
                         abs(sum(t['pnl'] for t in losses))
                         if losses and abs(sum(t['pnl'] for t in losses)) > 0 else float('inf'))
    else:
        win_rate = 0
        avg_win = 0
        avg_loss = 0
        profit_factor = 0

    return MetricsResult(
        total_return=round(total_return, 4),
        annual_return=round(annual_return, 4),
        sharpe_ratio=round(sharpe, 4),
        max_drawdown=round(max_dd, 4),
        volatility=round(volatility, 4),
        total_trades=len(trades),
        win_rate=round(win_rate, 4),
        avg_win=round(avg_win, 2),
        avg_loss=round(avg_loss, 2),
        profit_factor=round(profit_factor, 2),
        calmar_ratio=round(calmar, 4),
    )


def print_metrics(metrics: MetricsResult):
    """友好打印指标"""
    print("=" * 50)
    print("  回测绩效报告")
    print("=" * 50)
    print(f"  总收益率:      {metrics.total_return:>8.2%}")
    print(f"  年化收益率:    {metrics.annual_return:>8.2%}")
    print(f"  夏普比率:      {metrics.sharpe_ratio:>8.2f}")
    print(f"  最大回撤:      {metrics.max_drawdown:>8.2%}")
    print(f"  年化波动率:    {metrics.volatility:>8.2%}")
    print(f"  卡玛比率:      {metrics.calmar_ratio:>8.2f}")
    print("-" * 50)
    print(f"  总交易次数:    {metrics.total_trades:>8d}")
    print(f"  胜率:          {metrics.win_rate:>8.2%}")
    print(f"  平均盈利:      {metrics.avg_win:>8.2f}")
    print(f"  平均亏损:      {metrics.avg_loss:>8.2f}")
    print(f"  盈亏比:        {metrics.profit_factor:>8.2f}")
    print("=" * 50)
