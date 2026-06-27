"""
========================================
  因子选股月度再平衡器
========================================

【与 BacktestEngine 的区别】
BacktestEngine 是逐日运行策略生成信号
FactorRebalancer 是每月最后一个交易日集中打分选股

【设计原理】
因子选股是"截面策略"而非"时间序列策略"：
  时间序列（择时）：「今天比昨天便宜了吗？」→ 买卖判断
  截面（选股）：「这只股票比其他股票好吗？」→ 排名判断

本模块从 FactorSelectionStrategy 提取核心因子逻辑，
但改为"每月末对所有股票统一打分"模式。

【处理流程】
每月最后一个交易日：
  1. 取所有股票过去20日数据
  2. 计算5个因子：PE/ROE/动量/量比/波动率
  3. 综合打分 → 选前N只
  4. 生成再平衡信号（卖出旧持仓、买入新选股）
"""
import pandas as pd
import numpy as np
from typing import Dict, List, Optional
from strategies.base import Signal
from config.strategy_config import FACTOR_SELECTION_CONFIG


class FactorRebalancer:
    """
    因子选股月度再平衡器

    使用示例：
        rebalancer = FactorRebalancer()
        signals = rebalancer.generate_rebalance_signals(
            data_dict=all_data,
            current_date=today,
            current_prices=prices,
            positions=current_positions,
        )
    """

    def __init__(self, config: dict = None):
        self.config = config or FACTOR_SELECTION_CONFIG
        self.factors = self.config.get('factors', [])
        self.top_n = self.config.get('top_n', 3)  # 资金有限，只选前3只
        self.previous_selected: List[str] = []    # 上次选中的股票

    def generate_rebalance_signals(self,
                                   data_dict: Dict[str, pd.DataFrame],
                                   current_date: pd.Timestamp,
                                   current_prices: Dict[str, float],
                                   current_positions: Dict[str, int]) -> List[Signal]:
        """
        生成月度再平衡信号

        参数:
            data_dict: {symbol: DataFrame} 所有股票数据
            current_date: 当前日期（月末最后一个交易日）
            current_prices: {symbol: close_price}
            current_positions: {symbol: shares}

        返回:
            Signal 列表（卖出旧股 + 买入新股）
        """
        signals: List[Signal] = []

        # ---- Step 1: 计算每只股票的综合因子得分 ----
        score_map: Dict[str, float] = {}

        for symbol, df in data_dict.items():
            if df.empty:
                continue

            # 取过去60日数据（足够计算所有因子）
            hist = df.loc[:current_date]
            if len(hist) < 30:
                continue

            score = self._compute_total_score(hist, symbol)
            if score is not None:
                score_map[symbol] = score

        if not score_map:
            return signals

        # ---- Step 2: 选出得分最高的前 N 只 ----
        sorted_symbols = sorted(score_map.items(), key=lambda x: x[1], reverse=True)
        selected = [sym for sym, _ in sorted_symbols[:self.top_n]]
        self.previous_selected = selected

        # ---- Step 3: 卖出不再持有的股票 ----
        for sym, shares in current_positions.items():
            if shares > 0 and sym not in selected:
                price = current_prices.get(sym, 0)
                if price > 0:
                    signals.append(Signal(
                        symbol=sym,
                        direction=-1,
                        weight=1.0,           # 全部卖出
                        price=price,
                        confidence=0.7,
                        strategy='factor_selection',
                        timestamp=current_date,
                    ))

        # ---- Step 4: 买入新选中的股票 ----
        weight_per_stock = 1.0 / len(selected) if selected else 0
        for sym in selected:
            price = current_prices.get(sym, 0)
            if price > 0:
                confidence = min(score_map.get(sym, 0) / 3.0, 1.0)
                signals.append(Signal(
                    symbol=sym,
                    direction=1,
                    weight=weight_per_stock,
                    price=price,
                    confidence=round(confidence, 2),
                    strategy='factor_selection',
                    timestamp=current_date,
                ))

        return signals

    def _compute_total_score(self, df: pd.DataFrame, symbol: str) -> Optional[float]:
        """计算单只股票的综合因子得分"""
        if df.empty:
            return None

        last = df.iloc[-1]
        scores = []

        for factor in self.factors:
            fname = factor['name']
            fweight = factor['weight']
            fdir = factor['direction']

            value = self._calc_factor(fname, df, last)
            if value is None:
                continue

            # 因子值 → 分数（0~1 之间）
            score = self._normalize_value(value)

            # 方向调整
            if fdir == -1:
                score = 1 - score

            scores.append(score * fweight)

        return sum(scores) if scores else None

    @staticmethod
    def _calc_factor(fname: str, df: pd.DataFrame, last_row) -> Optional[float]:
        """计算单个因子值"""
        close = df['close']
        volume = df['volume'] if 'volume' in df.columns else None

        try:
            if fname == 'momentum_1m':
                # 1月动量 = 近20日累计涨幅
                if len(close) >= 21:
                    return (close.iloc[-1] / close.iloc[-21] - 1)
                return None

            elif fname == 'volume_ratio':
                # 量比 = 当日成交量 / 20日均量
                if volume is not None and len(volume) >= 20:
                    avg_vol = volume.iloc[-20:].mean()
                    if avg_vol > 0:
                        return volume.iloc[-1] / avg_vol
                return None

            elif fname == 'volatility':
                # 20日滚动波动率
                if len(close) >= 20:
                    returns = close.pct_change().iloc[-20:]
                    return returns.std()
                return None

            elif fname in ['pe', 'roe']:
                # 财务因子：若 DataFrame 中有对应列则用，否则从 akshare 加载
                if fname in df.columns:
                    return last_row[fname]
                # 对于没有财务数据的场景，返回 0.5（中性分）
                return 0.5

        except (IndexError, KeyError, ZeroDivisionError):
            return None

        return None

    @staticmethod
    def _normalize_value(value: float) -> float:
        """将因子值归一化到 0~1 之间"""
        # 使用 sigmoid 函数将任意实数值映射到 (0, 1)
        # 对于动量/波动率等可能有正负的值，这种映射比rank更稳定
        return 1.0 / (1.0 + np.exp(-value * 3))
