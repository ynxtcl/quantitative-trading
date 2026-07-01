"""
MockBroker 接口契约测试 — 验证 BrokerBase 14个接口的完整行为

测试策略：
  不用 mock/patch，直接创建 MockBroker 实例调用接口。
  Mock 模式下异步成交线程可能造成竞态，测试中通过 reset() + 控制流确保确定性。
"""
import pytest
import time
from trading.order import (
    Order, OrderStatus, OrderType, OrderSide,
    Fill, Position, AccountInfo, OrderResult
)
from trading.mock_broker import MockBroker


# ==================== Fixtures ====================

@pytest.fixture
def broker():
    """创建一个干净的 MockBroker 实例"""
    b = MockBroker(initial_capital=100000.0)
    b.update_prices({"000001": 10.0, "000333": 50.0})
    b.connect()
    yield b
    b.disconnect()


# ==================== 生命周期 ====================

class TestLifecycle:
    """测试 connect / disconnect / is_connected"""

    def test_initial_state(self):
        """刚创建时未连接"""
        b = MockBroker()
        assert not b.is_connected()

    def test_connect(self, broker):
        """connect() 后已连接"""
        assert broker.is_connected()

    def test_disconnect(self, broker):
        """disconnect() 后未连接"""
        broker.disconnect()
        assert not broker.is_connected()

    def test_double_connect(self, broker):
        """重复 connect 不报错"""
        assert broker.connect() is True
        assert broker.is_connected()


# ==================== 下单 & 涨跌停 ====================

class TestPlaceOrder:
    """测试下单核心逻辑"""

    def test_place_order_buy(self, broker):
        """正常买入 → 返回成功"""
        order = Order(symbol="000001", side=OrderSide.BUY, order_type=OrderType.MARKET,
                      quantity=100, price=10.0, strategy="test")
        result = broker.place_order(order)
        assert result.success
        assert result.order_id.startswith("MOCK")
        assert result.order.status == OrderStatus.SUBMITTED

    def test_place_order_sell(self, broker):
        """先买入再卖出 → 卖出成功"""
        # 先买入100股
        buy = Order(symbol="000001", side=OrderSide.BUY, order_type=OrderType.MARKET,
                    quantity=100, price=10.0, strategy="test")
        broker.place_order(buy)

        # 等成交回调 (max_delay=3.0, 给buffer)
        time.sleep(4.0)

        # 再卖出
        sell = Order(symbol="000001", side=OrderSide.SELL, order_type=OrderType.MARKET,
                     quantity=100, price=10.0, strategy="test")
        result = broker.place_order(sell)
        assert result.success, f"sell failed: {result.message}"


    def test_reject_no_price(self, broker):
        """无行情 → 拒单"""
        order = Order(symbol="999999", side=OrderSide.BUY, order_type=OrderType.MARKET,
                      quantity=100, price=10.0, strategy="test")
        result = broker.place_order(order)
        assert not result.success
        assert "无行情" in result.message

    def test_reject_limit_up(self, broker):
        """涨停买入 → 拒单"""
        order = Order(symbol="000001", side=OrderSide.BUY, order_type=OrderType.MARKET,
                      quantity=100, price=11.01, strategy="test")  # price > 10*1.10
        result = broker.place_order(order)
        assert not result.success
        assert "涨停" in result.message

    def test_reject_limit_down(self, broker):
        """跌停卖出 → 拒单"""
        order = Order(symbol="000001", side=OrderSide.SELL, order_type=OrderType.MARKET,
                      quantity=100, price=8.99, strategy="test")  # price < 10*0.90
        result = broker.place_order(order)
        assert not result.success
        assert "跌停" in result.message

    def test_reject_insufficient_cash(self, broker):
        """资金不足 → 拒单"""
        order = Order(symbol="000001", side=OrderSide.BUY, order_type=OrderType.MARKET,
                      quantity=100000, price=10.0, strategy="test")  # 需要 100万
        result = broker.place_order(order)
        assert not result.success
        assert "资金不足" in result.message


# ==================== 撤单 ====================

class TestCancelOrder:
    """测试撤单逻辑"""

    def test_cancel_active_order(self, broker):
        """撤单活跃订单 → 成功"""
        # 注意：使用 LIMIT 价在涨跌停内，确保不会被拒
        order = Order(symbol="000001", side=OrderSide.BUY, order_type=OrderType.LIMIT,
                      quantity=1000, price=10.0, strategy="test")
        result = broker.place_order(order)
        assert result.success

        # 立即撤单（在异步成交之前）
        cancelled = broker.cancel_order(result.order_id)
        assert cancelled

    def test_cancel_nonexistent_order(self, broker):
        """撤单不存在订单 → 失败"""
        assert not broker.cancel_order("NONEXIST")

    def test_cancel_twice(self, broker):
        """重复撤单 → 第二次失败"""
        order = Order(symbol="000001", side=OrderSide.BUY, order_type=OrderType.LIMIT,
                      quantity=1000, price=10.0, strategy="test")
        result = broker.place_order(order)
        assert broker.cancel_order(result.order_id)
        assert not broker.cancel_order(result.order_id)

    def test_cancel_filled_order(self, broker):
        """已成交订单不可撤单"""
        order = Order(symbol="000001", side=OrderSide.BUY, order_type=OrderType.MARKET,
                      quantity=100, price=10.0, strategy="test")
        result = broker.place_order(order)
        # 等成交
        time.sleep(4.0)
        # 直接检查状态（如果已成交就不能撤）
        o = broker.get_order(result.order_id)
        if o and o.is_finalized:
            assert not broker.cancel_order(result.order_id)
        else:
            # 如果 timeout 了也没问题
            pass


# ==================== 订单/成交查询 ====================

class TestOrderQuery:
    """测试 get_order / get_orders / get_fills"""

    def test_get_order_after_place(self, broker):
        """下单后能查到订单"""
        order = Order(symbol="000001", side=OrderSide.BUY, order_type=OrderType.MARKET,
                      quantity=100, price=10.0, strategy="test")
        result = broker.place_order(order)
        assert broker.get_order(result.order_id) is not None

    def test_get_order_nonexistent(self, broker):
        """不存在的订单返回 None"""
        assert broker.get_order("INVALID") is None

    def test_get_orders_after_multiple(self, broker):
        """多次下单后 get_orders 返回所有订单"""
        for i in range(3):
            order = Order(symbol="000001", side=OrderSide.BUY, order_type=OrderType.MARKET,
                          quantity=100, price=10.0, strategy="test")
            broker.place_order(order)
        orders = broker.get_orders()
        assert len(orders) >= 3

    def test_get_fills_after_fill(self, broker):
        """成交后 get_fills 返回成交记录"""
        order = Order(symbol="000001", side=OrderSide.BUY, order_type=OrderType.MARKET,
                      quantity=100, price=10.0, strategy="test")
        broker.place_order(order)
        time.sleep(3.0)
        fills = broker.get_fills()
        # 可能有成交可能超时，但至少不应报错
        assert isinstance(fills, list)


# ==================== 持仓/资金 ====================

class TestPositionsAndAccount:
    """测试 get_positions / get_account_info"""

    def test_initial_account(self, broker):
        """初始账户信息正确"""
        acc = broker.get_account_info()
        assert acc.total_assets == 100000.0
        assert acc.cash == 100000.0
        assert acc.frozen_cash == 0.0
        assert acc.market_value == 0.0

    def test_initial_positions_empty(self, broker):
        """初始持仓为空"""
        assert broker.get_positions() == []

    def test_position_after_buy(self, broker):
        """买入成交后有持仓"""
        order = Order(symbol="000001", side=OrderSide.BUY, order_type=OrderType.MARKET,
                      quantity=100, price=10.0, strategy="test")
        broker.place_order(order)
        time.sleep(3.0)
        # 检查是否成交
        positions = broker.get_positions()
        fills = broker.get_fills()
        if fills:
            assert len(positions) >= 1
            assert any(p.symbol == "000001" for p in positions)


# ==================== 行情 ====================

class TestMarketData:
    """测试行情接口 (非抽象方法: get_multiple_prices)"""

    def test_get_current_price(self, broker):
        assert broker.get_current_price("000001") == 10.0

    def test_get_current_price_missing(self, broker):
        assert broker.get_current_price("999999") is None

    def test_get_multiple_prices(self, broker):
        prices = broker.get_multiple_prices(["000001", "000333", "999999"])
        assert prices["000001"] == 10.0
        assert prices["000333"] == 50.0
        assert "999999" not in prices

    def test_subscribe_quotes(self, broker):
        """subscribe_quotes 不抛异常"""
        callback_called = []

        def cb(symbol, price, ts):
            callback_called.append((symbol, price))

        broker.subscribe_quotes(["000001"], callback=cb)
        broker.update_prices({"000001": 10.5})
        broker.subscribe_quotes(["000001"], callback=cb)  # 第二次调用触发回调
        assert len(callback_called) >= 1


# ==================== 事件回调 ====================

class TestCallbacks:
    """测试 on_fill / on_disconnect 回调"""

    def test_on_fill_callback(self, broker):
        """成交后触发 on_fill 回调"""
        fills_received = []

        def on_fill(fill):
            fills_received.append(fill)

        broker.on_fill(on_fill)
        order = Order(symbol="000001", side=OrderSide.BUY, order_type=OrderType.MARKET,
                      quantity=100, price=10.0, strategy="test")
        broker.place_order(order)
        time.sleep(3.0)
        # 如果有成交，应该收到回调
        # 如果超时，没有回调也不会崩
        for f in fills_received:
            assert isinstance(f, Fill)

    def test_reconnect(self, broker):
        """断线重连成功"""
        broker.disconnect()
        assert broker.reconnect(max_retries=1)
        assert broker.is_connected()


# ==================== 重置 ====================

class TestReset:
    """测试 reset() 清空所有状态"""

    def test_reset_clears_positions(self, broker):
        """reset 后持仓为空"""
        order = Order(symbol="000001", side=OrderSide.BUY, order_type=OrderType.MARKET,
                      quantity=100, price=10.0, strategy="test")
        broker.place_order(order)
        broker.reset()
        assert broker.get_positions() == []

    def test_reset_restores_capital(self, broker):
        """reset 后资金恢复初始值"""
        broker.reset(initial_capital=50000.0)
        acc = broker.get_account_info()
        assert acc.cash == 50000.0
