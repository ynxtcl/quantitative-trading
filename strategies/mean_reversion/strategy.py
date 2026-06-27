"""Mean reversion strategy"""
import pandas as pd
import numpy as np
from strategies.base import BaseStrategy
class MeanReversionStrategy(BaseStrategy):
    def generate_signals(self, data):
        df = data.copy()
        p = self.config.get('bb_period', 20); s = self.config.get('bb_std', 2.0)
        mid = df['close'].rolling(p).mean()
        std = df['close'].rolling(p).std()
        df['bb_upper'] = mid + s * std; df['bb_lower'] = mid - s * std
        rsi_p = self.config.get('rsi_period', 14)
        delta = df['close'].diff()
        gain = delta.clip(lower=0).rolling(rsi_p).mean()
        loss = (-delta.clip(upper=0)).rolling(rsi_p).mean()
        rs = gain / loss; df['rsi'] = 100 - 100 / (1 + rs)
        df['ema50'] = df['close'].ewm(span=50).mean()
        df['signal'] = 0
        buy = (df['close'] < df['bb_lower']) & (df['rsi'] < 30) & (df['close'] > df['ema50'] * 0.97)
        sell = (df['close'] > df['bb_upper']) | (df['rsi'] > 70)
        df.loc[buy, 'signal'] = 1; df.loc[sell, 'signal'] = -1
        return df['signal']
