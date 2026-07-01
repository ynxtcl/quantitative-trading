"""
========================================
 策略A：趋势跟踪 — 通道突破 + ADX过滤
========================================

【策略逻辑】
趋势跟踪的核心思想：顺势而为。
不预测顶底，只跟随已经形成的趋势。

入场条件（三个条件必须同时满足）：
  1. 价格突破过去20日最高点（唐奇安通道突破）
  2. 价格在60日均线上方（不在下降趋势中逆势）
  3. ADX > 20（趋势强度足够，不是震荡市）

出场条件（满足其一）：
  1. 价格跌破过去10日最低点（趋势反转信号）

【为什么趋势跟踪策略长期有效？】
行为金融学解释：
  - 锚定效应：投资者对突破价格反应不足，趋势会持续
  - 羊群效应：突破吸引更多买家，形成自我强化的循环
  - 处置效应：投资者过早卖出盈利头寸，趋势不会立即反转

【信号置信度计算】
  confidence = min(ADX / 50, 1.0)
  - ADX=20（最低阈值）→ confidence=0.4
  - ADX=50（强趋势）→ confidence=1.0
  - ADX=40（中等趋势）→ confidence=0.8
"""

import pandas as pd
import numpy as np
from typing import List
from strategies.base import BaseStrategy, Signal


class TrendFollowingStrategy(BaseStrategy):
    """
    趋势跟踪策略

    入场：价格突破过去20日高点（唐奇安通道突破）
    过滤：ADX > 20 + 价格在60日均线上方
    出场：价格跌破过去10日低点
    """

    def calculate_indicators(self, data: pd.DataFrame) -> pd.DataFrame:
        df = data.copy()

        # ============ 唐奇安通道 ============
        entry_p = self.config.get('entry_period', 20)
        exit_p = self.config.get('exit_period', 10)
        df['high_max'] = df['high'].rolling(entry_p).max().shift(1)
        df['low_min'] = df['low'].rolling(exit_p).min().shift(1)

        # ============ 趋势过滤均线 ============
        ma_p = self.config.get('ma_filter_period', 60)
        df['ma_filter'] = df['close'].rolling(ma_p).mean()

        # ============ ADX 计算 ============
        df['adx'] = self._calc_adx(df, 14)

        # 成交量均线（参考备用）
        df['volume_ma'] = df['volume'].rolling(20).mean()

        # ============ 分阶段止盈指标 (Step A) ============
        if self.config.get('take_profit_enabled', False):
            tp_lookback = self.config.get('take_profit_lookback', 10)
            # 最近N日最高点，不含当日（shift(1)防未来泄漏）
            df['tp_high_max'] = df['high'].rolling(tp_lookback).max().shift(1)
            # 从高点回撤比例 = (tp_high_max - close) / tp_high_max
            df['tp_drawdown'] = (df['tp_high_max'] - df['close']) / df['tp_high_max']
        else:
            df['tp_high_max'] = np.nan
            df['tp_drawdown'] = np.nan

        return df


    def generate_signals(self, data: pd.DataFrame) -> List[Signal]:
        signals = []
        if len(data) < 2:
            return signals

        current = data.iloc[-1]
        current_date = data.index[-1] if isinstance(data.index, pd.DatetimeIndex) else data['date'].iloc[-1]

        # 跳过指标未就绪的阶段
        if pd.isna(current.get('high_max')) or pd.isna(current.get('ma_filter')) or pd.isna(current.get('adx')):
            return signals

        # ────── 买入条件 ──────
        adx_threshold = self.config.get('adx_threshold', 20)
        buy_cond = (
            current['high'] > current['high_max']
            and current['close'] > current['ma_filter']
            and current['adx'] > adx_threshold
        )

        if buy_cond:
            confidence = min(current['adx'] / 50.0, 1.0)

            # ---- ATR 动态仓位控制 (2026-07-01 新增) ----
            # 当 close/atr < threshold 时，按比例缩减仓位
            # 高波动时自动减仓，避免被震出场
            base_weight = self.config.get('position_weight', 1.0)
            if self.config.get('use_atr_sizing', False) and current.get('atr', 0) > 0:
                atr_threshold = self.config.get('atr_sizing_threshold', 12)
                close_atr_ratio = current['close'] / current['atr']
                if close_atr_ratio < atr_threshold:
                    # 线性缩减：close/atr=12 → weight*100%, =6 → weight*50%
                    sizing_factor = close_atr_ratio / atr_threshold
                    base_weight *= max(sizing_factor, 0.3)  # 最低保留30%

            signals.append(Signal(
                symbol=self.symbol,
                direction=1,
                weight=base_weight,
                price=current['close'],
                confidence=round(confidence, 2),
                strategy='trend_following',
                timestamp=current_date
            ))

        # ────── 分阶段止盈 (Step A: 2026-07-01) ──────
        # 从最近10日高点回落>8% → 平掉一半仓位锁定利润
        # 剩余仓位继续按原规则奔跑，不错过后面的趋势
        if self.config.get('take_profit_enabled', False) \
                and not pd.isna(current.get('tp_drawdown', np.nan)):
            tp_drawdown_threshold = self.config.get('take_profit_drawdown', 0.08)
            tp_exit_ratio = self.config.get('take_profit_exit_ratio', 0.5)
            if current['tp_drawdown'] > tp_drawdown_threshold:
                signals.append(Signal(
                    symbol=self.symbol,
                    direction=-1,
                    weight=tp_exit_ratio,
                    price=current['close'],
                    confidence=0.7,          # 略低于通道退出的0.8
                    strategy='trend_following',
                    timestamp=current_date
                ))

        # ────── 卖出/平仓条件（通道跌破 = 趋势反转）──────
        sell_cond = (
            current['low'] < current['low_min']
        )

        if sell_cond:
            signals.append(Signal(
                symbol=self.symbol,
                direction=-1,
                weight=1.0,
                price=current['close'],
                confidence=0.8,
                strategy='trend_following',
                timestamp=current_date
            ))


        return signals

    # ─────────────── 内部方法 ───────────────

    @staticmethod
    def _calc_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
        high, low, close = df['high'], df['low'], df['close']

        # 真实波幅 TR
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs()
        ], axis=1).max(axis=1)

        # 方向运动
        up_move = high - high.shift()
        down_move = low.shift() - low

        plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0), index=df.index)
        minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0), index=df.index)

        # 平滑 ATR & DI
        atr = tr.rolling(period).mean()
        atr_safe = atr.replace(0, np.nan)
        plus_di = 100 * plus_dm.rolling(period).mean() / atr_safe
        minus_di = 100 * minus_dm.rolling(period).mean() / atr_safe

        # DX → ADX
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
        adx = dx.rolling(period).mean()

        return adx
