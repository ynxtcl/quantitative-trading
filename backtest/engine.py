"""Backtest engine"""
import pandas as pd
from copy import deepcopy

class BacktestEngine:
    def __init__(self, config):
        self.cfg = config
        self.capital = config.get('initial_capital', 100000.0)
    def run(self, data, strategy, symbol):
        """Run backtest for one strategy on one stock"""
        from backtest.metrics import compute_metrics
        df = data.copy()
        signals = strategy.generate_signals(df)
        df['signal'] = signals
        df['position'] = df['signal'].fillna(0)
        # Execute
        df['daily_ret'] = df['close'].pct_change() * df['position'].shift(1)
        df['commission'] = df['daily_ret'].abs() * self.cfg.get('commission', 0.0003)
        df['stamp'] = df['daily_ret'].clip(upper=0) * self.cfg.get('stamp_tax', 0.001) * -1
        df['slippage'] = df['daily_ret'].abs() * self.cfg.get('slippage', 0.001)
        df['net_ret'] = df['daily_ret'] - df['commission'] - df['stamp'] - df['slippage']
        df['equity'] = self.capital * (1 + df['net_ret']).cumprod()
        metrics = compute_metrics(df, self.capital)
        return df, metrics
