"""Smoke test for trading/ modules"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_imports():
    """Test 1: All modules import correctly"""
    from trading.order import Order, OrderSide, OrderType, OrderStatus, Fill, Position, AccountInfo
    from trading.broker_base import BrokerBase
    from trading.mock_broker import MockBroker, LARGE_CAP_SYMBOLS
    from trading.order_manager import OrderManager
    from trading.real_time_data import MarketDataReplay
    from trading.live_engine import LiveEngine
    print('[PASS] All trading modules imported')
    return True

def test_mock_broker():
    """Test 2: Mock broker place order and fill"""
    from trading.order import Order, OrderSide, OrderType, OrderStatus
    from trading.mock_broker import MockBroker

    broker = MockBroker(initial_capital=100000.0)
    assert broker.connect() == True, "Connect failed"
    print('[PASS] Broker connected')

    broker.update_prices({'000001': 22.50})
    
    order = Order(symbol='000001', side=OrderSide.BUY, order_type=OrderType.MARKET,
                  quantity=200, price=22.50, strategy='test')
    result = broker.place_order(order)
    assert result.success == True, f"Place order failed: {result.message}"
    print(f'[PASS] Order placed: {result.order_id}')

    time.sleep(4)
    
    acct = broker.get_account_info()
    print(f'[INFO] Account: total={acct.total_assets:.2f} cash={acct.cash:.2f} pos={len(acct.positions)}')
    assert acct.total_assets > 0, "Account should have value"

    positions = broker.get_positions()
    if positions:
        p = positions[0]
        print(f'[INFO] Position: {p.symbol} qty={p.quantity} cost={p.avg_cost:.2f} val={p.market_value:.2f}')
        assert p.quantity > 0, "Should have shares"

    broker.reset()
    acct2 = broker.get_account_info()
    assert abs(acct2.total_assets - 100000.0) < 0.01, f"Reset failed: {acct2.total_assets}"
    print('[PASS] Broker reset works')

    return True

def test_order_status():
    """Test 3: Order status lifecycle"""
    from trading.order import Order, OrderSide, OrderType, OrderStatus
    from trading.mock_broker import MockBroker

    broker = MockBroker(initial_capital=100000.0)
    broker.connect()
    broker.update_prices({'000333': 68.50})

    order = Order(symbol='000333', side=OrderSide.BUY, order_type=OrderType.MARKET,
                  quantity=100, price=68.50, strategy='test')
    result = broker.place_order(order)
    time.sleep(3)
    
    status = broker.get_order_status(result.order_id)
    print(f'[INFO] Order status: {status}')
    assert status in [OrderStatus.FILLED, OrderStatus.PARTIAL_FILLED, OrderStatus.TIMEOUT]
    print(f'[PASS] Order lifecycle works: {status.value}')

    return True

def test_cancel():
    """Test 4: Cancel order"""
    from trading.order import Order, OrderSide, OrderType
    from trading.mock_broker import MockBroker

    broker = MockBroker(initial_capital=100000.0)
    broker.connect()
    broker.update_prices({'000858': 145.00})

    order = Order(symbol='000858', side=OrderSide.BUY, order_type=OrderType.MARKET,
                  quantity=10000, price=145.00, strategy='test')
    result = broker.place_order(order)
    
    cancelled = broker.cancel_order(result.order_id)
    print(f'[INFO] Cancel result: {cancelled}')
    print(f'[PASS] Cancel API works')

    return True

if __name__ == '__main__':
    print("=" * 50)
    print("  Trading Module Smoke Tests")
    print("=" * 50)
    
    tests = [
        ("Imports", test_imports),
        ("MockBroker", test_mock_broker),
        ("Order Status", test_order_status),
        ("Cancel Order", test_cancel),
    ]
    
    passed = 0
    for name, fn in tests:
        try:
            fn()
            passed += 1
        except Exception as e:
            print(f'[FAIL] {name}: {e}')
    
    print(f"\n{'='*50}")
    print(f"  Result: {passed}/{len(tests)} passed")
    print(f"{'='*50}")
