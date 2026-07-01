"""
========================================
  风控系统 — 无状态过滤器
========================================

【设计原则】
风控系统不做状态维护——所有状态由引擎维护。
每次调用传入当前持仓情况，风控只做"过滤"。

【风控规则】
1. 单标的风控：任何一只股票的仓位不超过 max_single_weight（仅买入信号）
2. 总仓位风控：总仓位不超过 max_total_position
3. 止损风控：单笔最大亏损（信号层辅助过滤）
4. 多标的互斥：每天最多交易 N 只不同的股票
5. 组合回撤熔断：组合最大回撤超过阈值时禁止开新仓
6. 置信度排序：高置信度信号优先分配仓位
7. 广谱下跌过滤器（前置风控）：过半股票下跌时自动降仓
"""
from typing import Dict, List
from strategies.base import Signal


class RiskManager:
    """
    风险管理系统 — 无状态过滤器

    - 单标的上限：仅买入信号 weight 不超过 max_single_weight（卖出信号不受限）
    - 累计持仓上限：某只股票的现有仓位 + 新信号仓位不超过 max_single_weight
    - 总仓位上限：总持仓市值/总资产 不超过 max_total_position
    - 止损线：stop_loss（引擎层强制执行）
    - 每日最大亏损：max_daily_loss（参考值）
    - 组合回撤熔断：current_drawdown > max_drawdown 时返回空信号
    - C1 波动率自适应：高波动时自动降低仓位上限
    - C2 行业集中度：单行业总暴露不超过 max_industry_weight
    - 置信度排序：过滤前先按置信度降序排列，高置信度信号优先
    - 广谱下跌过滤器：market_median_return < threshold 时自动降仓
    """

    def __init__(self, config: dict = None):
        # 默认配置（所有风控参数的基准值）
        defaults = {
            "max_single_weight": 0.3,       # 单标的仓位上限 30%（仅买入）
            "max_total_position": 0.95,     # 总仓位上限 95%
            "stop_loss": 0.08,              # 止损线 8%（引擎层强制执行）
            "max_daily_loss": 0.03,         # 每日最大亏损 3%（参考值）
            "max_daily_symbols": 5,         # 每日最多交易 5 只不同股票
            "max_drawdown": 0.25,           # 组合最大回撤熔断 25%
            "vol_adaptive": True,           # C1: 波动率自适应开关
            "vol_low": 0.15,                # C1: 低波动基准（年化15%）
            "vol_high": 0.40,               # C1: 高波动基准（年化40%）
            "max_industry_weight": 0.50,     # C2: 单行业总暴露上限 50%
            "industry_map": {},              # C2: {symbol: industry_name}
        }
        if config:
            # 合并传入配置与默认值（传入优先级更高）
            merged = dict(defaults)
            merged.update(config)
            self.config = merged
        else:
            self.config = dict(defaults)

    def filter_signals(self,
                       signals: List[Signal],
                       current_position_ratio: float = 0,
                       current_positions: Dict[str, int] = None,
                       current_drawdown: float = 0.0,
                       current_position_ratios: Dict[str, float] = None,
                       annualized_volatility: float = 0.0,
                       market_median_return: float = 0.0) -> List[Signal]:
        """
        过滤风险信号

        风控流程：
        0. 硬熔断底线：current_drawdown > max_drawdown → 返回空
        0b. 渐进式回撤缩仓（progressive_drawdown=True）：回撤15%/25%/35%逐级降仓位
        0c. 广谱下跌过滤器（前置风控）：过半股票20日跌超阈值→自动降总仓位上限
        1. 波动率目标制（vol_target > 0）：年化波动率超过目标时自动缩仓
        2. 按置信度降序排列，高置信度优先执行
        3. 单标的风控（仅限买入信号）：signal.weight ≤ max_single_weight
        4. 每日符号数限制：最多交易 N 只不同股票
        5. 总仓位风控：当前仓位 + 信号仓位 ≤ max_total

        参数:
            signals: 待过滤的信号列表（已净权重求和后）
            current_position_ratio: 当前已用仓位比例 (0.0 ~ 1.0)
            current_positions: 当前各股票持仓量 {symbol: shares}
            current_drawdown: 当前组合回撤比例 (0.0 ~ 1.0)
            current_position_ratios: 当前各股票仓位占比 {symbol: ratio}（B3累计上限用）
            annualized_volatility: 当前年化波动率（C1波动率自适应用）
            market_median_return: (2026-07-01 v2) 所有持仓股票20日中位数回报率

        返回:
            过滤后的信号列表
        """
        if not signals:
            return []

        # ==== 规则 0: 硬熔断底线 ====
        max_dd = self.config.get("max_drawdown", 0.25)
        if current_drawdown > max_dd:
            return []  # 回撤超标 → 硬熔断

        # ==== 规则 0c: 广谱下跌过滤器（前置风控）====
        # 原理：当市场上过半股票都在下跌时，即使组合回撤还没触发熔断，
        # 也应该主动降低仓位。这是"前置"风控——不等回撤发生就行动。
        weakness_factor = 1.0
        if self.config.get("market_weakness_enabled", False):
            threshold = self.config.get("market_weakness_threshold", -0.05)
            severe = self.config.get("market_weakness_severe", -0.10)
            if market_median_return < severe:
                weakness_factor = 0.25  # 严重弱势：仅25%仓位
            elif market_median_return < threshold:
                weakness_factor = 0.50  # 普通弱势：仅50%仓位

        # ==== 规则 0b: 渐进式回撤缩仓 ====
        drawdown_factor = 1.0
        if self.config.get("progressive_drawdown", False):
            dd_stage1 = self.config.get("dd_stage1", 0.15)
            dd_stage2 = self.config.get("dd_stage2", 0.25)
            dd_stage3 = self.config.get("dd_stage3", 0.35)
            if current_drawdown > dd_stage3:
                drawdown_factor = 0.30  # 回撤>35%: 仅30%仓位
            elif current_drawdown > dd_stage2:
                drawdown_factor = 0.50  # 回撤>25%: 仅50%仓位
            elif current_drawdown > dd_stage1:
                drawdown_factor = 0.70  # 回撤>15%: 仅70%仓位

        max_symbols = self.config.get("max_daily_symbols", 5)
        max_single = self.config["max_single_weight"]
        max_total = self.config["max_total_position"]
        industry_map = self.config.get("industry_map", {})
        max_industry = self.config.get("max_industry_weight", 0.50)

        # ---- C1: 波动率自适应 ----
        vol_factor = 1.0
        if self.config.get("vol_adaptive", True) and annualized_volatility > 0:
            vol_low = self.config.get("vol_low", 0.15)
            vol_high = self.config.get("vol_high", 0.40)
            if annualized_volatility > vol_low:
                vol_factor = max(0.5, 1.0 - (annualized_volatility - vol_low) / (vol_high - vol_low) * 0.5)

        # ---- 波动率目标制 ----
        # 目标年化波动率 = vol_target
        # 当实际波动率 > 目标时，按比例缩仓
        # 例如：目标18%，实际36% → 仓位减半
        vol_target_factor = 1.0
        vol_target = self.config.get("vol_target", 0)
        if vol_target > 0 and annualized_volatility > vol_target:
            vol_target_factor = vol_target / annualized_volatility

        # 综合所有因子
        combined_factor = drawdown_factor * vol_factor * vol_target_factor * weakness_factor
        max_single *= combined_factor
        max_total *= combined_factor

        # 已有持仓的股票列表
        existing_positions = set()
        if current_positions:
            existing_positions = {
                sym for sym, shares in current_positions.items()
                if shares > 0
            }

        # ---- C2: 计算当前各行业总暴露 ----
        industry_exposure: Dict[str, float] = {}
        if current_position_ratios and industry_map:
            for sym, ratio in current_position_ratios.items():
                ind = industry_map.get(sym, "其他")
                industry_exposure[ind] = industry_exposure.get(ind, 0.0) + ratio

        # ==== 规则 2: 按置信度排序（高置信度优先） ====
        signals = sorted(signals, key=lambda s: s.confidence, reverse=True)

        filtered = []
        symbols_today = set()
        used_ratio = current_position_ratio

        for signal in signals:
            sym = signal.symbol

            # ---- 规则 3: 单标的风控（仅针对买入信号）----
            target_weight = signal.weight
            if signal.direction == 1:
                current_sym_ratio = current_position_ratios.get(sym, 0) if current_position_ratios else 0
                cumulative = current_sym_ratio + target_weight
                if cumulative > max_single:
                    target_weight = max(max_single - current_sym_ratio, 0)
                    if target_weight <= 0:
                        continue

                # ---- C2: 行业集中度检查 ----
                if industry_map:
                    ind = industry_map.get(sym, "其他")
                    current_ind_ratio = industry_exposure.get(ind, 0.0)
                    if current_ind_ratio + target_weight > max_industry:
                        target_weight = max(max_industry - current_ind_ratio, 0)
                        if target_weight <= 0:
                            continue

            # ---- 规则 4: 每日符号数限制 ----
            if sym not in existing_positions:
                if len(symbols_today) >= max_symbols:
                    continue
                symbols_today.add(sym)

            # ---- 规则 5: 总仓位上限（仅针对买入信号）----
            if signal.direction == 1:
                if used_ratio + target_weight > max_total:
                    target_weight = max_total - used_ratio
                    if target_weight <= 0:
                        continue
                used_ratio += target_weight

            # 更新信号权重
            signal.weight = round(target_weight, 4)
            filtered.append(signal)

        return filtered
