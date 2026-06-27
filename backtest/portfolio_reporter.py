"""
========================================
  组合报告生成器
========================================

【输出内容】
1. 组合净值曲线（equity_curve_portfolio.png）
2. 各策略信号数量分布
3. 组合绩效指标（夏普/回撤/卡玛）
4. 各股票持仓占比
5. Walk-Forward 样式的汇总报告
"""
import os
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd
import numpy as np
from typing import Dict, List, Optional

from portfolio.engine import PortfolioResult
from backtest.metrics import calculate_metrics, MetricsResult


class PortfolioReporter:
    """
    组合报告生成器

    使用示例：
        reporter = PortfolioReporter(report_dir)
        reporter.generate(result, "组合回测报告")
    """

    def __init__(self, report_dir: str = "data_storage/reports"):
        self.report_dir = report_dir
        os.makedirs(report_dir, exist_ok=True)

    def generate(self, result: PortfolioResult, title: str = "组合回测报告"):
        """
        生成完整组合报告

        输出：
        1. 控制台文本报告
        2. equity_curve_portfolio.png — 组合净值曲线
        3. allocation_portfolio.png — 各股票持仓占比
        """
        self._print_report(result, title)
        self._plot_equity_curve(result, title)
        self._plot_allocation(result, title)

    # ─────────────── 文本报告 ───────────────

    def _print_report(self, result: PortfolioResult, title: str):
        """打印组合回测的文本报告"""
        print("\n" + "=" * 65)
        print(f"  {title}")
        print("=" * 65)

        # ---- 基本信息 ----
        n_days = len(result.daily_records)
        n_trades = len(result.trades)
        final_val = result.final_value()
        profit = final_val - result.initial_capital
        profit_pct = profit / result.initial_capital * 100

        print(f"\n  [组合概况]")
        print(f"  回测天数: {n_days} 天")
        print(f"  总交易次数: {n_trades} 笔")
        print(f"  初始资金: {result.initial_capital:>10,.2f}")
        print(f"  最终资产: {final_val:>10,.2f}")
        print(f"  总盈亏:   {profit:>+10,.2f} ({profit_pct:+.2f}%)")

        # ---- 从净值序列计算指标 ----
        if n_days > 1:
            daily_values = [r.total_value for r in result.daily_records]
            # 构建模拟的交易记录
            paired = self._pair_portfolio_trades(result.trades, result.daily_records)
            metrics = calculate_metrics(
                daily_values=daily_values,
                trades=paired,
                initial_capital=result.initial_capital,
            )
            self._print_metrics(metrics)

        # ---- 策略信号分布 ----
        self._print_strategy_stats(result)

        # ---- 持仓集中度 ----
        self._print_concentration(result)

        print("=" * 65 + "\n")

    def _print_metrics(self, metrics: MetricsResult):
        """打印绩效指标"""
        print(f"\n  [绩效指标]")
        print(f"  总收益率:      {metrics.total_return:>8.2%}")
        print(f"  年化收益率:    {metrics.annual_return:>8.2%}")
        print(f"  夏普比率:      {metrics.sharpe_ratio:>8.2f}")
        print(f"  最大回撤:      {metrics.max_drawdown:>8.2%}")
        print(f"  年化波动率:    {metrics.volatility:>8.2%}")
        print(f"  卡玛比率:      {metrics.calmar_ratio:>8.2f}")
        print(f"  胜率:          {metrics.win_rate:>8.2%}")
        print(f"  盈亏比:        {metrics.profit_factor:>8.2f}")

    def _print_strategy_stats(self, result: PortfolioResult):
        """打印各策略信号数量分布"""
        print(f"\n  [策略信号分布]")
        strategy_counts: Dict[str, int] = {}
        total_signal_days = 0

        for rec in result.daily_records:
            if rec.strategy_contributions:
                for sname, count in rec.strategy_contributions.items():
                    strategy_counts[sname] = strategy_counts.get(sname, 0) + count
                    total_signal_days += count

        if total_signal_days == 0:
            print("  (无信号统计)")
            return

        for sname in ['trend_following', 'mean_reversion', 'factor_selection']:
            count = strategy_counts.get(sname, 0)
            pct = count / total_signal_days * 100 if total_signal_days > 0 else 0
            label = {
                'trend_following': '趋势跟踪',
                'mean_reversion': '均值回归',
                'factor_selection': '因子选股',
            }.get(sname, sname)
            print(f"  {label:>10}: {count:>4} 次 ({pct:>5.1f}%)")

    def _print_concentration(self, result: PortfolioResult):
        """打印持仓集中度"""
        print(f"\n  [持仓集中度]")
        # 取最后一天的持仓
        last_rec = result.daily_records[-1] if result.daily_records else None
        if last_rec and last_rec.position_values:
            sorted_pos = sorted(
                last_rec.position_values.items(),
                key=lambda x: x[1], reverse=True
            )
            total = sum(v for _, v in sorted_pos)
            for sym, val in sorted_pos:
                pct = val / total * 100 if total > 0 else 0
                print(f"  {sym}: {val:>10,.2f} ({pct:>5.1f}%)")
        else:
            print("  (空仓)")

    # ─────────────── 图表 ───────────────

    def _plot_equity_curve(self, result: PortfolioResult, title: str):
        """绘制组合净值曲线 + 回撤曲线"""
        if len(result.daily_records) < 2:
            return

        df = result.to_dataframe()
        df = df.copy()
        df['date'] = pd.to_datetime(df['date'])

        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 8), gridspec_kw={'height_ratios': [3, 1]})

        # 净值曲线
        ax1.plot(df['date'], df['total_value'], label='组合总资产', color='#2196F3', linewidth=1.5)
        ax1.axhline(y=result.initial_capital, color='gray', linestyle='--', alpha=0.5, label='初始资金')
        ax1.fill_between(df['date'], result.initial_capital, df['total_value'],
                         where=(df['total_value'] >= result.initial_capital),
                         color='#4CAF50', alpha=0.15, label='盈利区')
        ax1.fill_between(df['date'], result.initial_capital, df['total_value'],
                         where=(df['total_value'] < result.initial_capital),
                         color='#f44336', alpha=0.15, label='亏损区')
        ax1.set_title(f'{title} — 净值曲线', fontsize=14, fontweight='bold')
        ax1.set_ylabel('总资产 (¥)')
        ax1.legend(loc='upper left')
        ax1.grid(True, alpha=0.3)

        # 回撤曲线
        values = df['total_value'].values
        rolling_max = np.maximum.accumulate(values)
        drawdown = (values - rolling_max) / rolling_max * 100

        ax2.fill_between(df['date'], 0, drawdown, color='#f44336', alpha=0.6)
        ax2.set_title('回撤曲线', fontsize=12)
        ax2.set_ylabel('回撤 (%)')
        ax2.set_xlabel('日期')
        ax2.grid(True, alpha=0.3)

        # 日期格式化
        for ax in [ax1, ax2]:
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
            ax.xaxis.set_major_locator(mdates.MonthLocator(interval=6))
            plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha='right')

        plt.tight_layout()
        path = os.path.join(self.report_dir, 'equity_curve_portfolio.png')
        plt.savefig(path, dpi=150, bbox_inches='tight')
        plt.close()
        print(f"  [图表] 净值曲线 → {path}")

    def _plot_allocation(self, result: PortfolioResult, title: str):
        """绘制最终持仓占比图"""
        last_rec = result.daily_records[-1] if result.daily_records else None
        if not last_rec or not last_rec.position_values:
            return

        values = last_rec.position_values
        labels = list(values.keys())
        sizes = list(values.values())

        fig, ax = plt.subplots(figsize=(8, 6))
        wedges, texts, autotexts = ax.pie(
            sizes, labels=labels, autopct='%1.1f%%',
            startangle=90,
            colors=plt.cm.Set3(range(len(labels))),
        )
        ax.set_title(f'{title} — 最终持仓分布\n(现金: ¥{last_rec.capital:,.2f})',
                     fontsize=12, fontweight='bold')

        plt.tight_layout()
        path = os.path.join(self.report_dir, 'allocation_portfolio.png')
        plt.savefig(path, dpi=150, bbox_inches='tight')
        plt.close()
        print(f"  [图表] 持仓分布 → {path}")

    # ─────────────── 辅助工具 ───────────────

    @staticmethod
    def _pair_portfolio_trades(trades, daily_records) -> List[dict]:
        """将组合交易配对（用于计算指标）"""
        paired = []
        buy_map: Dict[str, List] = {}

        for t in trades:
            if t.direction == 1:
                key = t.symbol
                if key not in buy_map:
                    buy_map[key] = []
                buy_map[key].append(t)
            elif t.direction == -1:
                key = t.symbol
                buys = buy_map.get(key, [])
                matching = [b for b in buys if b.date <= t.date]
                if matching:
                    buy = matching[-1]
                    cost_basis = buy.value + buy.cost
                    proceeds = t.value - t.cost
                    # 注意：这里简化配对精度，可能不精确（多笔买卖）
                    # 但对于指标计算的大致方向足够
                    if abs(cost_basis) > 0:
                        pnl_ratio = (proceeds - cost_basis) / cost_basis
                        paired.append({
                            'date': t.date, 'symbol': t.symbol,
                            'direction': -1, 'price': t.price,
                            'quantity': t.quantity, 'value': t.value,
                            'cost': t.cost, 'strategy': t.strategy,
                            'pnl': proceeds - cost_basis,
                        })

        return paired
