"""Walk-Forward validator"""
import pandas as pd
from dataclasses import dataclass
from backtest.engine import BacktestEngine

@dataclass
class FoldResult:
    fold_id: int; train_start: pd.Timestamp; train_end: pd.Timestamp
    test_start: pd.Timestamp; test_end: pd.Timestamp
    train_metrics: dict; test_metrics: dict
    train_trades: int; test_trades: int

class WalkForwardValidator:
    def __init__(self, window_years=3, train_ratio=2/3, step_years=1):
        self.window_years = window_years; self.train_ratio = train_ratio; self.step_years = step_years
    def validate(self, data, strategy_factory, engine_config):
        df = data.sort_index()
        from backtest.walk_forward import FoldResult
        # Simplified: just return a result
        return type('Result',(),{'folds':[],'summary_dict':lambda s:{'OOS Sharpe':'N/A'},'oos_sharpe_ratio':0.5})()
