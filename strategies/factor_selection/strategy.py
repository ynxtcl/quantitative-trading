"""Factor selection strategy"""
from strategies.base import BaseStrategy
import pandas as pd
class FactorSelectionStrategy(BaseStrategy):
    def generate_signals(self, data):
        return pd.Series(0, index=data.index)
