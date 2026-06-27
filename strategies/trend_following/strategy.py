"""Trend following strategy"""
import pandas as pd
import numpy as np
from strategies.base import BaseStrategy
class TrendFollowingStrategy(BaseStrategy):
    def generate_signals(self, data):
        df = data.copy()
        ep = self.config.get('entry_period', 20)
        xp = self.config.get('exit_period', 10)
        df['hi'] = df['high'].rolling(ep).max()
        df['lo'] = df['low'].rolling(xp).min()
        df['ma60'] = df['close'].rolling(60).mean()
        df['adx'] = self._adx(df, 14)
        df['signal'] = 0
        buy = (df['close'] > df['hi'].shift(1)) & (df['close'] > df['ma60']) & (df['adx'] > 20)
        sell = df['close'] < df['lo'].shift(1)
        df.loc[buy, 'signal'] = 1
        df.loc[sell, 'signal'] = -1
        return df['signal']
    def _adx(self, df, period):
        high, low, close = df['high'], df['low'], df['close']
        plus_dm = high.diff()
        minus_dm = low.diff()
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(1)
        atr = tr.rolling(period).mean()
        return 100 * (atr / close).rolling(period).mean() if atr.notna().any() else pd.Series(25, index=df.index)
