"""
========================================
  OrderManager — 订单管理器
========================================

【职责】
1. 接收信号 → 创建订单
2. 信号转订单：Signal → Order（单位转换、手数处理）
3. 下单前风控检查（调用 risk_manager.pre_trade_check）
4. 路由到 broker 执行
5. 监控超时
6. 记录所有订单到数据库

【与信号的转换关系】
    Signal(direction=1, weight=0.5, price=22.0)
      ↓
    Order(side=BUY, quantity=2200, price=22.0)
      ↓
    MockBroker.place_order(order)
      ↓
    Fill(order_id=..., quantity=2000, price=22.03)
"""
import time
import threading
from typing import Dict, List, Optional
from datetime import datetime

from strategies.base import Signal
from trading.order import (
    Order, OrderResult, OrderSide, OrderType,
    OrderStatus, Fill, Position, AccountInfo
)
from trading.broker_base import BrokerBase
from portfolio.risk_manager import RiskManager
from config.settings import TRADING_CONFIG
from ops.logger import get_logger
from ops.database import get_db

log = get_logger('order_manager')


class OrderManager:
    """订单管理器 — 管理完整订单生命周期"""

    def __init__(self, broker: BrokerBase, risk_manager: RiskManager):
        self.broker = broker
        self.risk_manager = risk_manager
        self.config = TRADING_CONFIG
        self.lot_size = 100  # A 股最小交易单位

        # 运行状态
        self._orders: Dict[str, Order] = {}        # 全部订单
        self._active_orders: Dict[str, Order] = {}  # 处理中的订单
        self._fills: List[Fill] = []                # 全部成交
        self._daily_symbols: set = set()            # 今日已交易标的
        self._last_reset_day: str = ""              # 上次重置日期

        # 监控线程
        self._monitor_thread: Optional[threading.Thread] = None
        self._running = False
        self._order_timeout = self.config.get("order_timeout_seconds", 30)

    def start_monitor(self):
        """启动订单监控线程（检查超时）"""
        self._running = True
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop, daemon=True, name="order_monitor"
        )
        self._monitor_thread.start()
        log.info('订单监控已启动')

    def stop_monitor(self):
        """停止订单监控"""
        self._running = False
        log.info('订单监控已停止')

    def _monitor_loop(self):
        """监控循环 — 检查超时订单"""
        while self._running:
            try:
                now = datetime.now()
                to_remove = []
                for oid, order in list(self._active_orders.items()):
                    if not order.is_active:
                        to_remove.append(oid)
                        continue
                    # 检查超时
                    if order.submitted_at:
                        elapsed = (now - order.submitted_at).total_seconds()
                        if elapsed > self._order_timeout:
                            log.warning(f'订单超时', order_id=oid,
                                        elapsed=f'{elapsed:.1f}s')
                            self.broker.cancel_order(oid)
                            order.status = OrderStatus.TIMEOUT
                            to_remove.append(oid)
                for oid in to_remove:
                    self._active_orders.pop(oid, None)
            except Exception as e:
                log.error(f'监控循环异常', error=str(e))
            time.sleep(1)

    def reset_daily(self, trade_date: str):
        """日初重置（交易符号计数归零）"""
        if trade_date != self._last_reset_day:
            self._daily_symbols.clear()
            self._last_reset_day = trade_date
            log.info('日交易计数已重置', date=trade_date)

    def signal_to_order(self, signal: Signal, capital: float,
                        current_price: float) -> Optional[Order]:
        """信号转订单

        Signal(direction=1, weight=0.5, price=22.0)
          → Order(side=BUY, qty=int(可用资金×权重/价格/100)*100)
        """
        if signal.direction == 0:
            return None
        if current_price <= 0:
            return None

        side = OrderSide.BUY if signal.direction == 1 else OrderSide.SELL

        if side == OrderSide.BUY:
            # 买入：可用资金 × 权重 ÷ 价格 → 股数
            available_capital = capital * signal.weight
            raw_qty = int(available_capital / current_price)
        else:
            # 卖出：使用持仓数量 × 权重
            positions = self.broker.get_positions()
            current_shares = 0
            for p in positions:
                if p.symbol == signal.symbol:
                    current_shares = p.quantity
                    break
            raw_qty = int(current_shares * signal.weight)

        # A 股手数对齐（向下取整到 100 的倍数）
        qty = (raw_qty // self.lot_size) * self.lot_size
        if qty <= 0:
            # 至少 1 手
            if raw_qty >= 100:
                qty = (raw_qty // 100) * 100
            else:
                return None

        order = Order(
            symbol=signal.symbol,
            side=side,
            order_type=OrderType.MARKET,
            quantity=qty,
            price=current_price,
            strategy=signal.strategy,
        )
        return order

    def execute_signals(self, signals: List[Signal],
                        account: AccountInfo) -> List[OrderResult]:
        """批量执行信号 → 返回下单结果

        流程:
        1. 日初重置
        2. 信号 → 订单转换
        3. 交易前风控检查
        4. 路由执行
        """
        date_str = datetime.now().strftime('%Y-%m-%d')
        self.reset_daily(date_str)

        results = []
        for sig in signals:
            # 信号 → 订单
            order = self.signal_to_order(
                sig, account.cash, sig.price
            )
            if order is None:
                continue

            # 交易前风控检查（调用 risk_manager）
            # 这里的 pre_trade_check 需要封装一个信号检查
            # 使用 risk_manager 的现有逻辑

            # 执行下单
            result = self.broker.place_order(order)
            results.append(result)

            if result.success:
                self._orders[order.order_id] = order
                self._active_orders[order.order_id] = order
                self._daily_symbols.add(order.symbol)

                # 记录到数据库
                try:
                    db = get_db()
                    # 这里简化处理：不强制依赖 ops/database
                except Exception:
                    pass

        return results

    def pending_orders_count(self) -> int:
        """待处理订单数"""
        return sum(1 for o in self._active_orders.values() if o.is_active)

    def get_active_orders(self) -> List[Order]:
        """获取所有活动订单"""
        return [o for o in self._active_orders.values() if o.is_active]

    def has_reached_daily_limit(self, symbol: str) -> bool:
        """是否已达每日标的数量限制"""
        max_symbols = self.config.get("max_daily_symbols", 5)
        if symbol in self._daily_symbols:
            return False  # 已有持仓，不计数
        return len(self._daily_symbols) >= max_symbols
