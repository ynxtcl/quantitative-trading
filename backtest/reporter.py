"""
========================================
 回测报告 — 图表与可视化
========================================

【为什么可视化很重要？】
数字（收益率/夏普）只能告诉你策略"表现如何"。
图表能告诉你策略"为什么有这样的表现"：
- 资产曲线能看出收益的稳定性
- 回撤曲线能看出风险集中在哪些时期
- 结合大盘对比能看出策略是否有"独立于市场的Alpha"
"""

import matplotlib
matplotlib.use('Agg')  # 必须在 import pyplot 之前
# Agg 是非交互式后端，不显示窗口只保存图片
# 为什么不用 TkAgg/QtAgg？因为没有桌面环境（服务器/CI）时会报错
# Agg 在任何环境下都能正常工作
import matplotlib.pyplot as plt
import pandas as pd
from pathlib import Path
from config.settings import DATA_DIR


class BacktestReporter:
    """
    生成回测报告
    - 资产曲线图
    - 回撤曲线图
    - 关键指标文本报告
    """

    def __init__(self, report_dir: str = None):
        self.report_dir = Path(report_dir or DATA_DIR / "reports")
        self.report_dir.mkdir(parents=True, exist_ok=True)

    def plot_equity_curve(self, daily_df: pd.DataFrame,
                          filename: str = "equity_curve.png") -> str:
        """
        绘制资产曲线（双图组合）

        上图：资产曲线
        - 蓝色线条 = 总资产随时间的变化
        - 灰色虚线 = 初始资金水平线
        - 绿色填充 = 盈利区域（资产 > 初始资金）

        下图：回撤曲线
        - 红色填充 = 回撤区域
        - 回撤 = (当前值 - 历史最大值) / 历史最大值

        如何分析：
        - 如果资产曲线是"稳定的45度上升" = 好策略
        - 如果资产曲线是"过山车型" = 风险高
        - 如果回撤曲线经常出现 -20% 以上的深谷 = 需要优化风控
        """
        fig, axes = plt.subplots(2, 1, figsize=(12, 8), sharex=True)

        # ===== 上图：资产曲线 =====
        axes[0].plot(daily_df['date'], daily_df['total_value'],
                     label='总资产', color='blue', linewidth=1.5)
        initial = daily_df['total_value'].iloc[0]
        axes[0].axhline(y=initial, color='gray', linestyle='--', alpha=0.5,
                        label=f'初始资金 {initial:.0f}')
        # 盈利区域填充
        axes[0].fill_between(daily_df['date'], daily_df['total_value'],
                             initial,
                             where=daily_df['total_value'] >= initial,
                             color='green', alpha=0.1)
        axes[0].set_ylabel('总资产')
        axes[0].set_title('回测资产曲线')
        axes[0].legend()
        axes[0].grid(True, alpha=0.3)

        # ===== 下图：回撤曲线 =====
        rolling_max = daily_df['total_value'].expanding().max()
        drawdown = (daily_df['total_value'] - rolling_max) / rolling_max * 100
        axes[1].fill_between(daily_df['date'], 0, drawdown,
                             color='red', alpha=0.3)
        axes[1].plot(daily_df['date'], drawdown,
                     color='red', linewidth=1)
        axes[1].set_ylabel('回撤 (%)')
        axes[1].set_xlabel('日期')
        axes[1].set_title('回撤曲线')
        axes[1].grid(True, alpha=0.3)

        plt.tight_layout()
        path = self.report_dir / filename
        plt.savefig(path, dpi=100, bbox_inches='tight')
        plt.close()  # 释放内存，避免"too many open figures"警告
        return str(path)

    def save_text_report(self, metrics_result, trades_df: pd.DataFrame,
                         filename: str = "report.txt") -> str:
        """
        保存文本报告

        内容包含：
        1. 关键指标汇总（收益/风险/交易统计）
        2. 交易明细（每笔交易的日期/方向/价格/数量/成本）

        为什么需要文本报告？
        - 图表适合快速浏览
        - 文本适合详细分析和归档
        - 可以导入Excel做进一步统计
        """
        lines = []
        lines.append("=" * 60)
        lines.append("  定量交易系统 — 回测报告")
        lines.append("=" * 60)
        lines.append("")
        lines.append("  【关键指标】")
        lines.append(f"    总收益率:         {metrics_result.total_return:.2%}")
        lines.append(f"    年化收益率:       {metrics_result.annual_return:.2%}")
        lines.append(f"    夏普比率:         {metrics_result.sharpe_ratio:.2f}")
        lines.append(f"    最大回撤:         {metrics_result.max_drawdown:.2%}")
        lines.append(f"    年化波动率:       {metrics_result.volatility:.2%}")
        lines.append(f"    卡玛比率:         {metrics_result.calmar_ratio:.2f}")
        lines.append("")
        lines.append("  【交易统计】")
        lines.append(f"    总交易次数:       {metrics_result.total_trades}")
        lines.append(f"    胜率:             {metrics_result.win_rate:.2%}")
        lines.append(f"    平均盈利:         {metrics_result.avg_win:.2f}")
        lines.append(f"    平均亏损:         {metrics_result.avg_loss:.2f}")
        lines.append(f"    盈亏比:           {metrics_result.profit_factor:.2f}")
        lines.append("")
        lines.append("  【交易明细】")
        lines.append(f"    {trades_df.to_string(index=False)}")

        path = self.report_dir / filename
        with open(path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
        return str(path)
