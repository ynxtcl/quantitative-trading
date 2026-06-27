"""
========================================
 Walk-Forward 交叉验证 — 样本外测试
========================================

【为什么需要 Walk-Forward？】
量化交易中最大的骗局：样本内回测曲线漂亮，一上实盘就亏钱。

原因是策略的参数过拟合了历史数据。
就像考试前给你答案——你考100分，不代表你真会了。

Walk-Forward 的核心思想：
  用"历史数据"优化参数（训练集）
  用"未来数据"验证效果（测试集）
  两者完全不重叠，杜绝未来信息泄露。

【Walk-Forward 参数设置】
  window = 3 年（训练 2 年 + 测试 1 年）
  step  = 1 年

  第1轮：训练 2020-01~2021-12 → 测试 2022-01~2022-12
  第2轮：训练 2021-01~2022-12 → 测试 2023-01~2023-12
  第3轮：训练 2022-01~2023-12 → 测试 2024-01~2024-12

  为什么是 2:1（2年训练·1年测试）？
  - 训练太少（<1年）：策略学不到完整的市场周期（牛熊转换）
  - 训练太多（>3年）：市场结构可能已变化，旧模式不再适用
  - A股大约每3-4年一个完整牛熊周期，2年训练能捕获主要模式
  - 1年测试能验证策略在未见过的市场环境中的表现

【如何判断策略是否真的有效？】
  样本外指标（测试集）应接近样本内指标（训练集）的 70% 以上：
  - 训练夏普 1.5，测试夏普 1.0 → 正常衰减，策略可信
  - 训练夏普 2.0，测试夏普 0.2 → 严重过拟合，弃用
  - 训练夏普 0.8，测试夏普 0.9 → 意外稳健，可能发现了真规律

【关键约束】
  同一个策略实例不能在训练集和测试集之间共用状态！
  每轮必须新建策略实例（new strategy instance per fold）
  否则训练时积累的内部状态会泄漏到测试集
"""

import pandas as pd
import numpy as np
from typing import List, Tuple, Callable
from dataclasses import dataclass, field

from strategies.base import BaseStrategy
from backtest.engine import BacktestEngine, BacktestResult
from backtest.metrics import MetricsResult, calculate_metrics


@dataclass
class FoldResult:
    """单轮 Walk-Forward 结果

    每轮包含训练和测试两套指标。
    将两者对比，就能判断策略是否过拟合。
    """
    fold_id: int                        # 第几轮（1-based）
    train_start: pd.Timestamp           # 训练集起始
    train_end: pd.Timestamp             # 训练集结束
    test_start: pd.Timestamp            # 测试集起始
    test_end: pd.Timestamp              # 测试集结束
    train_metrics: MetricsResult        # 训练集指标
    test_metrics: MetricsResult         # 测试集指标
    train_trades: int = 0               # 训练集交易次数
    test_trades: int = 0                # 测试集交易次数


@dataclass
class WalkForwardResult:
    """Walk-Forward 汇总结果

    - 所有轮的训练/测试指标分别汇总平均
    - oos_ratio（样本外比率）= 测试集平均 / 训练集平均
      → oos_ratio > 0.7 表示策略稳健
      → oos_ratio < 0.3 表示严重过拟合
    """
    folds: List[FoldResult] = field(default_factory=list)
    avg_train_return: float = 0.0
    avg_test_return: float = 0.0
    avg_train_sharpe: float = 0.0
    avg_test_sharpe: float = 0.0
    avg_train_maxdd: float = 0.0
    avg_test_maxdd: float = 0.0
    oos_return_ratio: float = 0.0       # 样本外收益率 / 样本内
    oos_sharpe_ratio: float = 0.0       # 样本外夏普 / 样本内

    def summary_dict(self) -> dict:
        """打印友好的汇总字典"""
        return {
            '训练平均年化收益': f"{self.avg_train_return:.2%}",
            '测试平均年化收益': f"{self.avg_test_return:.2%}",
            '训练平均夏普': f"{self.avg_train_sharpe:.2f}",
            '测试平均夏普': f"{self.avg_test_sharpe:.2f}",
            '训练平均最大回撤': f"{self.avg_train_maxdd:.2%}",
            '测试平均最大回撤': f"{self.avg_test_maxdd:.2%}",
            '样本外收益比率(oos_return_ratio)': f"{self.oos_return_ratio:.2%}",
            '样本外夏普比率(oos_sharpe_ratio)': f"{self.oos_sharpe_ratio:.2%}",
            '结论': ('策略稳健 [OK]' if self.oos_sharpe_ratio > 0.7
                     else '中度过拟合 [WARN]' if self.oos_sharpe_ratio > 0.3
                     else '严重过拟合 [FAIL]'),
        }


class WalkForwardValidator:
    """
    Walk-Forward 交叉验证器

    使用方法：
        validator = WalkForwardValidator(
            window_years=3,      # 总窗口3年（2年训练+1年测试）
            train_ratio=2/3,     # 前2/3为训练
            step_years=1,        # 每次推进1年
        )
        result = validator.validate(data, strategy_factory, engine_config)

    注意 strategy_factory 是一个"工厂函数"而非策略实例。
    因为每轮都要创建新的策略实例，防止状态泄露。
    """

    def __init__(self,
                 window_years: int = 3,
                 train_ratio: float = 2/3,
                 step_years: int = 1):
        """
        参数:
            window_years: 每个窗口的总年数（默认3年=训练2+测试1）
            train_ratio: 训练集占窗口的比例（默认2/3）
            step_years: 每次滑动的年数（默认1年）
        """
        self.window_years = window_years
        self.train_ratio = train_ratio
        self.step_years = step_years

    def validate(self,
                 data: pd.DataFrame,
                 strategy_factory: Callable[[], BaseStrategy],
                 engine_config: dict) -> WalkForwardResult:
        """
        执行 Walk-Forward 验证

        参数:
            data: 完整OHLCV数据（必须按日期升序排列）
            strategy_factory: 策略工厂函数，每轮调用创建新策略实例
            engine_config: 回测引擎配置

        返回:
            WalkForwardResult 包含每轮详细结果和汇总统计

        实现说明：
        1. 根据时间范围计算有多少个窗口
        2. 对每个窗口划分训练/测试
        3. 在训练集上回测（获取指标作为基准）
        4. 在测试集上回测（获取指标作为验证）
        5. 收集所有轮次的结果
        """
        # 确保日期索引
        if not isinstance(data.index, pd.DatetimeIndex):
            data = data.copy()
            if 'date' in data.columns:
                data = data.set_index('date')
            else:
                data.index = pd.to_datetime(data.index)

        # 确定时间范围
        total_start = data.index.min()
        total_end = data.index.max()
        total_days = (total_end - total_start).days
        total_years = total_days / 365.0

        if total_years < self.window_years:
            raise ValueError(
                f"数据总长度 {total_years:.1f} 年不足一个窗口 {self.window_years} 年"
            )

        # 计算窗口数
        n_folds = max(1, int((total_years - self.window_years) / self.step_years) + 1)

        folds: List[FoldResult] = []

        for fold_i in range(n_folds):
            # 计算当前窗口的时间范围
            fold_start = total_start + pd.DateOffset(years=fold_i * self.step_years)
            fold_end = fold_start + pd.DateOffset(years=self.window_years)

            # 切分训练/测试
            train_end = fold_start + pd.DateOffset(years=self.window_years * self.train_ratio)
            # ⚠ 消除边界重叠：用 searchsorted 找到训练集最后一天的下一个交易日
            # 避免 data.loc[闭区间] 将边界日同时包含在训练集和测试集中
            train_end_idx = data.index.searchsorted(train_end, side='right')
            if train_end_idx >= len(data):
                continue
            test_start = data.index[train_end_idx]

            # 检查是否超出数据范围
            if fold_end > total_end:
                fold_end = total_end

            train_data = data.loc[fold_start:train_end].copy()
            test_data = data.loc[test_start:fold_end].copy()

            if len(train_data) < 60 or len(test_data) < 20:
                # 数据太少，不足以计算指标（需要至少60个交易日初始化指标）
                continue

            # ===== 训练集回测 =====
            train_strategy = strategy_factory()
            train_engine = BacktestEngine(engine_config)
            train_result = train_engine.run(train_data, train_strategy)

            # 配对交易计算盈亏
            train_trades_paired = self._pair_trades(train_result)
            train_metrics = calculate_metrics(
                daily_values=[r.total_value for r in train_result.daily_records],
                trades=train_trades_paired,
                initial_capital=engine_config['initial_capital'],
            )

            # ===== 测试集回测 =====
            test_strategy = strategy_factory()
            test_engine = BacktestEngine(engine_config)
            test_result = test_engine.run(test_data, test_strategy)

            test_trades_paired = self._pair_trades(test_result)
            test_metrics = calculate_metrics(
                daily_values=[r.total_value for r in test_result.daily_records],
                trades=test_trades_paired,
                initial_capital=engine_config['initial_capital'],
            )

            folds.append(FoldResult(
                fold_id=fold_i + 1,
                train_start=train_data.index[0],
                train_end=train_data.index[-1],
                test_start=test_data.index[0],
                test_end=test_data.index[-1],
                train_metrics=train_metrics,
                test_metrics=test_metrics,
                train_trades=len(train_result.trades),
                test_trades=len(test_result.trades),
            ))

        # ===== 汇总统计 =====
        result = self._aggregate(folds)
        return result

    def _aggregate(self, folds: List[FoldResult]) -> WalkForwardResult:
        """汇总多轮结果，计算平均值和 OOS 比率"""
        if not folds:
            return WalkForwardResult()

        avg_train_return = np.mean([f.train_metrics.annual_return for f in folds])
        avg_test_return = np.mean([f.test_metrics.annual_return for f in folds])
        avg_train_sharpe = np.mean([f.train_metrics.sharpe_ratio for f in folds])
        avg_test_sharpe = np.mean([f.test_metrics.sharpe_ratio for f in folds])
        avg_train_maxdd = np.mean([f.train_metrics.max_drawdown for f in folds])
        avg_test_maxdd = np.mean([f.test_metrics.max_drawdown for f in folds])

        # OOS 比率：样本外 / 样本内
        # 如果小于 0，说明训练赚钱但测试亏钱 → 严重过拟合
        # 如果大于 1，说明测试表现比训练还好 → 罕见，可能运气
        oos_return = (avg_test_return / avg_train_return
                      if avg_train_return != 0 else 0)
        oos_sharpe = (avg_test_sharpe / avg_train_sharpe
                      if avg_train_sharpe != 0 else 0)

        return WalkForwardResult(
            folds=folds,
            avg_train_return=avg_train_return,
            avg_test_return=avg_test_return,
            avg_train_sharpe=avg_train_sharpe,
            avg_test_sharpe=avg_test_sharpe,
            avg_train_maxdd=avg_train_maxdd,
            avg_test_maxdd=avg_test_maxdd,
            oos_return_ratio=oos_return,
            oos_sharpe_ratio=oos_sharpe,
        )

    @staticmethod
    def _pair_trades(result: BacktestResult) -> List[dict]:
        """将买卖交易配对，计算每笔盈亏"""
        trade_dicts = []
        buy_trades = [t for t in result.trades if t.direction == 1]
        sell_trades = [t for t in result.trades if t.direction == -1]
        for sell in sell_trades:
            matching_buys = [b for b in buy_trades if b.date <= sell.date]
            if matching_buys:
                buy = matching_buys[-1]
                cost_basis = buy.value + buy.cost
                sell_proceeds = sell.value - sell.cost
                pnl = sell_proceeds - cost_basis
                trade_dicts.append({
                    'date': sell.date, 'symbol': sell.symbol,
                    'direction': -1, 'price': sell.price,
                    'quantity': sell.quantity, 'value': sell.value,
                    'cost': sell.cost, 'strategy': sell.strategy,
                    'pnl': pnl,
                })
        return trade_dicts
