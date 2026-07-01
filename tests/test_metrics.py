"""
绩效指标单元测试 — 已知输入 → 预期输出
"""
import pytest
from backtest.metrics import calculate_metrics, MetricsResult


class TestMetricsKnownSequence:
    """测试已知收益序列的指标计算"""

    def test_no_change_flat(self):
        """资金不涨不跌 → 总收益率0%, 回撤0%"""
        daily_values = [100000.0] * 253  # 1年 + 1天
        m = calculate_metrics(daily_values, [], 100000.0)
        assert m.total_return == 0.0
        assert m.annual_return == 0.0
        assert m.max_drawdown == 0.0
        assert m.total_trades == 0

    def test_steady_growth(self):
        """每年稳定增长10% → 年化约10%, 夏普约正数"""
        daily_values = [100000.0]
        daily_return = 1.10 ** (1 / 252) - 1  # 几何复利精确到10%
        for i in range(252):
            daily_values.append(daily_values[-1] * (1 + daily_return))
        m = calculate_metrics(daily_values, [], 100000.0)
        assert m.annual_return == pytest.approx(0.10, rel=0.02)
        assert m.sharpe_ratio > 0
        assert m.max_drawdown >= 0  # 单调增长 → 无回撤

    def test_total_return_50pct(self):
        """已知总涨幅50% → 总收益率0.5"""
        values = [100000.0, 110000.0, 120000.0, 130000.0, 140000.0, 150000.0]
        m = calculate_metrics(values, [], 100000.0)
        assert m.total_return == 0.5
        assert m.total_trades == 0


class TestMetricsWithTrades:
    """测试包含交易记录的指标"""

    def test_win_rate_50pct(self):
        values = [100000.0, 101000.0, 102000.0, 101500.0, 103000.0]
        trades = [
            {"pnl": 500},   # win
            {"pnl": -300},  # loss
            {"pnl": 200},   # win
            {"pnl": -100},  # loss
        ]
        m = calculate_metrics(values, trades, 100000.0)
        assert m.win_rate == 0.5
        assert m.total_trades == 4

    def test_all_wins(self):
        values = [100000.0, 101000.0, 102000.0]
        trades = [{"pnl": 500}, {"pnl": 300}]
        m = calculate_metrics(values, trades, 100000.0)
        assert m.win_rate == 1.0
        assert m.avg_win > 0

    def test_all_losses(self):
        values = [100000.0, 99000.0, 98000.0]
        trades = [{"pnl": -500}, {"pnl": -300}]
        m = calculate_metrics(values, trades, 100000.0)
        assert m.win_rate == 0.0
        assert m.avg_loss > 0


class TestMetricsDrawdown:
    """测试最大回撤计算"""

    def test_drawdown_20pct(self):
        """从100到80 → 最大回撤-20%"""
        values = [100.0, 95.0, 90.0, 85.0, 80.0, 85.0, 90.0]
        m = calculate_metrics([v * 1000 for v in values], [], 100000.0)
        assert m.max_drawdown == pytest.approx(-0.20, abs=0.01)

    def test_drawdown_no_recovery(self):
        """持续下跌 → 最大回撤即最终跌幅"""
        values = [100.0, 80.0, 60.0, 40.0]
        m = calculate_metrics([v * 1000 for v in values], [], 100000.0)
        assert m.max_drawdown == pytest.approx(-0.60, abs=0.01)
