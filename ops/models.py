"""
========================================
  Ops ORM 模型 — 数据库表结构定义
========================================

【设计原则】
1. 使用 Python 标准库 sqlite3 + dataclass，零外部依赖
2. 每个 dataclass 对应一张数据库表，字段名与代码中一致
3. 时间戳统一使用 ISO 格式字符串（UTC+8），便于跨平台

【表关系】
  run_logs 1→N trades
  run_logs 1→N daily_metrics
  run_logs 1→N risk_events
"""

from dataclasses import dataclass, asdict, field
from typing import Optional, Dict, Any, List


@dataclass
class RunLog:
    """策略运行日志 — 每次回测/验证/组合运行的主记录"""
    run_id: str                    # 形如 R001
    timestamp: str                 # ISO 格式，如 2026-06-29T15:30:00
    run_type: str                  # 'wf_single' / 'portfolio' / 'phase4_xgboost' / 'scheduled'
    description: str               # 运行描述
    status: str                    # 'success' / 'failed' / 'partial'
    symbols: str                   # 逗号分隔的股票代码
    config_snapshot: str           # JSON 快照（关键配置参数）
    error_message: str = ""        # 失败时的错误信息
    duration_seconds: float = 0.0  # 运行耗时


@dataclass
class MetricsRecord:
    """绩效指标 — 每次运行的量化结果"""
    run_id: str
    total_return: float
    annual_return: float
    sharpe_ratio: float
    max_drawdown: float
    volatility: float
    win_rate: float
    profit_factor: float
    calmar_ratio: float
    final_value: float
    total_trades: int
    total_days: int


@dataclass
class TradeRecord:
    """交易记录 — 单笔成交信息"""
    run_id: str
    symbol: str
    date: str
    direction: int                # 1=买入, -1=卖出
    price: float
    quantity: int
    value: float
    cost: float
    strategy: str                 # 'trend_following' / 'mean_reversion' / 'factor_selection'
    pnl: float = 0.0


@dataclass
class DailyMetricRecord:
    """每日净值记录 — 用于回撤/夏普分析"""
    run_id: str
    date: str
    total_value: float
    capital: float
    positions_count: int
    drawdown: float = 0.0


@dataclass
class RiskEventRecord:
    """风控事件记录 — 每次风控触发详情"""
    run_id: str
    date: str
    rule_name: str                # 'stop_loss' / 'drawdown_breaker' / 'single_weight_clip' / 等
    symbol: str
    detail: str                   # JSON 描述触发原因
    severity: str = 'warning'     # 'info' / 'warning' / 'critical'


@dataclass
class WalkForwardRecord:
    """Walk-Forward 单轮验证记录"""
    run_id: str
    symbol: str
    fold_id: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    train_sharpe: float
    test_sharpe: float
    oos_ratio: float
    train_return: float = 0.0
    test_return: float = 0.0
    train_trades: int = 0
    test_trades: int = 0
    ml_won: bool = False         # Phase 4 专用


@dataclass
class ConfigSnapshot:
    """配置快照 — 用于追踪每次运行的配置变化"""
    run_id: str
    config_type: str              # 'backtest' / 'risk' / 'strategy_tf' / 'strategy_mr' / 'strategy_fs'
    config_json: str
    version_hash: str             # git commit hash 或手动版本号


# 表名映射：dataclass → 数据库表
TABLE_MAP: Dict[str, str] = {
    'RunLog': 'run_logs',
    'MetricsRecord': 'metrics',
    'TradeRecord': 'trades',
    'DailyMetricRecord': 'daily_metrics',
    'RiskEventRecord': 'risk_events',
    'WalkForwardRecord': 'walk_forward_folds',
    'ConfigSnapshot': 'config_snapshots',
}

# 所有模型的创建语句（由 migrations.py 使用）
ALL_MODELS = [
    RunLog, MetricsRecord, TradeRecord,
    DailyMetricRecord, RiskEventRecord,
    WalkForwardRecord, ConfigSnapshot,
]
