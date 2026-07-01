"""
========================================
 策略B：均值回归 — 布林带 + RSI确认
========================================

【策略逻辑 — 与趋势跟踪相反】
趋势跟踪赌"趋势会持续"，均值回归赌"价格会回到均值"。
在震荡市中，均值回归表现优异；在单边市中会持续亏损。

入场条件：
  1. 价格触及布林带下轨（close < bb_lower）
  2. RSI < 30（超卖状态）
  3. 布林带宽度 > 5%（防止极度压缩时入场）
  4. 价格不低于EMA50的85%（避免接飞刀）

出场条件：
  1. 价格触及布林带上轨（close > bb_upper）
  2. RSI > 70（超买状态）

【行为金融学基础】
均值回归的心理学依据是"过度反应"：
- 投资者对负面消息反应过度，导致价格跌过头
- 这种过度反应是暂时的，价格最终会回到合理区间
- 均值回归就是利用这种过度反应反向交易

【风险提示】
均值回归的致命弱点：趋势一旦形成，回归可能永远不会发生。
"价格可以长期处于非理性状态"——凯恩斯
"""

import pandas as pd
import numpy as np
from typing import List
from strategies.base import BaseStrategy, Signal


class MeanReversionStrategy(BaseStrategy):
    """
    均值回归策略

    入场：价格触及布林带下轨 + RSI超卖
    出场：价格回到中轨 或 触及上轨 + RSI超买
    过滤：趋势过滤（不在强单边市中逆势）
    """

    def calculate_indicators(self, data: pd.DataFrame) -> pd.DataFrame:
        df = data.copy()

        # ============ 布林带（Bollinger Bands）============
        # 由 John Bollinger 在1980年代发明
        # 上轨 = MA + kσ    下轨 = MA - kσ
        # 其中 k 为标准差倍数（通常=2），σ 为滚动标准差
        #
        # 统计意义：假设价格收益率服从正态分布
        # k=2 时，价格在轨道内的概率≈95%
        # 所以价格触及下轨 = 只有2.5%的概率比这更低 = 超卖
        bb_p = self.config.get('bb_period', 20)
        bb_s = self.config.get('bb_std', 2.0)
        df['bb_ma'] = df['close'].rolling(bb_p).mean()
        df['bb_std'] = df['close'].rolling(bb_p).std()
        df['bb_upper'] = df['bb_ma'] + bb_s * df['bb_std']
        df['bb_lower'] = df['bb_ma'] - bb_s * df['bb_std']

        # 布林带宽度 = (上轨-下轨)/中轨
        # 衡量波动率大小。宽度越宽 = 波动越大
        # 当布林带非常窄时（挤压），往往预示着大行情即将爆发
        # 此时做均值回归风险很大（突破方向不确定）
        df['bb_width'] = (df['bb_upper'] - df['bb_lower']) / df['bb_ma'].replace(0, np.nan)

        # ============ RSI（相对强弱指数）============
        rsi_p = self.config.get('rsi_period', 14)
        df['rsi'] = self._calc_rsi(df['close'], rsi_p)
        # RSI 范围 0-100：
        #   < 30 = 超卖（价格可能反弹）
        #   > 70 = 超买（价格可能回落）
        #   30-70 = 正常区间
        #
        # 注意：在强趋势市场中，RSI可以在超买/超卖区长时间停留
        # 2017-2021年比特币牛市，RSI曾连续数月>70
        # 如果仅凭RSI>70就做空，会亏光

        # ============ 趋势过滤 ============
        # 使用 EMA(50) 而不是 SMA(50)
        # 因为 EMA 对近期价格变化更敏感
        # 均值回归需要"及时发现趋势变化"——防止在趋势中逆势
        # 所以更敏感的EMA更适合
        df['ema_50'] = df['close'].ewm(span=50).mean()

        # ============ ATR 止损 ============
        # 真实波幅均值 — 衡量日均波动范围
        # 用于动态止损：ATR越大，止损越宽
        atr_p = self.config.get('atr_period', 14)
        df['atr'] = self._calc_atr(df, atr_p)
        # 持仓期间的止损价 = 买入价 - stop_loss * ATR
        # 例如买入价 10 元，ATR=0.5，stop_loss_atr=2
        # → 止损价 = 10 - 2*0.5 = 9.0 元（跌5%触发）

        # ============ 成交量确认 ============
        # 缩量止跌信号：当日成交量 < 20日均量 * 0.8
        # 说明抛压衰竭，是较好的入场时机
        df['vol_ma20'] = df['volume'].rolling(20).mean().replace(0, np.nan)
        df['vol_ratio'] = df['volume'] / df['vol_ma20']
        # vol_ratio < 0.8 = 缩量，> 1.5 = 放量

        # ============ 偏离度 ============
        # (价格 - 均值) / 标准差，类似Z-score
        # deviation=2 意味着价格在2σ的位置
        # deviation=-2.5 意味着价格在-2.5σ的位置——强烈的回归信号
        df['deviation'] = (df['close'] - df['bb_ma']) / (df['bb_std'].replace(0, np.nan) + 1e-8)

        return df

    def generate_signals(self, data: pd.DataFrame) -> List[Signal]:
        signals = []
        if len(data) < 2:
            return signals

        current = data.iloc[-1]
        current_date = data.index[-1] if isinstance(data.index, pd.DatetimeIndex) else data['date'].iloc[-1]

        oversold = self.config.get('rsi_oversold', 30)
        overbought = self.config.get('rsi_overbought', 70)

        # ────── 买入信号：下轨 + 超卖 + 成交量确认 ──────
        buy_cond = (
            current['close'] < current['bb_lower']
            and current['rsi'] < oversold
            and current['bb_width'] > self.config.get('min_bb_width', 0.05)
            and current['bb_width'] < self.config.get('max_bb_width', 0.20)  # 排除极端波动
        )

        # 趋势过滤（仅在震荡/上涨趋势中做多）
        # 如果 close < ema_50 * 0.97，说明价格低于中期趋势线
        # 原为 0.85，改为 0.97 后要求价格在中轨附近才能入场
        # 这大大降低了"接飞刀"的风险
        if buy_cond and self.config.get('trend_filter', True):
            trend_threshold = self.config.get('trend_filter_level', 0.97)
            buy_cond = buy_cond and (current['close'] > current['ema_50'] * trend_threshold)

        # 成交量确认：缩量止跌（抛压衰竭）或恐慌放量（底部换手）
        if buy_cond and self.config.get('volume_filter', True):
            vol_low = self.config.get('vol_low_threshold', 0.8)
            vol_high = self.config.get('vol_high_threshold', 2.0)
            vol_cond = (
                (current['vol_ratio'] < vol_low) or (current['vol_ratio'] > vol_high)
            )
            buy_cond = buy_cond and vol_cond

        if buy_cond:
            confidence = min(abs(current['deviation']) / 3.0, 1.0)

            # ---- 偏离度动态仓位 (2026-07-01 新增) ----
            # 偏离度 Z-score 越大 → 回归信号越强 → 仓位越高
            # Z=2 (触及下轨) → weight * 1.0
            # Z=3 (极度超卖) → weight * 1.3 (最多加30%)
            base_weight = self.config.get('position_weight', 0.7)
            deviation = abs(current.get('deviation', 0))
            if self.config.get('use_deviation_confidence', False) and deviation > 2.0:
                deviation_multiplier = 1.0 + (deviation - 2.0) * 0.3  # Z=2→1.0x, Z=3→1.3x
                base_weight *= min(deviation_multiplier, 1.3)

            # ---- 布林带收缩突破预警 (2026-07-01 新增) ----
            # 当布林带极度收缩后开始扩张且价格触及下轨 → 反弹概率更高
            if 'bb_width' in current.index and current['bb_width'] < 0.08:
                base_weight *= 1.1  # 收缩后反弹加仓10%

            signals.append(Signal(
                symbol=self.symbol,
                direction=1,
                weight=round(base_weight, 4),
                price=current['close'],
                confidence=round(confidence, 2),
                strategy='mean_reversion',
                timestamp=current_date
            ))

        # ────── 卖出信号：上轨 + 超买 ──────
        sell_cond = (
            current['close'] > current['bb_upper']
            and current['rsi'] > overbought
        )

        # ────── ATR 止损卖出：数据驱动（无需持仓状态）──────
        # 如果价格跌破 bb_lower 超过 stop_loss_atr 倍的 ATR，
        # 说明超卖恶化而非回归——止损离场
        # 这是纯数据驱动的止损，不依赖策略内部状态
        stop_loss_atr = self.config.get('stop_loss_atr', 0)
        if not sell_cond and stop_loss_atr > 0:
            stop_line = current['bb_lower'] - stop_loss_atr * current.get('atr', 0)
            if current['close'] < stop_line:
                sell_cond = True

        if sell_cond:
            confidence = min(abs(current['deviation']) / 3.0, 1.0)
            signals.append(Signal(
                symbol=self.symbol,
                direction=-1,
                weight=self.config.get('position_weight', 0.5),
                price=current['close'],
                confidence=round(confidence, 2),
                strategy='mean_reversion',
                timestamp=current_date
            ))

        return signals

    # ─────────────── 内部方法 ───────────────

    @staticmethod
    def _calc_rsi(close: pd.Series, period: int = 14) -> pd.Series:
        """
        计算RSI指标

        RSI = 100 - 100/(1 + RS)
        RS = 平均涨幅 / 平均跌幅（过去N日）

        为什么RSI有效？
        - RSI衡量的是"上涨速度 vs 下跌速度"的比率
        - 当价格急跌时：平均跌幅增大 → RS变小 → RSI变低
        - 但"快速下跌"往往意味着"跌过头"——价格应该回归
        - 这就是RSI超卖信号的逻辑基础

        注意：RSI的计算有很多种变体
        - Wilder原始RSI：使用平滑移动平均
        - Cutler's RSI：使用简单移动平均（本代码采用的是这种）
        - 两者差别不大，Cutler版本更简单计算
        """
        delta = close.diff()
        gain = delta.clip(lower=0)     # 只保留涨幅（负值变0）
        loss = (-delta).clip(lower=0)  # 只保留跌幅（正值变0）

        avg_gain = gain.rolling(period).mean()
        avg_loss = loss.rolling(period).mean()

        rs = avg_gain / avg_loss.replace(0, np.nan)  # 防止除零
        rsi = 100 - (100 / (1 + rs))
        return rsi

    @staticmethod
    def _calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        """
        计算 ATR（Average True Range，平均真实波幅）

        ATR = 过去 N 天的真实波幅平均值
        真实波幅 = max(当日最高-当日最低, |当日最高-前日收盘|, |当日最低-前日收盘|)

        为什么均值回归需要 ATR？
        - ATR 衡量的是"价格平均每天波动多少"
        - 在高波动环境下（ATR 大），止损位需要更宽，否则会被噪音止损出局
        - 在低波动环境下（ATR 小），止损位可以更窄，控制风险
        - 这就是"动态止损"——用 ATR 自动适应市场波动
        """
        high = df['high']
        low = df['low']
        close = df['close']

        prev_close = close.shift(1)
        tr1 = high - low
        tr2 = (high - prev_close).abs()
        tr3 = (low - prev_close).abs()

        # 三者取最大值 = 真实波幅
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(period).mean()
        return atr
