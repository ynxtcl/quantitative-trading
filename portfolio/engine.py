"""
========================================
  PortfolioEngine — 多策略组合回测引擎
========================================

【与 BacktestEngine 的区别】
BacktestEngine（单股票）            PortfolioEngine（组合）
────────────────────────────────   ───────────────────────────────
position: int（单只股票股数）        positions: Dict[str, int]
每日跑1个策略                        每日跑N个策略（同/不同股票）
信号直接执行                         信号先 combine → risk filter → 执行
单股票净值曲线                       组合净值曲线（多股票合并）

【执行流程】
每日循环：
  1. 遍历所有股票 → 所有策略 → 收集信号
  2. combiner.combine() → 净权重求和（冲突解决）
  3. risk_manager.filter_signals() → 风控过滤
  4. 批量执行过滤后的信号
  5. 记录当日组合净值

【净权重求和逻辑】
同一只股票如果多个策略同时出信号：
  - TF 买入 (weight=0.40) + MR 卖出 (weight=0.15) = 净买入 0.25
  - 净结果为正 → 买入；为负 → 卖出；为0 → 无信号
"""
import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

from strategies.base import BaseStrategy, Signal
from strategies.factor_rebalancer import FactorRebalancer
from portfolio.combiner import PortfolioCombiner
from portfolio.risk_manager import RiskManager


@dataclass
class PortfolioTradeRecord:
    """组合回测的成交记录"""
    date: pd.Timestamp
    symbol: str
    direction: int           # 1=买入, -1=卖出
    price: float
    quantity: int
    value: float
    cost: float
    strategy: str            # 'trend_following' / 'mean_reversion' / 'factor_selection'


@dataclass
class PortfolioDailyRecord:
    """组合每日持仓快照"""
    date: pd.Timestamp
    capital: float
    total_value: float
    positions: Dict[str, int]
    position_values: Dict[str, float]
    signal_count: int
    strategy_contributions: Dict[str, float] = field(default_factory=dict)


class PortfolioResult:
    """组合回测结果容器"""
    def __init__(self, daily_records: List[PortfolioDailyRecord],
                 trades: List[PortfolioTradeRecord],
                 initial_capital: float):
        self.daily_records = daily_records
        self.trades = trades
        self.initial_capital = initial_capital
        self._daily_df: Optional[pd.DataFrame] = None

    def to_dataframe(self) -> pd.DataFrame:
        if self._daily_df is not None:
            return self._daily_df
        rows = []
        for r in self.daily_records:
            rows.append({
                'date': r.date,
                'capital': r.capital,
                'total_value': r.total_value,
                'positions_count': len(r.positions),
                'signal_count': r.signal_count,
            })
        self._daily_df = pd.DataFrame(rows)
        return self._daily_df

    def final_value(self) -> float:
        return self.daily_records[-1].total_value if self.daily_records else self.initial_capital

    def total_return(self) -> float:
        return (self.final_value() - self.initial_capital) / self.initial_capital

    def annual_return(self) -> float:
        n_days = len(self.daily_records)
        if n_days < 2:
            return 0.0
        n_years = n_days / 252
        return (1 + self.total_return()) ** (1 / n_years) - 1

    def get_equity_series(self) -> pd.Series:
        df = self.to_dataframe()
        return df.set_index('date')['total_value']


class PortfolioEngine:
    """
    多策略组合回测引擎

    使用示例：
        engine = PortfolioEngine(BACKTEST_CONFIG)
        result = engine.run(
            data_dict=data_dict,
            tf_strategies=tf_strats,
            mr_strategies=mr_strats,
            rebalancer=rebalancer,
            combiner=combiner,
            risk_manager=risk_mgr,
        )
    """

    def __init__(self, config: dict):
        self.initial_capital = config['initial_capital']
        self.commission = config['commission']
        self.min_commission = config.get('min_commission', 5.0)
        self.stamp_tax = config['stamp_tax']
        self.slippage = config['slippage']
        self.peak_value = self.initial_capital
        self.daily_return_buffer = []  # 跟踪峰值净值（用于回撤熔断）
        self.cost_basis: Dict[str, float] = {}  # {symbol: 平均持仓成本（含佣金）}
        self.stop_loss_triggered: int = 0        # 止损触发次数统计
        self.trailing_stop_triggered: int = 0    # 移动止损触发次数统计
        self.daily_return_buffer: List[float] = []  # C1: 最近20日收益率缓存
        self.peak_prices: Dict[str, float] = {}     # {symbol: 持仓期间最高价（用于Trailing Stop）}

        self.capital = self.initial_capital
        self.positions: Dict[str, int] = {}
        self.trades: List[PortfolioTradeRecord] = []
        self.daily_records: List[PortfolioDailyRecord] = []

    def run(self,
            data_dict: Dict[str, pd.DataFrame],
            tf_strategies: Dict[str, BaseStrategy] = {},
            mr_strategies: Dict[str, BaseStrategy] = {},
            rebalancer: Optional[FactorRebalancer] = None,
            xgb_strategies: Dict[str, BaseStrategy] = {},
            xgb_data_dict: Optional[Dict[str, pd.DataFrame]] = None,
            combiner: Optional[PortfolioCombiner] = None,
            risk_manager: Optional[RiskManager] = None) -> PortfolioResult:
        """
        执行组合回测

        参数:
            data_dict: {symbol: DataFrame} 所有股票的 OHLCV 数据
                       TF/MR/FS 使用此数据（原始 OHLCV）
            tf_strategies: {symbol: TrendFollowingStrategy}
            mr_strategies: {symbol: MeanReversionStrategy}
            rebalancer: FactorRebalancer 实例（None=跳过因子选股）
            xgb_strategies: {symbol: XGBoostSignalStrategy}  (Phase 4.5 新增)
            xgb_data_dict: {symbol: DataFrame} 带 ML 特征的数据
                           None 时引擎自动调用 engineer_features (Phase 4.5 新增)
            combiner: PortfolioCombiner 实例
            risk_manager: RiskManager 实例

        返回:
            PortfolioResult 包含每日净值 + 交易记录

        数据流完整性保障:
            - xgb_data_dict 应包含预计算的 22 维特征列
            - data_dict 保持原始 OHLCV（TF/MR 不需要特征列）
            - 两者索引必须一致（日期对齐在方法内部处理）
        """
        self._reset()

        # ---- 对齐日期索引 ----
        aligned: Dict[str, pd.DataFrame] = {}
        symbols = list(data_dict.keys())
        for sym in symbols:
            df = data_dict[sym].copy()
            if not isinstance(df.index, pd.DatetimeIndex):
                if 'date' in df.columns:
                    df = df.set_index('date')
                else:
                    df.index = pd.to_datetime(df.index)
            aligned[sym] = df

        # ---- 对齐 XGBoost 数据（如果有） ----
        xgb_aligned: Dict[str, pd.DataFrame] = {}
        if xgb_data_dict:
            for sym, df in xgb_data_dict.items():
                xdf = df.copy()
                if not isinstance(xdf.index, pd.DatetimeIndex):
                    if 'date' in xdf.columns:
                        xdf = xdf.set_index('date')
                    else:
                        xdf.index = pd.to_datetime(xdf.index)
                xgb_aligned[sym] = xdf
        else:
            # 如果没有预计算特征数据，使用 data_dict（XGBoost 策略内部会调用 engineer_features）
            xgb_aligned = aligned

        # 取所有股票交易日的并集
        all_dates = pd.DatetimeIndex([])
        for df in aligned.values():
            all_dates = all_dates.union(df.index)
        all_dates = sorted(all_dates)

        # 初始化持仓
        for sym in symbols:
            self.positions[sym] = 0

        current_prices: Dict[str, float] = {sym: 0.0 for sym in symbols}
        last_rebalance_month: Optional[Tuple[int, int]] = None

        for current_date in all_dates:
            strategies_signals: Dict[str, List[Signal]] = {
                'trend_following': [],
                'mean_reversion': [],
                'factor_selection': [],
                'xgboost': [],
            }

            # ---- 运行择时策略（TF + MR）- 跳过空的策略字典 ----
            for sym in symbols:
                df = aligned[sym]
                if current_date not in df.index:
                    if sym in current_prices and current_prices[sym] > 0:
                        pass
                    else:
                        continue
                else:
                    current_prices[sym] = float(df.loc[current_date, 'close'])

                    bar = df.loc[:current_date]

                    # 趋势跟踪（跳过空字典=不启用）
                    if tf_strategies and sym in tf_strategies:
                        tf_sigs = tf_strategies[sym].run(bar)
                        strategies_signals['trend_following'].extend(tf_sigs)

                    # 均值回归（跳过空字典=不启用）
                    if mr_strategies and sym in mr_strategies:
                        mr_sigs = mr_strategies[sym].run(bar)
                        strategies_signals['mean_reversion'].extend(mr_sigs)

                    # XGBoost ML 策略（Phase 4.5 新增）
                    # 使用预计算特征数据（xgb_aligned）而非原始 OHLCV（aligned）
                    # 如果 xgb_aligned 中日期不存在，跳过（可能上市日不同）
                    if xgb_strategies and sym in xgb_strategies:
                        sym_xgb = xgb_aligned.get(sym)
                        if sym_xgb is not None and current_date in sym_xgb.index:
                            xgb_bar = sym_xgb.loc[:current_date]
                            xgb_sigs = xgb_strategies[sym].run(xgb_bar)
                            strategies_signals['xgboost'].extend(xgb_sigs)

            # ---- 因子选股月度再平衡 ----
            if rebalancer is not None:
                current_month = (current_date.year, current_date.month)
                is_last_td = self._is_last_trading_day(current_date, all_dates)
                if current_month != last_rebalance_month and is_last_td:
                    rebal_sigs = rebalancer.generate_rebalance_signals(
                        aligned, current_date, current_prices, self.positions
                    )
                    strategies_signals['factor_selection'] = rebal_sigs
                    last_rebalance_month = current_month

            # ---- 净权重求和（combiner=None 时直接合并） ----
            if combiner is not None:
                combined_signals = combiner.combine(strategies_signals)
            else:
                combined_signals = []
                for _, sigs in strategies_signals.items():
                    combined_signals.extend(sigs)

            # ---- 计算当前仓位比例 ----
            total_position_value = sum(
                self.positions.get(sym, 0) * current_prices.get(sym, 0)
                for sym in symbols
            )
            total_assets = self.capital + total_position_value
            current_ratio = total_position_value / total_assets if total_assets > 0 else 0

            # ---- 计算当前回撤（用于熔断） ----
            current_value = self.capital + total_position_value
            self.peak_value = max(self.peak_value, current_value)
            current_drawdown = (self.peak_value - current_value) / self.peak_value if self.peak_value > 0 else 0.0

            # ---- 计算所有持仓股票的20日中位数回报率（用于广谱下跌过滤器）----
            market_median_return = 0.0
            if len(self.daily_records) >= 20:
                # 收集每只有持仓的股票的20日回报率
                all_returns_20d = []
                for sym in symbols:
                    if sym in aligned and sym in current_prices and current_prices[sym] > 0:
                        df_sym = aligned[sym]
                        idx = df_sym.index.get_loc(current_date) if current_date in df_sym.index else -1
                        if idx >= 20:
                            price_today = float(df_sym.iloc[idx]['close'])
                            price_20d_ago = float(df_sym.iloc[idx - 20]['close'])
                            if price_20d_ago > 0:
                                ret_20d = (price_today - price_20d_ago) / price_20d_ago
                                all_returns_20d.append(ret_20d)
                if all_returns_20d:
                    import numpy as np
                    market_median_return = float(np.median(all_returns_20d))

            # ---- B1: 组合级止损检查（在风控过滤之前执行） ----
            # 强制平仓信号优先于普通信号，确保亏损头寸及时了结
            stop_loss_pct = risk_manager.config.get("stop_loss", 0.08) if risk_manager is not None else 0.08
            trailing_pct = risk_manager.config.get("trailing_stop", 0.15) if risk_manager is not None else 0.15
            enforce_sl = risk_manager.config.get("enforce_stop_loss", True) if risk_manager is not None else False
            forced_sells = []
            if enforce_sl:
                forced_sells = self._check_stop_loss(current_prices, current_date, stop_loss_pct, trailing_pct)
                if forced_sells:
                    combined_signals = forced_sells + combined_signals

            # ---- 计算每只股票的当前仓位比例（B3: 累计持仓上限用） ----

            position_ratios: Dict[str, float] = {}
            if total_assets > 0:
                for sym, shares in self.positions.items():
                    pos_val = shares * current_prices.get(sym, 0)
                    position_ratios[sym] = pos_val / total_assets

            # ---- C1: 计算年化波动率（最近20日收益率标准差×sqrt252）----
            import numpy as np
            annualized_vol = 0.0
            if len(self.daily_return_buffer) >= 5:
                annualized_vol = float(np.std(self.daily_return_buffer, ddof=1) * np.sqrt(252))

            # ---- 风控过滤（无 risk_manager=不过滤） ----
            if risk_manager is not None:
                filtered_signals = risk_manager.filter_signals(
                    combined_signals,
                    current_position_ratio=current_ratio,
                    current_positions=self.positions,
                    current_drawdown=current_drawdown,
                    current_position_ratios=position_ratios,
                    annualized_volatility=annualized_vol,
                    market_median_return=market_median_return,
                )

            else:
                filtered_signals = combined_signals

            # ---- 执行信号 ----
            for signal in filtered_signals:
                self._execute_signal(signal, current_prices, current_date)

            # ---- 记录每日快照 ----
            self._record_daily(current_date, current_prices, strategies_signals)

            # ---- C1: 更新波动率缓存 ----
            if len(self.daily_records) >= 2:
                prev_val = self.daily_records[-2].total_value
                curr_val = self.daily_records[-1].total_value
                daily_ret = (curr_val - prev_val) / prev_val if prev_val > 0 else 0
                self.daily_return_buffer.append(daily_ret)
                # 只保留最近20个交易日
                if len(self.daily_return_buffer) > 20:
                    self.daily_return_buffer.pop(0)

        # 计算最终年化波动率（用于报告）
        if len(self.daily_return_buffer) >= 5:
            import numpy as np
            self.final_volatility = float(np.std(self.daily_return_buffer, ddof=1) * np.sqrt(252))
        else:
            self.final_volatility = 0.0

        return PortfolioResult(self.daily_records, self.trades, self.initial_capital)

    # ────────── 信号执行 ──────────

    # ───────── 止损检查 ──────────

    def _check_stop_loss(self,
                         current_prices: Dict[str, float],
                         current_date: pd.Timestamp,
                         stop_loss_pct: float = 0.08,
                         trailing_pct: float = 0.15) -> List[Signal]:
        """Check all positions for stop loss & trailing stop triggers.

        双重保护机制：
        1. 固定止损（stop_loss）：从成本价计算亏损比例
        2. 移动止损（trailing_stop）：从持仓期间最高价回落比例

        两者任一触发都会生成卖出信号。
        trailing_pct 由调用方传入（来自 risk_manager.config），默认15%。
        """
        signals: List[Signal] = []

        for sym, shares in list(self.positions.items()):
            if shares <= 0 or sym not in current_prices:
                # 无持仓时清除 peak_prices 记录
                self.peak_prices.pop(sym, None)
                continue
            price = current_prices[sym]
            cost = self.cost_basis.get(sym, 0)

            # ---- 固定止损检查 ----
            if cost > 0:
                unrealized_pnl = (price - cost) / cost
                if unrealized_pnl < -stop_loss_pct:
                    self.stop_loss_triggered += 1
                    signals.append(Signal(
                        symbol=sym,
                        direction=-1,
                        weight=1.0,
                        price=price,
                        confidence=1.0,
                        strategy='risk_manager',
                        timestamp=current_date,
                    ))
                    # 已触发止损，跳过 trailing stop 检查（避免重复）
                    self.peak_prices.pop(sym, None)
                    continue

            # ---- 移动止损（Trailing Stop）检查 ----
            # 更新持仓期间最高价
            if sym not in self.peak_prices or price > self.peak_prices[sym]:
                self.peak_prices[sym] = price

            # 检查是否从最高价回落超过阈值
            peak = self.peak_prices[sym]
            if peak > 0:
                drawdown_from_peak = (peak - price) / peak
                if drawdown_from_peak >= trailing_pct:
                    self.trailing_stop_triggered += 1
                    signals.append(Signal(
                        symbol=sym,
                        direction=-1,
                        weight=1.0,
                        price=price,
                        confidence=1.0,
                        strategy='risk_manager_trailing',
                        timestamp=current_date,
                    ))
                    # 触发后清除峰值记录
                    self.peak_prices.pop(sym, None)

        return signals

    # --------------------------------------------
    #  执行交易
    # --------------------------------------------

    def _execute_signal(self, signal: Signal,
                        current_prices: Dict[str, float],
                        current_date: pd.Timestamp):
        sym = signal.symbol
        if sym not in current_prices or current_prices[sym] <= 0:
            return

        price = current_prices[sym]

        if signal.direction == 1:
            exec_price = price * (1 + self.slippage)
            allocated = self.capital * signal.weight
            max_qty_raw = allocated / (exec_price * (1 + self.commission))
            max_qty = int(max_qty_raw)
            # A股最小交易单位=1手=100股
            max_qty = (max_qty // 100) * 100
            if max_qty <= 0:
                return
            # 佣金（最低5元）
            raw_comm = exec_price * max_qty * self.commission
            cost = max(self.min_commission, raw_comm)
            total = exec_price * max_qty + cost
            if total <= self.capital:
                self.capital -= total
                self.positions[sym] = self.positions.get(sym, 0) + max_qty
                # 更新加权平均持仓成本（含佣金）
                old_shares = self.positions.get(sym, 0) - max_qty
                old_cost_total = self.cost_basis.get(sym, 0) * old_shares
                new_cost_total = total  # 含佣金的总买入成本
                total_shares = old_shares + max_qty
                if total_shares > 0:
                    self.cost_basis[sym] = (old_cost_total + new_cost_total) / total_shares
                self.trades.append(PortfolioTradeRecord(
                    date=current_date, symbol=sym,
                    direction=1, price=exec_price,
                    quantity=max_qty, value=exec_price * max_qty,
                    cost=cost, strategy=signal.strategy,
                ))

        elif signal.direction == -1 and self.positions.get(sym, 0) > 0:
            exec_price = price * (1 - self.slippage)
            sell_qty = int(self.positions[sym] * signal.weight)
            # A股最小交易单位=1手=100股
            sell_qty = (sell_qty // 100) * 100
            if sell_qty <= 0:
                return
            # 卖出佣金（最低5元）+ 印花税
            raw_comm = exec_price * sell_qty * self.commission
            cost = max(self.min_commission, raw_comm)
            tax = exec_price * sell_qty * self.stamp_tax
            self.capital += exec_price * sell_qty - cost - tax
            self.positions[sym] -= sell_qty
            # 完全平仓后清除成本记录
            if self.positions[sym] <= 0:
                self.cost_basis.pop(sym, None)
            self.trades.append(PortfolioTradeRecord(
                date=current_date, symbol=sym,
                direction=-1, price=exec_price,
                quantity=sell_qty, value=exec_price * sell_qty,
                cost=cost + tax, strategy=signal.strategy,
            ))

    # ────────── 每日记录 ──────────

    def _record_daily(self, current_date: pd.Timestamp,
                      current_prices: Dict[str, float],
                      strategies_signals: Dict[str, List[Signal]] = None):
        positions_snapshot = dict(self.positions)
        position_values = {}
        total_pv = 0.0
        for sym, shares in positions_snapshot.items():
            if shares > 0 and sym in current_prices:
                mv = shares * current_prices[sym]
                position_values[sym] = round(mv, 2)
                total_pv += mv

        contribs = {}
        if strategies_signals:
            for sname, sigs in strategies_signals.items():
                contribs[sname] = len(sigs)

        signal_count = sum(len(v) for v in strategies_signals.values()) if strategies_signals else 0

        self.daily_records.append(PortfolioDailyRecord(
            date=current_date,
            capital=round(self.capital, 2),
            total_value=round(self.capital + total_pv, 2),
            positions=positions_snapshot,
            position_values=position_values,
            signal_count=signal_count,
            strategy_contributions=contribs,
        ))

    # ────────── 辅助 ──────────

    @staticmethod
    def _is_last_trading_day(target_date: pd.Timestamp,
                             all_dates: List[pd.Timestamp]) -> bool:
        target_month = target_date.month
        target_year = target_date.year
        try:
            idx = all_dates.index(target_date)
        except ValueError:
            return False
        if idx + 1 >= len(all_dates):
            return True
        next_date = all_dates[idx + 1]
        return next_date.month != target_month or next_date.year != target_year

    def _reset(self):
        self.capital = self.initial_capital
        self.positions = {}
        self.cost_basis = {}
        self.stop_loss_triggered = 0
        self.trailing_stop_triggered = 0
        self.peak_prices = {}
        self.trades = []
        self.daily_records = []
        self.peak_value = self.initial_capital
