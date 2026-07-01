"""
========================================
  BrokerBase — 券商抽象接口
========================================

【设计思想】
统一所有券商（模拟/实盘）的操作接口。
实盘模式下，XtQuantBroker 继承此类实现真实交易。
Mock 模式下，MockBroker 继承此类模拟交易。

【接口分类】
┌─────────────┬──────────────────────────────────────┬──────────┐
│  类别        │ 方法                                  │ 优先级    │
├─────────────┼──────────────────────────────────────┼──────────┤
│ 生命周期     │ connect / disconnect / is_connected   │ P0       │
│ 下单         │ place_order / cancel_order            │ P0       │
│ 订单查询     │ get_order / get_orders (新增)         │ P0       │
│ 成交查询     │ get_fills (新增)                      │ P0       │
│ 持仓/资金   │ get_positions / get_account_info       │ P0       │
│ 行情         │ get_current_price / get_multiple_prices│ P0       │
│ 实时行情     │ subscribe_quotes (新增)               │ P0       │
│ 事件回调     │ on_quote / on_fill (新增)             │ P1       │
│ 断线重连     │ on_disconnected / reconnect (新增)    │ P1       │
└─────────────┴──────────────────────────────────────┴──────────┘

【实盘流程】
subscribe_quotes() 
  → on_quote(quote) → 策略推理 → place_order(order)
  → on_fill(fill)   → 更新持仓 → 记录成交
  → on_disconnected → reconnect() → 恢复订阅

【Mock 流程】
MarketDataReplay.next_day()
  → engine.run_once() → 策略推理 → place_order(order)
  → (异步线程) _simulate_fill_async → on_fill callback
"""
from abc import ABC, abstractmethod
from typing import List, Dict, Optional, Callable
from trading.order import Order, OrderResult, Fill, Position, AccountInfo, OrderStatus


# 回调类型定义
OnFillCallback = Callable[[Fill], None]
OnQuoteCallback = Callable[[str, float, float], None]  # (symbol, price, timestamp)
OnDisconnectCallback = Callable[[str], None]            # (reason)


class BrokerBase(ABC):
    """券商抽象基类 — 定义所有券商必须实现的方法"""

    # ==================== 生命周期 ====================

    @abstractmethod
    def connect(self) -> bool:
        """连接到券商服务器"""
        ...

    @abstractmethod
    def disconnect(self):
        """断开连接"""
        ...

    @abstractmethod
    def is_connected(self) -> bool:
        """是否已连接"""
        ...

    # ==================== 下单 ====================

    @abstractmethod
    def place_order(self, order: Order) -> OrderResult:
        """提交订单"""
        ...

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """撤销订单"""
        ...

    # ==================== 订单查询 ====================

    @abstractmethod
    def get_order(self, order_id: str) -> Optional[Order]:
        """查询单个订单（含状态/成交明细）"""
        ...

    def get_orders(self) -> List[Order]:
        """查询当日所有委托（含已撤/已成交）
        
        实盘模式下从券商服务器拉取当日委托列表。
        Mock 模式下返回内存中所有订单。
        基类提供空列表默认实现，子类可覆盖。
        """
        return []

    # ==================== 成交查询 ====================

    def get_fills(self) -> List[Fill]:
        """查询当日所有成交明细
        
        实盘模式下从券商服务器拉取当日成交列表。
        Mock 模式下返回内存中所有成交。
        基类提供空列表默认实现，子类可覆盖。
        """
        return []

    # ==================== 持仓/资金 ====================

    @abstractmethod
    def get_positions(self) -> List[Position]:
        """获取当前持仓"""
        ...

    @abstractmethod
    def get_account_info(self) -> AccountInfo:
        """获取账户信息"""
        ...

    # ==================== 行情 ====================

    @abstractmethod
    def get_current_price(self, symbol: str) -> Optional[float]:
        """获取当前行情价"""
        ...

    def get_multiple_prices(self, symbols: List[str]) -> Dict[str, float]:
        """批量获取行情价（基类提供默认循环实现）"""
        result = {}
        for sym in symbols:
            price = self.get_current_price(sym)
            if price is not None:
                result[sym] = price
        return result

    # ==================== 实时行情订阅 ====================

    def subscribe_quotes(self, symbols: List[str], callback: OnQuoteCallback = None):
        """订阅实时行情
        
        Args:
            symbols: 需要订阅的股票代码列表
            callback: 行情推送回调 (symbol, price, timestamp)
            
        实盘模式下（XtQuantBroker）:
            - 通过 WebSocket 连接行情服务器
            - 每次 tick 推送时调用 callback
            - tick 频率: 3秒/笔（A股 Level-1）
            
        Mock 模式下（MockBroker）:
            - 从 MarketDataReplay 拉取当日行情
            - 模拟 intraday 分时推送（每分钟推一次）
        """
        raise NotImplementedError("子类必须实现 subscribe_quotes()")

    def unsubscribe_quotes(self, symbols: List[str] = None):
        """取消订阅实时行情
        
        Args:
            symbols: 需要取消的股票列表，None 表示取消全部
        """
        pass

    # ==================== 事件回调注册 ====================

    def on_fill(self, callback: OnFillCallback):
        """注册成交回调 — 券商异步推送成交回报时调用
        
        Args:
            callback: 成交回调函数，参数为 Fill 对象
        """
        self._fill_callback = callback

    def on_disconnect(self, callback: OnDisconnectCallback):
        """注册断线回调 — 网络异常/服务端断开时调用
        
        Args:
            callback: 断线回调函数，参数为原因字符串
        """
        self._disconnect_callback = callback

    # ==================== 断线重连 ====================

    def reconnect(self, max_retries: int = 3) -> bool:
        """断线重连
        
        Args:
            max_retries: 最大重试次数
            
        Returns:
            是否重连成功
        """
        for attempt in range(max_retries):
            if self.connect():
                return True
        return False
