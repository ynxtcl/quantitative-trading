"""
rqalpha_strategy — rqalpha 版趋势跟踪策略

逻辑与 strategies/trend_following/strategy.py 精确对齐：
- 入场：high > high_max(20期) AND close > MA(60) AND ADX > threshold
- 出场：low < low_min(10期)
- 置信度: min(ADX / 50, 1.0)

注意：原始策略使用 `shift(1)` 避免未来信息泄露，
rqalpha 的 `history_bars` 已经只返回历史数据，因此不需要额外 shift。
"""

import logging
from typing import Optional, Tuple, Callable

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 指标计算（纯 numpy，与原策略 logic 保持一致）
# ---------------------------------------------------------------------------

def calc_donchian_high(high: np.ndarray, period: int = 20) -> float:
    """唐奇安上轨：过去 period 日最高价（不含当日）"""
    if len(high) < period:
        return 0.0
    return float(np.max(high[-period:]))


def calc_donchian_low(low: np.ndarray, period: int = 10) -> float:
    """唐奇安下轨：过去 period 日最低价（不含当日）"""
    if len(low) < period:
        return 0.0
    return float(np.min(low[-period:]))


def calc_sma(close: np.ndarray, period: int = 60) -> float:
    """简单移动平均"""
    if len(close) < period:
        return 0.0
    return float(np.mean(close[-period:]))


def calc_adx(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> float:
    """
    计算 ADX（平均趋向指数）。
    使用滚动均值平滑，与原策略 strategies/trend_following/strategy.py 一致。
    """
    if len(close) < period * 2:
        return 0.0

    high_a = np.asarray(high, dtype=float)
    low_a = np.asarray(low, dtype=float)
    close_a = np.asarray(close, dtype=float)

    # 真实波幅 TR
    tr1 = high_a[1:] - low_a[1:]
    tr2 = np.abs(high_a[1:] - close_a[:-1])
    tr3 = np.abs(low_a[1:] - close_a[:-1])
    tr = np.maximum(np.maximum(tr1, tr2), tr3)

    # 方向运动
    up_move = high_a[1:] - high_a[:-1]
    down_move = low_a[:-1] - low_a[1:]

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    # 滚动均值平滑（与原策略一致）
    def rolling_mean(arr: np.ndarray, p: int) -> np.ndarray:
        out = np.full(len(arr), np.nan)
        if len(arr) < p:
            return out
        cumsum = np.cumsum(np.insert(arr, 0, 0))
        out[p - 1:] = (cumsum[p:] - cumsum[:-p]) / p
        return out

    atr = rolling_mean(tr, period)
    plus_dm_smooth = rolling_mean(plus_dm, period)
    minus_dm_smooth = rolling_mean(minus_dm, period)

    eps = 1e-10
    atr_safe = np.where(atr > eps, atr, eps)
    plus_di = 100 * plus_dm_smooth / atr_safe
    minus_di = 100 * minus_dm_smooth / atr_safe

    dx = 100 * np.abs(plus_di - minus_di) / np.where((plus_di + minus_di) > eps, plus_di + minus_di, eps)
    adx = rolling_mean(dx, period)

    if np.isnan(adx[-1]):
        return 0.0
    return float(adx[-1])


# ---------------------------------------------------------------------------
# rqalpha 策略工厂（通过 history_bars 获取数据）
# ---------------------------------------------------------------------------

def make_trend_following_strategy(
    entry_period: int = 20,
    exit_period: int = 10,
    ma_filter_period: int = 60,
    adx_threshold: float = 20.0,
    adx_period: int = 14,
    position_weight: float = 1.0,
) -> Tuple[Callable, Callable]:
    """
    创建 rqalpha 趋势跟踪策略的 init / handle_bar 函数对。

    逻辑与 strategies/trend_following/strategy.py 完全一致：
    - 入场：high > 20日最高 AND close > 60日均线 AND ADX > threshold
    - 出场：low < 10日最低
    - 置信度: min(ADX / 50, 1.0)

    注意：此版本依赖 rqalpha 的 history_bars() 从数据源获取数据，
    需要有效的数据 bundle 或自定义数据源。如果 rqalpha 环境不可用，
    请使用 make_self_contained_strategy() 替代。
    """

    def init(context):
        context.symbol = None
        context.entry_period = entry_period
        context.exit_period = exit_period
        context.ma_filter_period = ma_filter_period
        context.adx_threshold = adx_threshold
        context.adx_period = adx_period
        context.position_weight = position_weight
        context.bar_count = 0
        context.in_position = False
        context.last_signal = ""

    def handle_bar(context, bar_dict):
        symbol = context.symbol
        if symbol is None:
            return

        context.bar_count += 1
        # 需要足够的数据计算指标
        min_bars = max(context.entry_period, context.ma_filter_period, context.adx_period) + 2
        if context.bar_count < min_bars:
            return

        # --- 获取历史数据 ---
        lookback = max(context.entry_period, context.ma_filter_period, context.adx_period) + 2
        high_arr = history_bars(symbol, lookback, "1d", "high")
        low_arr = history_bars(symbol, lookback, "1d", "low")
        close_arr = history_bars(symbol, lookback, "1d", "close")

        if high_arr is None or low_arr is None or close_arr is None:
            return
        if len(high_arr) < lookback:
            return

        # --- 计算指标 ---
        # 唐奇安通道（不含当日 = 用 history_bars 返回的除最后一项外的数据）
        high_hist = high_arr[:-1] if len(high_arr) > 0 else high_arr
        low_hist = low_arr[:-1] if len(low_arr) > 0 else low_arr
        close_hist = close_arr[:-1] if len(close_arr) > 0 else close_arr

        # 确保有足够的历史数据
        if len(high_hist) < context.entry_period or len(low_hist) < context.exit_period:
            return

        high_max = calc_donchian_high(high_hist, context.entry_period)
        low_min = calc_donchian_low(low_hist, context.exit_period)
        ma_filter = calc_sma(close_arr, context.ma_filter_period)  # 包含当日数据（原策略用 shift(1)，这里 history_bars 已经不含未来）
        adx_val = calc_adx(high_arr, low_arr, close_arr, context.adx_period)

        current_close = float(bar_dict[symbol].last)
        current_high = float(bar_dict[symbol].high)
        current_low = float(bar_dict[symbol].low)

        pos = get_position(symbol).quantity
        trend_ok = adx_val > context.adx_threshold

        # --- 卖出逻辑（优先）---
        if pos > 0 and current_low < low_min:
            order_target_percent(symbol, 0)
            context.in_position = False
            context.last_signal = "exit"

        # --- 买入逻辑 ---
        if pos == 0 and trend_ok and current_high > high_max and current_close > ma_filter:
            confidence = min(adx_val / 50.0, 1.0)
            order_target_percent(symbol, context.position_weight * confidence)
            context.in_position = True
            context.last_signal = "entry"

    return init, handle_bar


# ---------------------------------------------------------------------------
# 自包含策略工厂（闭包捕获数据，不依赖 rqalpha 数据源）
# ---------------------------------------------------------------------------

def make_self_contained_strategy(
    symbol: str,
    ohlcv: pd.DataFrame,
    entry_period: int = 20,
    exit_period: int = 10,
    ma_filter_period: int = 60,
    adx_threshold: float = 20.0,
    adx_period: int = 14,
    position_weight: float = 1.0,
) -> Tuple[Callable, Callable]:
    """
    创建自包含趋势跟踪策略的 init / handle_bar 函数对。

    与 make_trend_following_strategy 的核心区别：
    - 不调用 history_bars() 从 rqalpha 数据源获取数据
    - 改为通过闭包直接捕获 ohlcv DataFrame，从中切片数据
    - 无需 rqalpha data bundle，无需自定义数据源

    策略逻辑完全一致：
    - 入场：high > 20日最高 AND close > 60日均线 AND ADX > threshold
    - 出场：low < 10日最低
    - 置信度: min(ADX / 50, 1.0)

    参数
    -----
    symbol : str
        rqalpha 代码，如 '000001.XSHE' — 仅用于下单接口，数据从 ohlcv 获取
    ohlcv : pd.DataFrame
        标准 OHLCV 数据，索引为 DatetimeIndex，含 open/high/low/close/volume 列
    entry_period : int
        唐奇安上轨周期
    exit_period : int
        唐奇安下轨周期
    ma_filter_period : int
        SMA 过滤周期
    adx_threshold : float
        ADX 入场阀值
    adx_period : int
        ADX 计算周期
    position_weight : float
        仓位权重
    """
    # 闭包捕获：转为 numpy 加速访问
    _dates = ohlcv.index.tolist()
    _high = ohlcv['high'].to_numpy(dtype=np.float64)
    _low = ohlcv['low'].to_numpy(dtype=np.float64)
    _close = ohlcv['close'].to_numpy(dtype=np.float64)

    _lookback_needed = max(entry_period, ma_filter_period, adx_period) + 2

    def init(context):
        context.symbol = symbol
        context.bar_pos = 0
        context.entry_period = entry_period
        context.exit_period = exit_period
        context.ma_filter_period = ma_filter_period
        context.adx_threshold = adx_threshold
        context.adx_period = adx_period
        context.position_weight = position_weight
        context.in_position = False
        context.last_signal = ""

    def handle_bar(context, bar_dict):
        i = context.bar_pos

        # 跳过前 N 期（不够计算指标）
        if i < _lookback_needed:
            context.bar_pos += 1
            return

        # --- 从闭包数据切片（不调用 history_bars）---
        # 唐奇安通道使用 i 之前的数据（不含当日）
        hist_start = max(0, i - entry_period)
        hist_high = _high[hist_start:i]
        hist_low = _low[max(0, i - exit_period):i]

        high_max = calc_donchian_high(hist_high, entry_period)
        low_min = calc_donchian_low(hist_low, exit_period)

        # SMA（包含当日到 i+1）
        sma_close = _close[max(0, i - ma_filter_period + 1):i + 1]
        sma_val = calc_sma(sma_close, ma_filter_period)

        # ADX（使用到当日为止的全部数据）
        adx_val = calc_adx(_high[:i + 1], _low[:i + 1], _close[:i + 1], adx_period)

        # 通过 bar_dict 获取当前报价（用于下单决策）
        current_close = float(bar_dict[context.symbol].close)
        current_high = float(bar_dict[context.symbol].high)
        current_low = float(bar_dict[context.symbol].low)

        pos = get_position(context.symbol).quantity
        trend_ok = adx_val > adx_threshold

        # --- 卖出逻辑（优先）---
        if pos > 0 and current_low < low_min:
            order_target_percent(context.symbol, 0)
            context.in_position = False
            context.last_signal = "exit"

        # --- 买入逻辑 ---
        if pos == 0 and trend_ok and current_high > high_max and current_close > sma_val:
            confidence = min(adx_val / 50.0, 1.0)
            order_target_percent(context.symbol, position_weight * confidence)
            context.in_position = True
            context.last_signal = "entry"

        context.bar_pos += 1

    return init, handle_bar
