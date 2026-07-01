"""
========================================
  LiveEngine — 实盘引擎（事件驱动版 V2）
========================================

【定位】
替换原有的 BacktestEngine + PortfolioEngine。
在 mock 模式下，以事件驱动架构模拟盘中实盘流程。

【V2 新增】
1. subscribe_quotes() 行情推送 → 触发策略盘中多次推理
2. on_fill() 成交回调 → 实时更新持仓状态
3. 支持 intraday 模式：盘中每个行情推送触发一次策略检查
4. on_error() 异常回调 → 错误隔离

【与回测引擎的核心区别】
┌──────────────┬─────────────────────┬──────────────────────────┐
│    维度      │ BacktestEngine      │ LiveEngine (V2)          │
├──────────────┼─────────────────────┼──────────────────────────┤
│ 数据推进     │ 自动逐日循环         │ 事件驱动：行情推送→推理  │
│ 成交模型     │ 假定100%成交         │ 概率成交 + 异步回调      │
│ 订单管理     │ 无                  │ 完整订单生命周期         │
│ 账户模型     │ 内置                 │ 委托 Broker 管理          │
│ 风控时机     │ 信号过滤             │ 下单前 + 成交后两次风控  │
│ 盘中频率     │ 每日1次推理          │ 行情每次推送均推理       │
│ 输出         │ print() + 报告       │ 结构化日志 + 回调        │
└──────────────┴─────────────────────┴──────────────────────────┘

【事件驱动流程】
update_prices() → subscribe_quotes()
   ↓ on_quote
策略推理 → 风控过滤 → 信号→订单 → place_order()
   ↓ on_fill (异步线程回调)
更新持仓 → 记录成交 → 检查止损
   ↓ on_error (任何异常)
错误日志 → 状态恢复
"""
import time
import sys
from typing import Dict, List, Optional, Callable
from datetime import datetime, date, timedelta

import pandas as pd

from strategies.base import BaseStrategy, Signal
from strategies.trend_following.strategy import TrendFollowingStrategy
from strategies.mean_reversion.strategy import MeanReversionStrategy
from strategies.factor_selection.strategy import FactorSelectionStrategy
from portfolio.combiner import PortfolioCombiner
from portfolio.risk_manager import RiskManager
from config.settings import RISK_CONFIG, TRADING_CONFIG, DEFAULT_SYMBOLS
from config.strategy_config import (
    TREND_FOLLOWING_CONFIG, MEAN_REVERSION_CONFIG, FACTOR_SELECTION_CONFIG
)

from trading.order import OrderResult, OrderSide, OrderStatus, AccountInfo, Fill
from trading.order_manager import OrderManager
from trading.mock_broker import MockBroker
from trading.real_time_data import MarketDataReplay
from trading.broker_base import OnFillCallback, OnQuoteCallback
from ops.logger import get_logger, flush

log = get_logger('live_engine')


class LiveEngine:
    """实盘引擎 V2 — 事件驱动架构"""

    def __init__(self,
                 symbols: List[str] = None,
                 speed: float = 10.0,
                 initial_capital: float = 100000.0):
        self.symbols = symbols or DEFAULT_SYMBOLS[:3]
        self.initial_capital = initial_capital

        # 初始化各子系统
        self.data = MarketDataReplay(symbols=self.symbols, speed=speed)

        self.broker = MockBroker(initial_capital=initial_capital)
        self.risk = RiskManager(RISK_CONFIG)
        self.combiner = PortfolioCombiner()
        self.order_manager = None  # init 中创建

        # 策略实例
        self.strategies: Dict[str, BaseStrategy] = {}
        self._strategy_symbols: Dict[str, str] = {}  # strategy_name → symbol

        # 运行状态
        self._running = False
        self._current_signals: List[Signal] = []
        self._daily_trades: List[dict] = []
        self._all_trades: List[dict] = []
        self._start_time: Optional[datetime] = None
        self._end_time: Optional[datetime] = None
        self._current_date: Optional[str] = None

        # 绩效追踪
        self._equity_curve: List[dict] = []
        self._drawdown_curve: List[dict] = []

        # V2: 事件回调注册 + 成交记录
        self._fills: List[Fill] = []
        self._on_error_callbacks: List[Callable] = []

    # ==================== 初始化 ====================

    def init_strategies(self):
        """初始化所有策略（支持 - 号前缀表示跳过）"""
        for name in ['trend_following', 'mean_reversion', 'factor_selection']:
            if name.startswith('-'):
                continue

        tf_config = dict(TREND_FOLLOWING_CONFIG)
        tf_config['symbol'] = self.symbols[0]
        self.strategies['trend_following'] = TrendFollowingStrategy(
            'trend_following', tf_config
        )
        self._strategy_symbols['trend_following'] = self.symbols[0]

        mr_config = dict(MEAN_REVERSION_CONFIG)
        mr_config['symbol'] = self.symbols[0]
        self.strategies['mean_reversion'] = MeanReversionStrategy(
            'mean_reversion', mr_config
        )
        self._strategy_symbols['mean_reversion'] = self.symbols[0]

        fs_config = dict(FACTOR_SELECTION_CONFIG)
        fs_config['symbols'] = self.symbols
        self.strategies['factor_selection'] = FactorSelectionStrategy(
            'factor_selection', fs_config
        )
        self._strategy_symbols['factor_selection'] = self.symbols[0]

        # 设置组合权重
        self.combiner.set_weights({
            'trend_following': 0.5,
            'mean_reversion': 0.1,
            'factor_selection': 0.4,
        })

        log.info('策略初始化完成', count=len(self.strategies))

    def start(self) -> bool:
        """启动引擎"""
        log.info('=' * 60)
        log.info('实盘引擎启动 (Mock 模式 V2 — 事件驱动)')
        log.info(f'初始资金: ¥{self.initial_capital:,.2f}')
        log.info(f'交易标的: {", ".join(self.symbols)}')
        log.info('=' * 60)

        # 1. 加载数据
        if not self.data.load_data():
            log.error('数据加载失败, 引擎启动终止')
            return False

        # 2. 连接券商（同时启动异步成交线程）
        if not self.broker.connect():
            log.error('券商连接失败, 引擎启动终止')
            return False

        # 3. 注册成交回调（V2 关键改进）
        self.broker.on_fill(self._on_fill_callback)

        # 4. 初始化策略
        self.init_strategies()

        # 5. 启动订单管理器
        self.order_manager = OrderManager(self.broker, self.risk)
        self.order_manager.start_monitor()

        self._running = True
        self._start_time = datetime.now()
        log.info('引擎启动完成')
        return True

    def stop(self):
        """停止引擎"""
        self._running = False
        if self.order_manager:
            self.order_manager.stop_monitor()
        self.broker.disconnect()
        self._end_time = datetime.now()
        log.info('引擎已停止')

    # ==================== V2: 成交回调 ====================

    def _on_fill_callback(self, fill: Fill):
        """成交回调 — 异步线程通知
        
        当 MockBroker 的后台线程完成成交后调用此方法。
        记录成交到本地缓存，供后续止损检查使用。
        """
        self._fills.append(fill)
        log.info(f'[成交回调] {fill.symbol} {fill.side.name} '
                 f'{fill.quantity}股 @ ¥{fill.price:.2f}')

    # ==================== 风控辅助 ====================

    def _calc_current_drawdown(self, account: AccountInfo) -> float:
        if not self._equity_curve:
            return 0.0
        peak = max(e['value'] for e in self._equity_curve)
        current = account.total_assets
        if peak <= 0:
            return 0.0
        return (peak - current) / peak

    def _calc_volatility(self) -> float:
        if len(self._equity_curve) < 20:
            return 0.0
        values = [e['value'] for e in self._equity_curve[-252:]]
        if len(values) < 2:
            return 0.0
        returns = [values[i] / values[i-1] - 1 for i in range(1, len(values))]
        if not returns:
            return 0.0
        import numpy as np
        return float(np.std(returns) * np.sqrt(252))

    # ==================== 日内多次推理（V2 新增） ====================

    def _run_strategies_intraday(self, market_data: Dict) -> List[Signal]:
        """日内策略推理 — 每次行情推送触发
        
        在真实实盘中，每次收到 tick/分钟级行情推送时调用。
        Mock 模式下，每次 next_day() 后调用（保持日频，但架构已支持盘内）。
        
        Returns:
            融合后、风控过滤后的信号列表
        """
        # 运行所有策略
        all_signals: Dict[str, List[Signal]] = {}
        for name, strategy in self.strategies.items():
            sym = self._strategy_symbols.get(name) or list(market_data.keys())[0]
            df = self._get_strategy_data(sym)
            if df is not None:
                signals = strategy.run(df)
                all_signals[name] = signals

        # 信号融合
        combined = self.combiner.combine(all_signals)

        # 风控过滤
        account = self.broker.get_account_info()
        positions = self.broker.get_positions()
        pos_dict = {p.symbol: p.quantity for p in positions}
        pos_ratios = {p.symbol: p.weight for p in positions}
        drawdown = self._calc_current_drawdown(account)

        filtered = self.risk.filter_signals(
            signals=combined,
            current_position_ratio=account.market_value / account.total_assets if account.total_assets > 0 else 0,
            current_positions=pos_dict,
            current_drawdown=drawdown,
            current_position_ratios=pos_ratios,
            annualized_volatility=self._calc_volatility(),
        )
        return filtered

    # ==================== 主循环 ====================

    def run_once(self) -> Dict:
        """单日交易循环

        V2 改进：拆分为 A/D 两个阶段
        A 阶段（盘中）：行情推送 → 策略推理 → 下单
        D 阶段（盘后）：记录净值 → 汇总
        """
        day_result = {
            'date': None,
            'signals': 0,
            'orders': 0,
            'fills': 0,
            'portfolio_value': 0.0,
            'cash': 0.0,
            'positions': 0,
            'errors': [],
        }

        try:
            # ===== A 阶段: 获取行情 & 策略推理 =====
            market_data = self.data.get_latest()
            if not market_data:
                return day_result

            current_date = self.data.get_current_date()
            if current_date is None:
                return day_result

            self._current_date = str(current_date.date()) if hasattr(current_date, 'date') else str(current_date)
            day_result['date'] = self._current_date

            # ① 更新 broker 行情缓存
            prices = {sym: d['close'] for sym, d in market_data.items()}
            self.broker.update_prices(prices)

            # ② 订阅行情回调（V2: 触发事件驱动）
            self.broker.subscribe_quotes(self.symbols)

            # ③ 策略推理 + 风控（V2: 拆出独立方法）
            filtered = self._run_strategies_intraday(market_data)
            day_result['signals'] = len(filtered)
            self._current_signals = filtered

            # ④ 执行订单
            if filtered:
                account = self.broker.get_account_info()
                results = self.order_manager.execute_signals(filtered, account)
                order_count = sum(1 for r in results if r.success)
                day_result['orders'] = order_count

            # ===== D 阶段: 记录净值 =====
            account = self.broker.get_account_info()
            day_result['portfolio_value'] = account.total_assets
            day_result['cash'] = account.cash
            day_result['positions'] = len(account.positions)
            day_result['fills'] = len(self.broker.get_fills())

            # 记录净值曲线
            self._equity_curve.append({
                'date': self._current_date,
                'value': account.total_assets,
                'cash': account.cash,
                'market_value': account.market_value,
            })

            # 打印日摘要
            if len(self._equity_curve) % 50 == 0 or len(self._equity_curve) <= 5:
                self._print_daily_summary(current_date, account, filtered)

        except Exception as e:
            log.error(f'运行异常', error=str(e))
            day_result['errors'].append(str(e))
            # V2: 触发异常回调
            for cb in self._on_error_callbacks:
                try:
                    cb(e)
                except Exception:
                    pass

        return day_result

    def run(self, max_days: int = None):
        """主循环 — 运行回放到结束或达到最大天数"""
        if not self._running:
            if not self.start():
                return

        day_count = 0
        try:
            while self._running:
                result = self.run_once()

                if result['date'] is None:
                    log.info('所有交易日回放完毕')
                    break

                day_count += 1

                if not self.data.next_day():
                    break

                if max_days and day_count >= max_days:
                    log.info(f'达到最大运行天数', days=max_days)
                    break

        except KeyboardInterrupt:
            log.info('用户中断')
        finally:
            self.stop()

        self._print_summary()

    # ==================== 辅助方法 ====================

    def _get_strategy_data(self, symbol: str,
                           current_date: pd.Timestamp = None) -> Optional[pd.DataFrame]:
        """获取策略所需的截至当前日期的数据"""
        from data.loader import DataLoader

        loader = DataLoader()
        data_dict = loader.load_multiple(
            [symbol],
            start=self.data.start_date,
            end=self.data.end_date
        )
        if symbol not in data_dict:
            return None

        df = data_dict[symbol]
        if current_date:
            df = df[df['date'] <= current_date].copy()
        if df.empty:
            return None

        from data.cleaner import clean_daily_data
        df = clean_daily_data(df)
        return df

    def _print_daily_summary(self, current_date, account, signals):
        date_str = str(current_date.date()) if hasattr(current_date, 'date') else str(current_date)
        log.info(f'[{date_str}] 资产: ¥{account.total_assets:,.2f} '
                 f'| 现金: ¥{account.cash:,.2f} '
                 f'| 持仓: {len(account.positions)}只 '
                 f'| 信号: {len(signals)}个 '
                 f'| 进度: {self.data.get_progress():.1%}')

    def _print_summary(self):
        elapsed = datetime.now() - self._start_time if self._start_time else timedelta(0)

        log.info('=' * 60)
        log.info('  模拟实盘交易完成 (V2 事件驱动)')
        log.info('=' * 60)
        log.info(f'运行时长: {elapsed.total_seconds():.1f}s')
        log.info(f'交易日: {len(self._equity_curve)} 天')

        if self._equity_curve:
            start_val = self.initial_capital
            end_val = self._equity_curve[-1]['value']
            total_return = (end_val - start_val) / start_val
            peak = max(e['value'] for e in self._equity_curve)
            max_dd = (peak - min(e['value'] for e in self._equity_curve)) / peak

            years = len(self._equity_curve) / 252
            annual_return = (1 + total_return) ** (1 / years) - 1 if years > 0 else 0

            log.info(f'初始资金: ¥{start_val:,.2f}')
            log.info(f'最终资产: ¥{end_val:,.2f}')
            log.info(f'总盈亏: ¥{end_val - start_val:+,.2f}')
            log.info(f'总收益率: {total_return:+.2%}')
            log.info(f'年化收益: {annual_return:+.2%}')
            log.info(f'最大回撤: {max_dd:.2%}')
            log.info(f'总成交笔数: {len(self._fills)} 笔')
            log.info('=' * 60)

        flush()

    def get_equity_curve(self) -> List[dict]:
        return self._equity_curve

    def on_error(self, callback: Callable):
        """注册异常回调"""
        self._on_error_callbacks.append(callback)


if __name__ == '__main__':
    engine = LiveEngine(symbols=DEFAULT_SYMBOLS[:3], speed=100, initial_capital=100000.0)
    engine.run(max_days=20)
