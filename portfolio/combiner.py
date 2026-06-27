"""
========================================
  组合器 — 多策略信号融合
  核心逻辑：净权重求和（冲突解决）
========================================

【为什么要做多策略组合？】
华尔街有一句名言："没有一个策略在所有市场环境下都有效。"

趋势跟踪策略在牛市中表现优异，但在震荡市中反复亏损。
均值回归策略在震荡市中表现优异，但在牛市中踏空。
因子选股策略在结构性行情中有效，但在普涨/普跌中失效。

多策略组合的核心思想：通过低相关性的策略分散风险。

【净权重求和（冲突解决）】
当多个策略对同一只股票同时出信号时：
  趋势跟踪 买入 weight=0.40
+ 均值回归 卖出 weight=0.15
= 净买入 0.25

逻辑解释：
- 两个策略出现方向分歧 → 市场方向不确定 → 小仓位试探
- 两个策略方向一致 → 高度共识 → 大胆执行
- 净权重=0 → 多空力量平衡 → 不操作
"""
from typing import Dict, List
from strategies.base import Signal


class PortfolioCombiner:
    """
    多策略信号组合器 — 净权重求和

    职责：
    1. 接收多个策略的信号
    2. 按策略权重调整信号
    3. 对同一只股票按净权重求和（冲突解决）
    4. 输出最终的净信号列表
    """

    def __init__(self):
        self.strategy_weights: Dict[str, float] = {}

    def set_weights(self, weights: dict):
        """
        设置各策略在组合中的权重
        权重会被归一化（保证总和=1）
        """
        total = sum(weights.values())
        self.strategy_weights = {
            k: v / total for k, v in weights.items()
        }

    def combine(self, strategies_signals: Dict[str, List[Signal]]) -> List[Signal]:
        """
        合并多策略信号 — 净权重求和

        处理流程：
        1. 遍历每个策略的信号，按策略权重调整 signal.weight
        2. 按股票代码分组：同一只股票的所有信号归为一组
        3. 对每组做净权重求和：
           Σ(买入weight × 策略权重) - Σ(卖出weight × 策略权重)
        4. 净权重>0 → 买入；净权重<0 → 卖出；净权重=0 → 丢弃
        5. 返回最终的净信号列表

        参数:
            strategies_signals: {strategy_name: [Signal, ...]}

        返回:
            净权重求和后的信号列表
        """
        # Step 1: 调整权重并按股票分组
        by_symbol: Dict[str, Dict[str, List[Signal]]] = {}
        # by_symbol = {symbol: {strategy_name: [signals]}}

        for strategy_name, signals in strategies_signals.items():
            weight = self.strategy_weights.get(strategy_name, 1.0)
            for signal in signals:
                sym = signal.symbol
                # 按策略权重调整信号仓位
                signal.weight *= weight

                if sym not in by_symbol:
                    by_symbol[sym] = {}
                if strategy_name not in by_symbol[sym]:
                    by_symbol[sym][strategy_name] = []
                by_symbol[sym][strategy_name].append(signal)

        # Step 2: 对每只股票做净权重求和
        final_signals: List[Signal] = []

        for symbol, strategy_dict in by_symbol.items():
            # 计算该股票的总净权重
            net_weight = 0.0
            total_confidence = 0.0
            n_signals = 0
            source_strategies = []

            for strategy_name, sigs in strategy_dict.items():
                for sig in sigs:
                    if sig.direction == 1:       # 买入 → 正权重
                        net_weight += sig.weight
                    elif sig.direction == -1:    # 卖出 → 负权重
                        net_weight -= sig.weight
                    total_confidence += sig.confidence
                    n_signals += 1
                    source_strategies.append(strategy_name)

            # 净权重为0 → 无信号
            if abs(net_weight) < 1e-8:
                continue

            # 置信度 = 所有信号置信度的平均值
            avg_confidence = total_confidence / n_signals if n_signals > 0 else 0.5

            # 确定方向
            direction = 1 if net_weight > 0 else -1
            weight = abs(net_weight)

            # 构建最终信号（取第一个信号的价格和时间）
            first_sig = None
            for sigs in strategy_dict.values():
                if sigs:
                    first_sig = sigs[0]
                    break

            if first_sig is None:
                continue

            final_signals.append(Signal(
                symbol=symbol,
                direction=direction,
                weight=min(weight, 1.0),       # 截断到[0,1]
                price=first_sig.price,
                confidence=round(avg_confidence, 2),
                strategy='portfolio',           # 标记为组合信号
                timestamp=first_sig.timestamp,
            ))

        # Step 3: 按置信度降序排列
        final_signals.sort(key=lambda s: s.confidence, reverse=True)

        return final_signals
