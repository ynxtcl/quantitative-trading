"""
========================================
  订单/持仓/成交 数据类
========================================

【订单状态机】
PENDING → SUBMITTED → PARTIAL_FILLED → FILLED
                            ↘ CANCELLED
                            ↘ REJECTED
         → TIMEOUT → CANCELLED

【设计原则】
1. 所有数据类都是纯数据容器，不含业务逻辑
2. 使用 Python dataclass，零外部依赖
3. 状态变更由 OrderManager 负责
"""
from dataclasses import dataclass, field
from typing import Optional, List
from datetime import datetime, timezone
from enum import Enum


class OrderStatus(Enum):
    """订单状态枚举"""
    PENDING = "pending"             # 待提交
    SUBMITTED = "submitted"         # 已提交（券商已接收）
    PARTIAL_FILLED = "partial"      # 部分成交
    FILLED = "filled"               # 完全成交
    CANCELLED = "cancelled"         # 已撤单
    REJECTED = "rejected"           # 被券商拒绝
    TIMEOUT = "timeout"             # 超时未成交


class OrderType(Enum):
    """订单类型"""
    MARKET = "market"               # 市价单
    LIMIT = "limit"                 # 限价单


class OrderSide(Enum):
    """订单方向"""
    BUY = 1
    SELL = -1


@dataclass
class Order:
    """订单 — 交易的最小单元"""
    symbol: str                     # 股票代码
    side: OrderSide                 # 买卖方向
    order_type: OrderType           # 订单类型
    quantity: int                   # 委托数量（股）
    price: float                    # 委托价格（市价单用当前价）
    strategy: str                   # 来源策略名
    order_id: str = ""              # 订单ID（系统生成）
    status: OrderStatus = OrderStatus.PENDING
    filled_quantity: int = 0        # 已成交数量
    filled_amount: float = 0.0      # 已成交金额
    avg_fill_price: float = 0.0     # 平均成交价
    created_at: datetime = field(default_factory=lambda: datetime.now())
    submitted_at: Optional[datetime] = None
    filled_at: Optional[datetime] = None
    cancel_reason: str = ""
    reject_reason: str = ""
    parent_order_id: str = ""       # 母订单ID（拆单用）

    @property
    def is_active(self) -> bool:
        """订单是否仍在处理中"""
        return self.status in [
            OrderStatus.PENDING,
            OrderStatus.SUBMITTED,
            OrderStatus.PARTIAL_FILLED,
        ]

    @property
    def is_finalized(self) -> bool:
        """订单是否已终结"""
        return self.status in [
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.REJECTED,
            OrderStatus.TIMEOUT,
        ]

    @property
    def remaining_quantity(self) -> int:
        """剩余未成交数量"""
        return self.quantity - self.filled_quantity


@dataclass
class Fill:
    """成交回报 — 一次成交的详细信息"""
    order_id: str
    symbol: str
    side: OrderSide
    price: float                    # 成交价
    quantity: int                   # 成交量
    amount: float                   # 成交金额
    commission: float               # 佣金
    stamp_tax: float                # 印花税（仅卖出）
    timestamp: datetime = field(default_factory=lambda: datetime.now())


@dataclass
class Position:
    """持仓信息"""
    symbol: str
    quantity: int                   # 持仓数量（股）
    avg_cost: float                 # 平均成本价
    current_price: float            # 当前市价
    market_value: float             # 持仓市值
    profit_loss: float              # 浮动盈亏
    profit_loss_pct: float          # 浮动盈亏百分比
    weight: float = 0.0             # 占组合比例


@dataclass
class AccountInfo:
    """账户信息"""
    total_assets: float              # 总资产
    cash: float                      # 可用资金
    frozen_cash: float               # 冻结资金
    market_value: float              # 持仓市值
    positions: List[Position] = field(default_factory=list)
    daily_pnl: float = 0.0           # 当日盈亏
    total_pnl: float = 0.0           # 累计盈亏


@dataclass
class OrderResult:
    """下单结果"""
    success: bool
    order_id: str
    message: str
    order: Optional[Order] = None
