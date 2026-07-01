"""
========================================
 回测引擎 — 自研轻量版
 逐日回测，含滑点/佣金/印花税
========================================

【引擎设计哲学】
1. 逐日回测（Bar-by-Bar）而非事件驱动
   - 每日开盘时运行策略 → 生成信号 → 执行交易
   - 收盘后记录持仓和资产价值
   - 简单、直观、易于调试

2. 显式成本模型
   - 滑点（slippage）：买入时价格上移，卖出时下移
   - 佣金（commission）：双边收取
   - 印花税（stamp_tax）：A股卖出时单边收取

3. 现货模式（不支持做空/杠杆）
   - position 只能是 ≥ 0 的整数
   - 买入时需有足够现金
   - 卖出时需有足够持仓

【回测引擎 vs 真实交易的差异（必须知道的陷阱）】
  1. 本引擎假设"每天按收盘价成交"——实盘中不一定能成交
  2. 本引擎假设"无限流动性"——小盘股可能买不到指定股数
  3. 本引擎假设"立即成交"——实盘有延迟
  4. 本引擎假设"单只股票"——没有处理多股票指令冲突
"""

import pandas as pd
import numpy as np
from typing import List
from dataclasses import dataclass

from strategies.base import BaseStrategy, Signal
from config.settings import BACKTEST_CONFIG


@dataclass
class TradeRecord:
    """单笔成交记录

    记录一笔真实成交的完整信息。
    注意：一条 signal 可能对应一次对冲/分批？
    本引擎简化处理：一个信号 = 一笔成交。
    """
    date: pd.Timestamp      # 成交日期
    symbol: str             # 股票代码
    direction: int          # 1=买入, -1=卖出
    price: float            # 实际成交价（含滑点）
    quantity: int           # 成交股数（整数）
    value: float            # 成交金额 = price × quantity
    cost: float             # 交易成本 = 佣金 + 印花税
    strategy: str           # 来源策略


@dataclass
class DailyRecord:
    """每日持仓记录

    记录了每天收盘后的账户状态。
    这是后续计算净值曲线、回撤、夏普比率的原始数据。
    """
    date: pd.Timestamp      # 日期
    capital: float          # 现金余额
    position: int           # 持仓股数
    total_value: float      # 总资产 = 现金 + 持仓市值
    signal_count: int       # 当天信号数


class BacktestEngine:
    """
    自研回测引擎 — 逐日回测
    显式处理滑点/佣金/印花税

    使用示例：
        engine = BacktestEngine(BACKTEST_CONFIG)
        result = engine.run(df, strategy)
        # result.trades → 交易记录列表
        # result.daily_records → 每日净值数据
    """

    def __init__(self, config: dict = None):
        cfg = config or BACKTEST_CONFIG
        self.initial_capital = cfg['initial_capital']
        self.commission = cfg['commission']
        self.commission_min = cfg.get('min_commission', 5.0)
        self.stamp_tax = cfg['stamp_tax']
        self.slippage = cfg['slippage']

        # 运行时状态（由 _reset 初始化）
        self.capital = self.initial_capital
        self.position = 0           # 当前持仓股数
        self.current_price = 0.0    # 当前价格
        self.current_date = None    # 当前日期
        self.trades: List[TradeRecord] = []
        self.daily_records: List[DailyRecord] = []

    def run(self, data: pd.DataFrame, strategy: BaseStrategy):
        """运行回测

        回测流程：
        第1天 → 策略运行（指标未就绪→无信号）→ 记录
        第2天 → 策略运行（指标开始计算）→ 可能有信号 → 执行 → 记录
        ...
        第N天 → 策略运行 → 信号 → 执行 → 记录

        参数:
            data: OHLCV DataFrame（按日期升序排列）
            strategy: 策略实例

        返回:
            BacktestResult（包含daily_records和trades）

        注意：
            data 必须是按日期升序排列的！
            如果倒序，策略看到的"历史"实际上是"未来" = 严重作弊
        """
        self._reset()

        # 确保日期索引
        if not isinstance(data.index, pd.DatetimeIndex):
            if 'date' in data.columns:
                data = data.set_index('date')
            else:
                data.index = pd.to_datetime(data.index)

        n = len(data)
        for i in range(n):
            # 取到当前为止的所有数据（包括今天的）
            bar = data.iloc[:i + 1]
            self.current_date = data.index[i]
            self.current_price = float(data.iloc[i]['close'])

            # 运行策略（传入截止今天的所有数据）
            # 策略内部会用 data.iloc[-1] 只看最新一天
            signals = strategy.run(bar)
            for signal in signals:
                self._execute_signal(signal)

            # 记录每日资产
            self._record_daily()

        return BacktestResult(self.daily_records, self.trades, self.initial_capital)

    def _execute_signal(self, signal: Signal):
        """
        执行交易信号

        买入逻辑：
          1. 计算成交价 = 收盘价 × (1 + 滑点)
          2. 计算最大可买股数（按信号权重分配资金）
          3. 计算交易成本（佣金）
          4. 检查现金是否足够
          5. 如果足够 → 买入

        卖出逻辑：
          1. 计算成交价 = 收盘价 × (1 - 滑点) ← 卖出时滑点不利
          2. 计算卖出股数（按信号权重）
          3. 计算交易成本（佣金 + 印花税）
          4. 更新现金和持仓

        为什么要对股数取整（int）？
        A股最小交易单位是1手=100股？
        不，A股是1股为单位，但实际交易中整手方便。
        int 是向下取整，这可能导致少量现金剩余。
        这是保守处理——宁可不买，绝不超买。
        """
        if signal.direction == 1 and self.position == 0:
            # ===== 买入 =====
            # 滑点：买在更高的价格
            execute_price = self.current_price * (1 + self.slippage)

            # 计算最大可买股数（含佣金：确保总花费不超过可用资金）
            max_qty_raw = self.capital * signal.weight / (execute_price * (1 + self.commission))
            max_qty = int(max_qty_raw)
            # A股最小交易单位=1手=100股
            max_qty = (max_qty // 100) * 100
            if max_qty <= 0:
                return

            # 交易成本（含最低5元佣金）
            raw_comm = execute_price * max_qty * self.commission
            cost = max(self.commission_min, raw_comm)
            total = execute_price * max_qty + cost

            if total <= self.capital:
                self.capital -= total
                self.position = max_qty
                self.trades.append(TradeRecord(
                    date=self.current_date, symbol=signal.symbol,
                    direction=1, price=execute_price,
                    quantity=max_qty, value=execute_price * max_qty,
                    cost=cost, strategy=signal.strategy
                ))

        elif signal.direction == -1 and self.position > 0:
            # ===== 卖出 =====
            # 滑点：卖在更低的价格
            execute_price = self.current_price * (1 - self.slippage)
            sell_qty = int(self.position * signal.weight)
            # A股最小交易单位=1手=100股
            sell_qty = (sell_qty // 100) * 100
            if sell_qty <= 0:
                return

            # 卖出成本 = 佣金（最低5元）+ 印花税（A股卖出时收取）
            raw_comm = execute_price * sell_qty * self.commission
            cost = max(self.commission_min, raw_comm)
            tax = execute_price * sell_qty * self.stamp_tax
            self.capital += execute_price * sell_qty - cost - tax
            self.position -= sell_qty
            self.trades.append(TradeRecord(
                date=self.current_date, symbol=signal.symbol,
                direction=-1, price=execute_price,
                quantity=sell_qty, value=execute_price * sell_qty,
                cost=cost + tax, strategy=signal.strategy
            ))

    def _record_daily(self):
        """记录每日资产快照

        总资产 = 现金 + 持仓市值
        这是净值曲线的原始数据。
        """
        pv = self.position * self.current_price
        self.daily_records.append(DailyRecord(
            date=self.current_date, capital=round(self.capital, 2),
            position=self.position,
            total_value=round(self.capital + pv, 2), signal_count=0
        ))

    def _reset(self):
        """重置引擎状态（用于多次回测）

        每次 run() 前必须重置，否则前一次回测的持仓会污染下一次。
        """
        self.capital = self.initial_capital
        self.position = 0
        self.current_price = 0.0
        self.current_date = None
        self.trades = []
        self.daily_records = []


@dataclass
class BacktestResult:
    """回测结果容器"""
    daily_records: List[DailyRecord]
    trades: List[TradeRecord]
    initial_capital: float

    def to_dataframe(self) -> pd.DataFrame:
        """每日净值 → DataFrame（便于绘图和分析）"""
        return pd.DataFrame([{
            'date': r.date, 'capital': r.capital,
            'position': r.position, 'total_value': r.total_value
        } for r in self.daily_records])

    def to_trades_dataframe(self) -> pd.DataFrame:
        """交易记录 → DataFrame（便于分析）"""
        return pd.DataFrame([{
            'date': t.date, 'symbol': t.symbol,
            'direction': t.direction, 'price': t.price,
            'quantity': t.quantity, 'value': t.value,
            'cost': t.cost, 'strategy': t.strategy
        } for t in self.trades])
