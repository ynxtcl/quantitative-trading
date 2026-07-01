"""
订单数据类单元测试 — 测试 Order / Fill / Position dataclass 的行为
"""
import pytest
from datetime import datetime
from trading.order import (
    Order, OrderStatus, OrderType, OrderSide,
    Fill, Position, AccountInfo, OrderResult
)


class TestOrderStatusEnum:
    """测试订单状态枚举"""

    def test_active_statuses(self):
        """PENDING / SUBMITTED / PARTIAL_FILLED 为活跃状态"""
        for s in [OrderStatus.PENDING, OrderStatus.SUBMITTED, OrderStatus.PARTIAL_FILLED]:
            order = Order(
                symbol="000001", side=OrderSide.BUY, order_type=OrderType.MARKET,
                quantity=100, price=10.0, strategy="test", status=s
            )
            assert order.is_active
            assert not order.is_finalized

    def test_finalized_statuses(self):
        """FILLED / CANCELLED / REJECTED / TIMEOUT 为终结状态"""
        for s in [OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED, OrderStatus.TIMEOUT]:
            order = Order(
                symbol="000001", side=OrderSide.BUY, order_type=OrderType.MARKET,
                quantity=100, price=10.0, strategy="test", status=s
            )
            assert order.is_finalized
            assert not order.is_active


class TestOrderCreation:
    """测试 Order 创建默认值"""

    def test_default_order_id_is_empty(self):
        order = Order(symbol="000333", side=OrderSide.BUY, order_type=OrderType.LIMIT,
                      quantity=200, price=50.0, strategy="mr")
        assert order.order_id == ""
        assert order.status == OrderStatus.PENDING
        assert order.filled_quantity == 0
        assert order.filled_amount == 0.0

    def test_remaining_quantity_full(self):
        order = Order(symbol="000333", side=OrderSide.BUY, order_type=OrderType.MARKET,
                      quantity=300, price=20.0, strategy="test")
        assert order.remaining_quantity == 300

    def test_remaining_quantity_partial(self):
        order = Order(symbol="000333", side=OrderSide.BUY, order_type=OrderType.MARKET,
                      quantity=300, price=20.0, strategy="test",
                      status=OrderStatus.PARTIAL_FILLED, filled_quantity=100)
        assert order.remaining_quantity == 200

    def test_remaining_quantity_filled(self):
        order = Order(symbol="000333", side=OrderSide.BUY, order_type=OrderType.MARKET,
                      quantity=300, price=20.0, strategy="test",
                      status=OrderStatus.FILLED, filled_quantity=300)
        assert order.remaining_quantity == 0


class TestFill:
    """测试成交回报 dataclass"""

    def test_fill_creation(self):
        fill = Fill(
            order_id="MOCK000001",
            symbol="000001",
            side=OrderSide.BUY,
            price=10.0,
            quantity=100,
            amount=1000.0,
            commission=5.0,
            stamp_tax=0.0,
        )
        assert fill.order_id == "MOCK000001"
        assert fill.price == 10.0
        assert fill.amount == 1000.0

    def test_sell_fill_has_stamp_tax(self):
        fill = Fill(
            order_id="MOCK000002",
            symbol="000001",
            side=OrderSide.SELL,
            price=12.0,
            quantity=100,
            amount=1200.0,
            commission=5.0,
            stamp_tax=1.2,
        )
        assert fill.stamp_tax == 1.2
        assert fill.side == OrderSide.SELL


class TestPosition:
    """测试持仓 dataclass"""

    def test_position_profit_loss(self):
        pos = Position(
            symbol="000001", quantity=1000, avg_cost=10.0,
            current_price=12.0, market_value=12000.0,
            profit_loss=2000.0, profit_loss_pct=0.20
        )
        assert pos.quantity == 1000
        assert pos.avg_cost == 10.0
        assert pos.profit_loss_pct == 0.20
        assert pos.market_value == 12000.0

    def test_position_loss(self):
        pos = Position(
            symbol="000001", quantity=1000, avg_cost=10.0,
            current_price=8.0, market_value=8000.0,
            profit_loss=-2000.0, profit_loss_pct=-0.20
        )
        assert pos.profit_loss == -2000.0


class TestAccountInfo:
    """测试账户信息 dataclass"""

    def test_account_creation(self):
        acc = AccountInfo(
            total_assets=100000.0,
            cash=50000.0,
            frozen_cash=10000.0,
            market_value=40000.0,
            positions=[],
            daily_pnl=500.0,
            total_pnl=2000.0,
        )
        assert acc.total_assets == 100000.0
        assert acc.daily_pnl == 500.0


class TestOrderResult:
    """测试下单结果 dataclass"""

    def test_success_result(self):
        order = Order(symbol="000001", side=OrderSide.BUY, order_type=OrderType.MARKET,
                      quantity=100, price=10.0, strategy="test")
        result = OrderResult(success=True, order_id="MOCK001", message="已提交", order=order)
        assert result.success
        assert result.order is not None

    def test_failure_result(self):
        result = OrderResult(success=False, order_id="", message="资金不足")
        assert not result.success
        assert result.order is None
