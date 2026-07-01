"""
========================================
  XGBoost 信号策略 — 继承 BaseStrategy
========================================

【设计】
XGBoostSignalStrategy 继承 BaseStrategy（模板方法模式），
通过机器学习模型替代传统规则（如唐奇安通道/布林带）生成信号。

【与现有策略的区别】

        传统策略                        XGBoost 策略
    ┌─────────────────┐           ┌────────────────────────────┐
    │ 规则A: 通道突破   │          │ 特征工程(20维) → XGBoost   │
    │ 规则B: ADX过滤    │   VS     │ → predict_proba()         │
    │ 规则C: MA60过滤   │          │ → 阈值判定 → Signal       │
    │ → 布尔逻辑 → SNL  │          │ → ML 概率输出             │
    └─────────────────┘           └────────────────────────────┘

【训练 vs 推理模式】
  - 训练模式：在 Walk-Forward 训练集上调用 fit() 训练 XGBoost
  - 推理模式：在测试集上只用 predict_proba() 生成信号
  - 通过 model 参数区分：model=None → 无信号（训练前），model=model → 推理

【阈值策略】
  - prob > 0.55  → 买入（需要一定置信度）
  - prob < 0.45  → 卖出（看跌信号）
  - 0.45~0.55    → 不操作（不确定区域）
  - 置信度 = max(prob, 1-prob) 映射到 Signal.confidence
"""

import pandas as pd
import numpy as np
from typing import List, Optional

from strategies.base import BaseStrategy, Signal
from models.feature_engineering import FEATURE_COLS


class XGBoostSignalStrategy(BaseStrategy):
    """
    XGBoost 增强信号策略

    参数:
        name: 策略名称（如 'xgboost_tf'）
        config: 配置字典
            - symbol: 股票代码
            - threshold_buy: 买入阈值（默认 0.55）
            - threshold_sell: 卖出阈值（默认 0.45）
            - position_weight: 信号权重（默认 1.0）
        model: 训练好的 XGBClassifier 实例
            为 None 时，generate_signals 返回空列表（训练前状态）
    """

    def __init__(
        self,
        name: str,
        config: dict,
        model=None,
    ):
        super().__init__(name, config)
        self.model = model
        self.feature_cols = config.get('feature_cols', FEATURE_COLS)
        self.threshold_buy = config.get('threshold_buy', 0.55)
        self.threshold_sell = config.get('threshold_sell', 0.45)
        self.position_weight = config.get('position_weight', 1.0)

        # 缓存最近的特征（用于分析）
        self.last_features = None
        self.last_prob = None

    def calculate_indicators(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        特征工程 — 从 OHLCV 派生 ML 特征（v4: 延迟导入，修复循环依赖）

        此方法在 run() 的模板方法中被调用。
        如果数据已包含特征列（预计算方案），直接返回。
        如果没有特征列，延迟导入 engineer_features 做实时特征工程。

        设计考量：
        - 延迟导入（lazy import）避免 models/feature_engineering 的循环依赖
        - 预计算方案（推荐）：在引擎外部调用 engineer_features()，性能最优
        - 实时计算方案（备选）：引擎传入原始 OHLCV 时自动计算
        """
        # 检查是否已有特征列
        if all(col in data.columns for col in self.feature_cols):
            return data

        # 延迟导入，避免循环依赖
        from models.feature_engineering import engineer_features
        return engineer_features(data)


    def generate_signals(self, data: pd.DataFrame) -> List[Signal]:
        """
        ML 推理 → 信号生成

        流程：
        1. 模型为 None → 无信号（训练阶段）
        2. 从 data 构建特征向量（最新行）
        3. predict_proba() → 上涨概率
        4. 概率转 Signal（买入/卖出/持有）
        """
        if self.model is None:
            return []

        if len(data) < 2:
            return []

        # 获取最新行
        current = data.iloc[-1]
        current_date = (data.index[-1]
                        if isinstance(data.index, pd.DatetimeIndex)
                        else pd.Timestamp.now())

        # 构建特征向量
        # 如果 data 没有特征列（来自 BacktestEngine 的原始 OHLCV），则返回空
        if not all(col in data.columns for col in self.feature_cols):
            return []

        features = pd.DataFrame([current[self.feature_cols]]).fillna(0)

        # 检查是否有有效特征
        if features.abs().sum(axis=1).iloc[0] < 1e-8:
            return []

        # ---- ML 推理 ----
        try:
            prob = self.model.predict_proba(features)[0, 1]  # P(上涨)
        except Exception:
            return []

        self.last_prob = prob
        self.last_features = features

        # ---- 转换为信号 ----
        signals = []

        if prob > self.threshold_buy:
            # 买入信号
            confidence = min(prob, 1.0)
            signals.append(Signal(
                symbol=self.symbol,
                direction=1,
                weight=self.position_weight,
                price=current['close'],
                confidence=round(confidence, 2),
                strategy=self.name,
                timestamp=current_date,
            ))

        elif prob < self.threshold_sell:
            # 卖出信号（平仓）
            confidence = min(1 - prob, 1.0)
            signals.append(Signal(
                symbol=self.symbol,
                direction=-1,
                weight=self.position_weight,
                price=current['close'],
                confidence=round(confidence, 2),
                strategy=self.name,
                timestamp=current_date,
            ))

        # prob between threshold_sell and threshold_buy → 持有，无信号

        return signals

    def get_last_prob(self) -> Optional[float]:
        """获取最近一次预测的概率（用于调试）"""
        return self.last_prob

    def get_last_features(self) -> Optional[pd.DataFrame]:
        """获取最近一次预测的特征向量（用于分析）"""
        return self.last_features
