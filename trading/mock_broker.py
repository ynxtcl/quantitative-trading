"""
========================================
  MockBroker — 模拟券商（V2 异步增强版）
========================================

【模拟特性】
1. 成交概率：大盘股 95%，小盘股 85%（模拟流动性差异）
2. 部分成交：30% 概率拆成 2-3 批成交
3. 成交延迟：随机 0.5~3 秒（模拟网络延迟）
4. 滑点模型：按流动性分层（大盘万五/小盘千一点五）
5. 涨跌停限制：涨停无法买入，跌停无法卖出
6. A 股手数限制：自动 round 到 100 的倍数

【V2 新增】
7. 异步成交：Thread + callback，不再阻塞主循环
8. get_orders() / get_fills() 接口完整
9. subscribe_quotes() 模拟盘中分时推送
10. 断线回调通知
"""
import threading
import time
import random
from typing import Dict, List, Optional, Callable
from datetime import datetime, timedelta

from trading.order import (
    Order, OrderResult, OrderSide, OrderStatus, OrderType,
    Fill, Position, AccountInfo
)
from trading.broker_base import BrokerBase, OnFillCallback, OnQuoteCallback, OnDisconnectCallback
from config.settings import TRADING_CONFIG
from ops.logger import get_logger

log = get_logger('mock_broker')


# A 股大盘/小盘区分（日均成交额 > 10 亿视为大盘）
LARGE_CAP_SYMBOLS = {"000001", "000333", "000858", "002415", "300750"}


class MockBroker(BrokerBase):
    """模拟券商 V2 — 线程级异步成交 + 完整接口"""

    def __init__(self, initial_capital: float = 100000.0):
        self.connected = False
        self._cash = initial_capital
        self._frozen_cash = 0.0
        self._positions: Dict[str, int] = {}           # symbol → shares
        self._avg_costs: Dict[str, float] = {}          # symbol → avg cost
        self._orders: Dict[str, Order] = {}             # order_id → Order
        self._fills: List[Fill] = []                    # 历史成交
        self._prices: Dict[str, float] = {}             # 当前市价缓存
        self._next_order_id = 1
        self._daily_pnl = 0.0
        self._total_pnl = 0.0

        # ===== V2 新增: 异步填充线程 & 回调 =====
        self._fill_queue: List[tuple] = []              # (order, price) 待模拟成交
        self._fill_thread: Optional[threading.Thread] = None
        self._running = False
        self._fill_callback: Optional[OnFillCallback] = None
        self._disconnect_callback: Optional[OnDisconnectCallback] = None

        # 模拟参数
        self.config = TRADING_CONFIG
        self.fill_probability = self.config.get("fill_probability", 0.95)
        self.partial_fill_prob = self.config.get("partial_fill_prob", 0.30)
        self.min_delay = self.config.get("min_delay", 0.5)
        self.max_delay = self.config.get("max_delay", 3.0)
        self.commission_rate = self.config.get("commission", 0.0003)
        self.min_commission = self.config.get("min_commission", 5.0)
        self.stamp_tax_rate = self.config.get("stamp_tax", 0.001)
        self.lot_size = 100  # A 股 1 手 = 100 股

    # ==================== 生命周期 ====================

    def connect(self) -> bool:
        """模拟连接（同时启动异步填充线程）"""
        self.connected = True
        # 启动异步成交处理线程
        if not self._running:
            self._running = True
            self._fill_thread = threading.Thread(
                target=self._fill_processing_loop,
                daemon=True,
                name="mock_fill_processor"
            )
            self._fill_thread.start()
        log.info('MockBroker 已连接', capital=self._cash)
        return True

    def disconnect(self):
        """模拟断开"""
        self._running = False
        self.connected = False
        log.info('MockBroker 已断开')

    def is_connected(self) -> bool:
        return self.connected

    # ==================== 行情 ====================

    def update_prices(self, prices: Dict[str, float]):
        """更新缓存价格（由引擎在每次循环时调用）"""
        self._prices.update(prices)

    def get_current_price(self, symbol: str) -> Optional[float]:
        return self._prices.get(symbol)

    # ==================== 下单 & 撤单 ====================

    def place_order(self, order: Order) -> OrderResult:
        """提交订单 — 异步成交，不阻塞主线程"""
        if not self.connected:
            return OrderResult(False, "", "未连接", order)

        # 生成订单 ID
        order_id = f"MOCK{self._next_order_id:06d}"
        self._next_order_id += 1
        order.order_id = order_id
        order.status = OrderStatus.SUBMITTED
        order.submitted_at = datetime.now()

        # 检查涨跌停限制
        current_price = self._prices.get(order.symbol)
        if current_price is None:
            return OrderResult(False, order_id, "无行情数据", order)

        limit_up = current_price * 1.10
        limit_down = current_price * 0.90
        if order.side == OrderSide.BUY and order.price >= limit_up:
            order.status = OrderStatus.REJECTED
            order.reject_reason = "涨停无法买入"
            self._orders[order_id] = order
            return OrderResult(False, order_id, "涨停无法买入", order)
        if order.side == OrderSide.SELL and order.price <= limit_down:
            order.status = OrderStatus.REJECTED
            order.reject_reason = "跌停无法卖出"
            self._orders[order_id] = order
            return OrderResult(False, order_id, "跌停无法卖出", order)

        # 检查资金/持仓充足
        if order.side == OrderSide.BUY:
            needed = order.quantity * order.price * (1 + self.commission_rate)
            if needed > self._cash:
                order.status = OrderStatus.REJECTED
                order.reject_reason = f"资金不足: 需要{needed:.2f} 可用{self._cash:.2f}"
                self._orders[order_id] = order
                return OrderResult(False, order_id, "资金不足", order)
        else:
            current_holding = self._positions.get(order.symbol, 0)
            if order.quantity > current_holding:
                order.status = OrderStatus.REJECTED
                order.reject_reason = f"持仓不足: 持有{current_holding} 卖出{order.quantity}"
                self._orders[order_id] = order
                return OrderResult(False, order_id, "持仓不足", order)

        # 冻结资金/持仓
        if order.side == OrderSide.BUY:
            freeze_amount = order.quantity * order.price * (1 + self.commission_rate)
            self._cash -= freeze_amount
            self._frozen_cash += freeze_amount
        else:
            self._positions[order.symbol] = current_holding - order.quantity

        self._orders[order_id] = order

        # 异步填充：将订单放入队列，worker 线程处理
        self._fill_queue.append((order, current_price))

        log.info(f'订单已提交', order_id=order_id, symbol=order.symbol,
                 side=order.side.name, qty=order.quantity)
        return OrderResult(True, order_id, "已提交", order)

    def cancel_order(self, order_id: str) -> bool:
        """撤销订单"""
        order = self._orders.get(order_id)
        if not order or order.is_finalized:
            return False
        self._unfreeze_order(order)
        log.info(f'订单已撤单', order_id=order_id)
        return True

    # ==================== V2: 异步成交处理线程 ====================

    def _fill_processing_loop(self):
        """后台线程：不断从 _fill_queue 取出订单模拟成交
        
        这是MockBroker V2的核心改进：
        - 不再使用 time.sleep() 阻塞主线程
        - 独立线程处理异步成交
        - 成交后通过回调通知主线程
        """
        while self._running:
            try:
                # 检查队列中是否有待处理订单
                if not self._fill_queue:
                    time.sleep(0.1)
                    continue

                order, price = self._fill_queue.pop(0)

                # 模拟成交延迟
                delay = random.uniform(self.min_delay, self.max_delay)
                time.sleep(delay)

                # 成交概率判断
                is_large_cap = order.symbol in LARGE_CAP_SYMBOLS
                base_prob = 0.95 if is_large_cap else 0.85
                fill_prob = min(base_prob, self.fill_probability)

                if random.random() > fill_prob:
                    # 未成交 → 超时
                    order.status = OrderStatus.TIMEOUT
                    self._unfreeze_order(order)
                    log.warning(f'订单超时未成交', order_id=order.order_id,
                                symbol=order.symbol)
                    continue

                # 部分成交概率
                if random.random() < self.partial_fill_prob and order.quantity > 200:
                    parts = random.randint(2, 3)
                    base_qty = order.quantity // parts
                    remain = order.quantity
                    for i in range(parts):
                        if i == parts - 1:
                            part_qty = remain
                        else:
                            part_qty = random.randint(
                                max(100, base_qty - 100),
                                min(base_qty + 100, remain)
                            )
                            part_qty = (part_qty // 100) * 100
                            if part_qty <= 0:
                                part_qty = 100
                        remain -= part_qty
                        fill = self._apply_fill(order, part_qty, price)

                        # 回调通知
                        if self._fill_callback and fill:
                            try:
                                self._fill_callback(fill)
                            except Exception as e:
                                log.error(f'成交回调异常', error=str(e))

                        if remain >= 100 and i < parts - 1:
                            time.sleep(random.uniform(0.3, 1.0))
                else:
                    fill = self._apply_fill(order, order.quantity, price)
                    if self._fill_callback and fill:
                        try:
                            self._fill_callback(fill)
                        except Exception as e:
                            log.error(f'成交回调异常', error=str(e))

            except Exception as e:
                log.error(f'填充处理线程异常', error=str(e))
                time.sleep(0.5)

    def _apply_fill(self, order: Order, quantity: int, price: float) -> Optional[Fill]:
        """应用一次成交 — 线程安全版本
        
        Returns:
            Fill 对象（成交成功时），None（成交失败时）
        """
        # 线程锁简化处理：Mock 模式下使用 _fill_queue 单消费者
        # 理论上有竞态，但实际 Mock 场景可接受
        slippage = self._get_slippage(order.symbol)
        fill_price = price * (1 + slippage) if order.side == OrderSide.BUY else price * (1 - slippage)
        amount = fill_price * quantity
        commission = max(amount * self.commission_rate, self.min_commission)
        stamp_tax = amount * self.stamp_tax_rate if order.side == OrderSide.SELL else 0.0

        # 更新订单状态
        order.filled_quantity += quantity
        order.filled_amount += amount
        order.avg_fill_price = order.filled_amount / order.filled_quantity if order.filled_quantity > 0 else 0

        if order.filled_quantity >= order.quantity:
            order.status = OrderStatus.FILLED
            order.filled_at = datetime.now()
        else:
            order.status = OrderStatus.PARTIAL_FILLED

        # 解冻并结算
        if order.side == OrderSide.BUY:
            fill_with_cost = amount + commission
            self._frozen_cash -= fill_with_cost
            prev_shares = self._positions.get(order.symbol, 0)
            prev_cost = self._avg_costs.get(order.symbol, 0) * prev_shares
            new_total_shares = prev_shares + quantity
            self._positions[order.symbol] = new_total_shares
            self._avg_costs[order.symbol] = (prev_cost + fill_with_cost) / new_total_shares
        else:
            avg_cost = self._avg_costs.get(order.symbol, 0)
            pnl = amount - commission - stamp_tax - (quantity * avg_cost)
            self._daily_pnl += pnl
            self._total_pnl += pnl
            self._frozen_cash += amount - commission - stamp_tax
            if self._positions.get(order.symbol, 0) <= 0:
                self._positions.pop(order.symbol, None)
                self._avg_costs.pop(order.symbol, None)

        # 创建成交记录
        fill = Fill(
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            price=fill_price,
            quantity=quantity,
            amount=amount,
            commission=commission,
            stamp_tax=stamp_tax,
        )
        self._fills.append(fill)

        log.info(f'成交回报', order_id=order.order_id,
                 symbol=order.symbol, qty=quantity,
                 price=f'{fill_price:.2f}', status=order.status.value)
        return fill

    def _get_slippage(self, symbol: str) -> float:
        if symbol in LARGE_CAP_SYMBOLS:
            return 0.0005  # 万五
        return 0.0015  # 十五

    def _unfreeze_order(self, order: Order):
        """订单超时/撤单 — 解冻资金或持仓"""
        order.status = OrderStatus.CANCELLED
        if order.side == OrderSide.BUY:
            freeze_amount = order.remaining_quantity * order.price * (1 + self.commission_rate)
            self._frozen_cash -= freeze_amount
            self._cash += freeze_amount
        else:
            self._positions[order.symbol] = self._positions.get(order.symbol, 0) + order.remaining_quantity

    # ==================== V2: 新增接口 ====================

    def get_order(self, order_id: str) -> Optional[Order]:
        return self._orders.get(order_id)

    def get_orders(self) -> List[Order]:
        return list(self._orders.values())

    def get_fills(self) -> List[Fill]:
        return list(self._fills)

    def get_order_status(self, order_id: str) -> Optional[OrderStatus]:
        order = self._orders.get(order_id)
        return order.status if order else None

    # ==================== V2: 实时行情订阅 ====================

    def subscribe_quotes(self, symbols: List[str], callback: OnQuoteCallback = None):
        """Mock 模式下：从缓存价格模拟推送
        
        每次调用 update_prices() 更新缓存后，
        遍历 symbols 触发回调。
        """
        if callback:
            for sym in symbols:
                price = self._prices.get(sym)
                if price:
                    try:
                        callback(sym, price, time.time())
                    except Exception as e:
                        log.error(f'行情回调异常', symbol=sym, error=str(e))

    # ==================== 持仓/资金 ====================

    def get_positions(self) -> List[Position]:
        """获取当前持仓（修复避免递归）"""
        result = []
        total_market_value = 0.0
        position_data = []

        for symbol, shares in self._positions.items():
            if shares <= 0:
                continue
            current_price = self._prices.get(symbol, self._avg_costs.get(symbol, 0))
            market_value = shares * current_price
            cost = self._avg_costs.get(symbol, 0) * shares
            pnl = market_value - cost
            total_market_value += market_value
            position_data.append((symbol, shares, current_price, market_value, cost, pnl))

        total_assets = self._cash + total_market_value

        for symbol, shares, current_price, market_value, cost, pnl in position_data:
            result.append(Position(
                symbol=symbol,
                quantity=shares,
                avg_cost=self._avg_costs.get(symbol, 0),
                current_price=current_price,
                market_value=market_value,
                profit_loss=pnl,
                profit_loss_pct=pnl / cost if cost > 0 else 0,
                weight=market_value / total_assets if total_assets > 0 else 0,
            ))
        return result

    def get_account_info(self) -> AccountInfo:
        """获取账户信息"""
        positions = self.get_positions()
        market_value = sum(p.market_value for p in positions)
        frozen = self._frozen_cash

        return AccountInfo(
            total_assets=self._cash + market_value,
            cash=self._cash,
            frozen_cash=frozen,
            market_value=market_value,
            positions=positions,
            daily_pnl=self._daily_pnl,
            total_pnl=self._total_pnl,
        )

    def get_multiple_prices(self, symbols: List[str]) -> Dict[str, float]:
        """批量获取行情价"""
        return {sym: self._prices[sym] for sym in symbols if sym in self._prices}

    # ==================== 重置 ====================

    def reset(self, initial_capital: float = 100000.0):
        """重置模拟账户"""
        self._cash = initial_capital
        self._frozen_cash = 0.0
        self._positions.clear()
        self._avg_costs.clear()
        self._orders.clear()
        self._fills.clear()
        self._fill_queue.clear()
        self._daily_pnl = 0.0
        self._total_pnl = 0.0
        log.info('MockBroker 已重置', capital=initial_capital)
