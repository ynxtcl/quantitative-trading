"""
========================================
  风控模块单元测试 — pytest
========================================

测试覆盖：
- A1: 置信度排序
- A2: 卖出信号不受限
- A3: 回撤熔断
- B1: 累计持仓上限（已在 risk_manager 层测试）
- B2: 参数外部化（配置传入）
- B3: 总仓位上限
- C1: 波动率自适应
- C2: 行业集中度

运行：
    cd quantitative_trading
    python -m pytest tests/test_risk_manager.py -v
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from typing import Dict, List
from strategies.base import Signal
from portfolio.risk_manager import RiskManager
import pandas as pd


def make_signal(symbol: str, direction: int, weight: float,
                confidence: float = 0.5) -> Signal:
    """Helper to create test signals"""
    return Signal(
        symbol=symbol,
        direction=direction,
        weight=weight,
        price=10.0,
        confidence=confidence,
        strategy='test',
        timestamp=pd.Timestamp('2024-01-01'),
    )


# ====================================================================
# A1: 置信度排序
# ====================================================================

def test_confidence_sorting():
    """高置信度信号应该优先执行（总仓位受限时保留高置信度）"""
    rm = RiskManager()
    sigs = [
        make_signal('000001', 1, 0.6, confidence=0.3),   # 低置信度
        make_signal('000333', 1, 0.6, confidence=0.9),   # 高置信度
    ]
    # 总仓位限制 0.95，两个 0.6 加起来 1.2 > 0.95
    result = rm.filter_signals(sigs, current_position_ratio=0.0, current_positions={})
    # 第一个应该被分配 > 仓位，第二个被截断
    assert len(result) >= 1, 'should keep at least 1 signal'
    # 高置信度应该在结果中（因为排序了）
    assert result[0].confidence == 0.9, 'high confidence signal should be first'
    # 如果只有一个信号通过了，那应该是高置信度的
    passed_syms = [s.symbol for s in result]
    if len(result) == 1:
        assert '000333' in passed_syms, 'high confidence should pass'


def test_confidence_sorting_rejects_low_first():
    """总仓位耗尽时，低置信度信号被拒绝"""
    rm = RiskManager()
    sigs = [
        make_signal('000001', 1, 0.7, confidence=0.3),
        make_signal('000333', 1, 0.7, confidence=0.9),
        make_signal('000858', 1, 0.7, confidence=0.6),
    ]
    result = rm.filter_signals(sigs, current_position_ratio=0.0, current_positions={})
    # 总仓位 95%，只能放 1 个 0.7 + 部分 0.25
    assert len(result) >= 1, 'should keep at least 1'
    # 高置信度优先
    assert result[0].symbol == '000333', 'highest confidence first'
    total_w = sum(s.weight for s in result)
    assert total_w <= 0.95 + 1e-6, 'total cannot exceed 95%'


# ====================================================================
# A2: 卖出信号不受单标的上限限制
# ====================================================================

def test_sell_signal_bypasses_single_cap():
    """卖出信号(direction=-1)不应该受单标的上限限制"""
    rm = RiskManager()
    sig = make_signal('000001', -1, 1.0)  # 全部卖出
    result = rm.filter_signals([sig], current_position_ratio=0.3, current_positions={'000001': 100})
    assert len(result) == 1, 'sell signal should pass'
    # 卖出信号不受单标的上限限制，也不受总仓位上限限制
    assert result[0].weight == 1.0, f'sell weight should be 1.0 (not capped): {result[0].weight}'


# ====================================================================
# A3: 回撤熔断
# ====================================================================

def test_drawdown_circuit_breaker():
    """回撤超过 max_drawdown 时返回空信号"""
    rm = RiskManager({'max_drawdown': 0.25, 'max_single_weight': 0.3, 'max_total_position': 0.95, 'max_daily_symbols': 10, 'stop_loss': 0.08, 'max_daily_loss': 0.03})
    sig = make_signal('000001', 1, 0.5)
    result = rm.filter_signals([sig], current_drawdown=0.30)  # 30% > 25%
    assert len(result) == 0, 'should return empty during circuit breaker'


def test_drawdown_normal_operation():
    """回撤低于阈值时正常执行"""
    rm = RiskManager({'max_drawdown': 0.25, 'max_single_weight': 0.3, 'max_total_position': 0.95, 'max_daily_symbols': 10, 'stop_loss': 0.08, 'max_daily_loss': 0.03})
    sig = make_signal('000001', 1, 0.2)
    result = rm.filter_signals([sig], current_drawdown=0.10)  # 10% < 25%
    assert len(result) == 1, 'should pass normally'


# ====================================================================
# B2: 参数外部化
# ====================================================================

def test_custom_config():
    """传入自定义配置"""
    custom = {
        'max_single_weight': 0.2,
        'max_total_position': 0.8,
        'max_daily_symbols': 3,
        'stop_loss': 0.05,
        'max_daily_loss': 0.02,
        'max_drawdown': 0.15,
    }
    rm = RiskManager(dict(custom))
    assert rm.config['max_single_weight'] == 0.2
    assert rm.config['max_total_position'] == 0.8
    # 测试更大信号被截断
    sig = make_signal('000001', 1, 0.5)
    result = rm.filter_signals([sig], current_position_ratio=0.0)
    assert result[0].weight <= 0.2, 'should cap at custom max_single'


# ====================================================================
# B3: 累计持仓上限
# ====================================================================

def test_cumulative_position_cap():
    """现有仓位 + 新信号不超过 max_single_weight"""
    rm = RiskManager()
    sig = make_signal('000001', 1, 0.5)
    position_ratios = {'000001': 0.20}  # 已有 20%
    result = rm.filter_signals(
        [sig],
        current_position_ratio=0.2,
        current_positions={'000001': 100},
        current_position_ratios=position_ratios,
    )
    assert len(result) == 1, 'should pass'
    # 20% + 新信号 -> 不超过 30%，所以最多 10%
    assert result[0].weight <= 0.10 + 1e-6, f'cumulative cap violated: {result[0].weight} > 0.10'


def test_cumulative_position_exceeded():
    """如果已经达到上限，买入信号被拒绝"""
    rm = RiskManager()
    sig = make_signal('000001', 1, 0.2)
    position_ratios = {'000001': 0.30}  # 已经 30%
    result = rm.filter_signals(
        [sig],
        current_position_ratio=0.3,
        current_positions={'000001': 100},
        current_position_ratios=position_ratios,
    )
    assert len(result) == 0, 'should reject when already at cap'


# ====================================================================
# 规则 3: 每日符号数限制
# ====================================================================

def test_daily_symbol_limit():
    """每日新开仓不超过 max_daily_symbols"""
    rm = RiskManager()
    sigs = [
        make_signal('000001', 1, 0.2),
        make_signal('000333', 1, 0.2),
        make_signal('000858', 1, 0.2),
        make_signal('002415', 1, 0.2),
        make_signal('300750', 1, 0.2),
        make_signal('600000', 1, 0.2),  # 第6只 -> 应该被拒绝
    ]
    result = rm.filter_signals(sigs, current_position_ratio=0.0, current_positions={})
    assert len(result) <= 5, 'at most 5 new symbols per day'
    symbols = set(s.symbol for s in result)
    assert '600000' not in symbols, '6th symbol should be rejected'


# ====================================================================
# 规则 4: 总仓位上限
# ====================================================================

def test_total_position_limit():
    """总仓位不超过 max_total_position"""
    rm = RiskManager()
    sigs = [
        make_signal('000001', 1, 0.6),
        make_signal('000333', 1, 0.6),
    ]
    result = rm.filter_signals(sigs, current_position_ratio=0.0, current_positions={})
    total_w = sum(s.weight for s in result)
    assert total_w <= 0.95 + 1e-6, f'total weight {total_w} exceeds 0.95'


def test_total_position_already_full():
    """已有 80% 仓位时，新信号被拒绝"""
    rm = RiskManager()
    sig = make_signal('000001', 1, 0.3)
    result = rm.filter_signals([sig], current_position_ratio=0.80, current_positions={})
    # 80% + 30% > 95%, 剩余 15%
    assert result[0].weight <= 0.15 + 1e-6, 'should cap at remaining capacity'


# ====================================================================
# C1: 波动率自适应
# ====================================================================

def test_volatility_adaptive_reduces_max():
    """高波动时仓位上限降低"""
    rm = RiskManager({
        'max_single_weight': 0.3,
        'max_total_position': 0.95,
        'max_daily_symbols': 10,
        'stop_loss': 0.08,
        'max_daily_loss': 0.03,
        'max_drawdown': 0.25,
        'vol_adaptive': True,
        'vol_low': 0.15,
        'vol_high': 0.40,
        'max_industry_weight': 0.50,
        'industry_map': {},
    })
    sig = make_signal('000001', 1, 0.3)
    # 高波动 60% 年化 -> factor ~0.5 -> max_single ~0.15
    result = rm.filter_signals([sig], annualized_volatility=0.60)
    assert result[0].weight <= 0.15 + 1e-5, f'vol adaptive should reduce: {result[0].weight} > 0.15'


def test_volatility_adaptive_low_vol():
    """低波动时仓位上限不变"""
    rm = RiskManager({
        'max_single_weight': 0.3,
        'max_total_position': 0.95,
        'max_daily_symbols': 10,
        'stop_loss': 0.08,
        'max_daily_loss': 0.03,
        'max_drawdown': 0.25,
        'vol_adaptive': True,
        'vol_low': 0.15,
        'vol_high': 0.40,
        'max_industry_weight': 0.50,
        'industry_map': {},
    })
    sig = make_signal('000001', 1, 0.3)
    # 低波动 10% 年化 -> factor 1.0 -> max_single 不变
    result = rm.filter_signals([sig], annualized_volatility=0.10)
    assert result[0].weight == 0.3, 'low vol should not reduce'


# ====================================================================
# C2: 行业集中度
# ====================================================================

def test_industry_concentration():
    """单行业总暴露不超过 max_industry_weight"""
    industry_map = {'000001': '银行', '000333': '家电'}
    rm = RiskManager({
        'max_single_weight': 0.3,
        'max_total_position': 0.95,
        'max_daily_symbols': 10,
        'stop_loss': 0.08,
        'max_daily_loss': 0.03,
        'max_drawdown': 0.25,
        'vol_adaptive': False,
        'max_industry_weight': 0.50,
        'industry_map': industry_map,
    })
    # 已有银行 15%，再加 40% -> 超过 50%，但单标的不超 30%
    # 所以行业集中度是约束条件，不是单标的上限
    sig = make_signal('000001', 1, 0.40)  # 银行
    pos_ratios = {'000001': 0.15, '000333': 0.10}
    result = rm.filter_signals(
        [sig],
        current_position_ratio=0.25,
        current_positions={'000001': 100, '000333': 50},
        current_position_ratios=pos_ratios,
    )
    assert len(result) == 1, 'should pass but capped by industry'
    # 银行已有 15%，max_industry=50%，最多加 35%
    # 但单标的上限 max_single=30%，累计 15%+30%=45%
    # 行业: 15%+40%=55%>50%，被行业截断到 max(50%-15%,0)=35%
    # 但 single cap: 15%+40%>30%，被单标截断到 max(30%-15%,0)=15%
    # 所以最终 weight = min(35%, 15%) = 15%
    assert abs(result[0].weight - 0.15) < 1e-4, f'industry/single cap: {result[0].weight}'


def test_industry_concentration_reject():
    """行业已满时买入被拒绝"""
    industry_map = {'000001': '银行'}
    rm = RiskManager({
        'max_single_weight': 0.3,
        'max_total_position': 0.95,
        'max_daily_symbols': 10,
        'stop_loss': 0.08,
        'max_daily_loss': 0.03,
        'max_drawdown': 0.25,
        'vol_adaptive': False,
        'max_industry_weight': 0.50,
        'industry_map': industry_map,
    })
    sig = make_signal('000001', 1, 0.1)
    pos_ratios = {'000001': 0.50}  # 银行已达 50%
    result = rm.filter_signals(
        [sig],
        current_position_ratio=0.5,
        current_positions={'000001': 100},
        current_position_ratios=pos_ratios,
    )
    assert len(result) == 0, 'should reject when industry already at cap'


# ====================================================================
# 边缘情况
# ====================================================================

def test_empty_signals():
    """空信号列表应返回空"""
    rm = RiskManager()
    result = rm.filter_signals([])
    assert result == []


def test_no_config_provided():
    """不传 config 应使用默认值"""
    rm = RiskManager()
    assert rm.config['max_single_weight'] == 0.3
    assert rm.config['max_total_position'] == 0.95
    assert rm.config['max_drawdown'] == 0.25


def test_sell_signals_not_counted_for_daily_limit():
    """卖出信号不应计入每日新开仓限制"""
    rm = RiskManager({'max_single_weight': 0.3, 'max_total_position': 0.95, 'max_daily_symbols': 1, 'stop_loss': 0.08, 'max_daily_loss': 0.03, 'max_drawdown': 0.25})
    sigs = [
        make_signal('000001', -1, 1.0),  # 卖出 - 不占每日额度
        make_signal('000333', 1, 0.2),   # 买入 - 应通过
    ]
    result = rm.filter_signals(sigs, current_position_ratio=0.3, current_positions={'000001': 100})
    # 卖出信号不消耗仓位容量，买入信号可以正常通过
    assert len(result) == 2, f'both should pass: {len(result)}'
    # 卖出权重保持 1.0
    sell = [s for s in result if s.direction == -1]
    assert len(sell) == 1, 'sell should be present'
    assert sell[0].weight == 1.0, 'sell weight should be 1.0'
