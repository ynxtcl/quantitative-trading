# -*- coding: utf-8 -*-
"""
========================================
  特征工程 — 从原始 OHLCV 派生 ML 特征
========================================

【设计哲学】
1. 所有特征只能使用历史数据（.shift(1) 保证不包含未来信息）
2. 特征命名统一前缀，便于 XGBoost 调用
3. NaN 值处理：用 0 填充（XGBoost 原生支持缺失值，但统一为 0 更可控）
4. 特征分 5 类：动量/通道/趋势/波动率/成交量

【特征列表（共 22 维）v3 — 标签改为 5 日 horizon】
  ╔══════════════════╤═══════════════════╤═══════════════════════╗
  ║ 类别             │ 特征              │ 计算方法               ║
  ╠══════════════════╪═══════════════════╪═══════════════════════╣
  ║ 动量(5维)        │ return_1d         │ close.pct_change(1)    ║
  ║                  │ return_5d         │ close.pct_change(5)    ║
  ║                  │ return_10d        │ close.pct_change(10)   ║
  ║                  │ return_20d        │ close.pct_change(20)   ║
  ║                  │ return_60d        │ close.pct_change(60)   ║
  ╠══════════════════╪═══════════════════╪═══════════════════════╣
  ║ 通道(3维)        │ high_max_20       │ high.rolling(20).max   ║
  ║                  │ low_min_10        │ low.rolling(10).min    ║
  ║                  │ high_max_60       │ high.rolling(60).max   ║
  ╠══════════════════╪═══════════════════╪═══════════════════════╣
  ║ 趋势+强度(5维)   │ ma_60_ratio       │ close / MA(60)         ║
  ║                  │ ema_50_ratio      │ close / EMA(50)        ║
  ║                  │ adx_14            │ ADX(14) 指标           ║
  ║                  │ price_vs_52w_high │ close / 252日最高 (新增)║
  ║                  │ price_vs_52w_low  │ close / 252日最低 (新增)║
  ╠══════════════════╪═══════════════════╪═══════════════════════╣
  ║ 波动率(5维)      │ volatility_5      │ return.std(5)          ║
  ║                  │ volatility_20     │ return.std(20)         ║
  ║                  │ atr_14            │ ATR(14) 真实波幅       ║
  ║                  │ bb_width          │ 布林带宽度(2σ)         ║
  ║                  │ daily_range_pct   │ (high-low)/close (新增)║
  ╠══════════════════╪═══════════════════╪═══════════════════════╣
  ║ 成交量(3维)      │ volume_ratio      │ vol / MA(20)vol        ║
  ║                  │ volume_1d         │ volume.pct_change(1)   ║
  ║                  │ volume_5d         │ volume.pct_change(5)   ║
  ╠══════════════════╪═══════════════════╪═══════════════════════╣
  ║ 交叉(1维)        │ ma_20_vs_ma_60    │ MA20/MA60 趋势对比(新增)║
  ╚══════════════════╧═══════════════════╧═══════════════════════╝
  【v3 变更说明】
  - 标签：forecast_horizon 默认从 1 改为 5（预测未来5日涨跌，过滤日间噪音）
  - 特征保持不变
"""

import pandas as pd
import numpy as np
from typing import List


# 所有特征列的完整列表（v2 — 剔除死特征，新增替代特征）
# 变更：移除 close_rank / vol_rank（单股票下恒为 0.5，零信息量）
#       新增: price_vs_52w_high / price_vs_52w_low / daily_range_pct / ma_20_vs_ma_60
FEATURE_COLS = [
    'return_1d', 'return_5d', 'return_10d', 'return_20d', 'return_60d',
    'high_max_20', 'low_min_10', 'high_max_60',
    'ma_60_ratio', 'ema_50_ratio', 'adx_14',
    'price_vs_52w_high', 'price_vs_52w_low',
    'volatility_5', 'volatility_20', 'atr_14', 'bb_width', 'daily_range_pct',
    'volume_ratio', 'volume_1d', 'volume_5d',
    'ma_20_vs_ma_60',
]


def engineer_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    从原始 OHLCV DataFrame 派生全部特征

    参数:
        df: 原始 OHLCV DataFrame（列：date, open, high, low, close, volume, amount）

    返回:
        带全部特征列的 DataFrame（原始列保留 + 新增特征列）
        缺失值填充为 0
    """
    data = df.copy()

    # ──────────────────────────────────────────
    #  1. 动量特征（不同时间窗口的收益率）
    # ──────────────────────────────────────────
    for period in [1, 5, 10, 20, 60]:
        # shift(1) 保证收益率不包含当日信息
        data[f'return_{period}d'] = data['close'].pct_change(period).shift(1)

    # ──────────────────────────────────────────
    #  2. 通道特征（价格突破位置）
    # ──────────────────────────────────────────
    data['high_max_20'] = data['high'].rolling(20).max().shift(1)
    data['low_min_10'] = data['low'].rolling(10).min().shift(1)
    data['high_max_60'] = data['high'].rolling(60).max().shift(1)

    # ──────────────────────────────────────────
    #  3. 趋势特征（均线位置 + ADX + 52周位置）
    # ──────────────────────────────────────────
    data['ma_60'] = data['close'].rolling(60).mean()
    data['ma_60_ratio'] = data['close'] / data['ma_60'].replace(0, np.nan)

    data['ema_50'] = data['close'].ewm(span=50, adjust=False).mean()
    data['ema_50_ratio'] = data['close'] / data['ema_50'].replace(0, np.nan)

    # ADX 计算（复用趋势跟踪策略的算法）
    data['adx_14'] = _calc_adx(data, 14)

    # ★ v2: 52 周最高/最低位置 — 个股在长周期中的位置
    # price_vs_52w_high 接近 1.0 → 股价处于 52 周高位（强势）
    # price_vs_52w_low  接近 1.0 → 股价处于 52 周低位（弱势）
    data['price_vs_52w_high'] = (data['close'] / data['close'].rolling(252).max()).shift(1)
    data['price_vs_52w_low']  = (data['close'] / data['close'].rolling(252).min()).shift(1)

    # ──────────────────────────────────────────
    #  4. 波动率特征
    # ──────────────────────────────────────────
    daily_ret = data['close'].pct_change()

    data['volatility_5'] = daily_ret.rolling(5).std().shift(1)
    data['volatility_20'] = daily_ret.rolling(20).std().shift(1)

    # ATR（真实波幅均值）
    data['atr_14'] = _calc_atr(data, 14).shift(1)

    # 布林带宽度（(上轨 - 下轨) / 中轨）
    bb_mid = data['close'].rolling(20).mean()
    bb_std = data['close'].rolling(20).std()
    bb_upper = bb_mid + 2 * bb_std
    bb_lower = bb_mid - 2 * bb_std
    data['bb_width'] = ((bb_upper - bb_lower) / bb_mid.replace(0, np.nan)).shift(1)

    # ★ v2: 日波动真实幅度 (high - low) / close
    # 补充 bb_width 的不足：bb_width 是 20 日带宽
    # daily_range_pct 是当日真实波动——高波日往往伴随反转
    data['daily_range_pct'] = ((data['high'] - data['low']) / data['close'].replace(0, np.nan)).shift(1)

    # ──────────────────────────────────────────
    #  5. 成交量特征
    # ──────────────────────────────────────────
    data['volume_ma_20'] = data['volume'].rolling(20).mean()
    data['volume_ratio'] = (data['volume'] / data['volume_ma_20'].replace(0, np.nan)).shift(1)

    data['volume_1d'] = data['volume'].pct_change(1).shift(1)
    data['volume_5d'] = data['volume'].pct_change(5).shift(1)

    # ──────────────────────────────────────────
    #  6. 交叉趋势特征（v2 新增替代原截面特征）
    #     ma_20_vs_ma_60: MA20 / MA60 的比值
    #     > 1.0 = 快线在慢线上方（多头排列，趋势向上）
    #     < 1.0 = 快线在慢线下方（空头排列，趋势向下）
    #     值越大 → 上涨趋势越陡峭
    # ──────────────────────────────────────────
    data['ma_20'] = data['close'].rolling(20).mean()
    data['ma_20_vs_ma_60'] = (data['ma_20'] / data['ma_60'].replace(0, np.nan)).shift(1)

    # ──────────────────────────────────────────
    #  清理：将所有 NaN / inf 替换为 0
    #  XGBoost 可以处理 NaN，但 0 更可控
    # ──────────────────────────────────────────
    for col in FEATURE_COLS:
        if col in data.columns:
            data[col] = data[col].fillna(0).replace([np.inf, -np.inf], 0)

    return data


def engineer_feature_matrix(df: pd.DataFrame) -> pd.DataFrame:
    """
    生成 XGBoost 训练/推理用的特征矩阵
    - 调用 engineer_features 派生特征
    - 只返回特征列（不含 OHLCV）
    - 删除全零的行（这些行指标未就绪，不应参与训练）
    """
    data = engineer_features(df)

    # 删除特征全为零的行（指标未就绪阶段）
    feature_data = data[FEATURE_COLS].copy()
    # 保留至少有一个非零特征的行
    valid_mask = (feature_data.abs().sum(axis=1) > 1e-8)
    feature_data = feature_data[valid_mask]

    return feature_data


def prepare_training_data(
    df: pd.DataFrame,
    min_samples: int = 100,
    forecast_horizon: int = 5,
) -> tuple:
    """
    准备 XGBoost 训练数据 (v3 — 默认 5 日 horizon)

    参数:
        df: 原始 OHLCV DataFrame
        min_samples: 最少样本数（不足则返回空）
        forecast_horizon: 预测 horizon（默认5=未来5日涨跌，v3 从1改为5）

    返回:
        (X, y) 元组
        X: 特征矩阵 DataFrame
        y: 标签 Series（1=涨, 0=跌）

    【设计考量】
    forecast_horizon=5 权衡了信噪比与交易频率：
    - 1日：噪音大（A股随机性强），信号交易过于频繁，滑点成本高
    - 5日：过滤日间噪音，适合持仓3-5天的中低频交易，与TrendFollowing可比
    - 20日+：信号太少，不适合1200条数据的训练窗口
    """
    data = engineer_features(df)

    if len(data) < min_samples:
        return pd.DataFrame(), pd.Series(dtype=float)

    # 标签 = 未来 forecast_horizon 日的涨跌幅
    future_ret = data['close'].pct_change(forecast_horizon).shift(-forecast_horizon)

    # 构建特征矩阵（排除前60行的 NaN）
    X = data[FEATURE_COLS].copy()
    y = (future_ret > 0).astype(int)  # 二分类：1=涨, 0=跌

    # 删除 NaN 行
    valid = ~(X.isnull().any(axis=1) | y.isna())
    X = X[valid].fillna(0).replace([np.inf, -np.inf], 0)
    y = y[valid]

    # 删除特征全零的行（指标未就绪阶段）
    non_zero = X.abs().sum(axis=1) > 1e-8
    X = X[non_zero]
    y = y[non_zero]

    return X, y


# ─────────────── 内部指标计算 ───────────────

def _calc_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """计算 ADX（平均趋向指数）"""
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

    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0),
        index=df.index
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0),
        index=df.index
    )

    # 平滑 ATR & DI
    atr = tr.rolling(period).mean()
    atr_safe = atr.replace(0, np.nan)
    plus_di = 100 * plus_dm.rolling(period).mean() / atr_safe
    minus_di = 100 * minus_dm.rolling(period).mean() / atr_safe

    # DX → ADX
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.rolling(period).mean()

    return adx


def _calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """计算 ATR（平均真实波幅）"""
    high, low, close = df['high'], df['low'], df['close']
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()
