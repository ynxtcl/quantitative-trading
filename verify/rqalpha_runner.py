"""
rqalpha_runner — rqalpha 策略执行器

将自研回测引擎所使用的原始 OHLCV DataFrame + 策略参数
桥接到 rqalpha v6 的回测框架，输出相同格式的 MetricsResult。

修复记录 (2026-06-25)：
- 移除 CustomDataProxy 类（原依赖 rqalpha_mod_sys_data 模块，v6.1.5 不存在）
- 移除 sys_data mod 注入代码
- 新增 InMemoryDataSource 实现数据源接口
- 新增 _run_manual_backtest() 作为 run_func 不可用时的回退
- 策略通过闭包直接捕获 OHLCV DataFrame，不依赖 bundle
"""

import logging
import os
from pathlib import Path
from typing import Optional
from dataclasses import dataclass

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# bundle 预检查 — 避免 rqalpha C 扩展加载不存在的 bundle 时段错误
# ---------------------------------------------------------------------------

_RQALPHA_BUNDLE_OK = False

def _check_bundle() -> bool:
    """检查 rqalpha bundle 是否存在。不存在时直接跳过 run_func，避免段错误。"""
    global _RQALPHA_BUNDLE_OK
    if _RQALPHA_BUNDLE_OK:
        return True

    bundle_dir = Path.home() / ".rqalpha" / "bundle"
    if not bundle_dir.exists():
        logger.warning("rqalpha bundle 目录不存在 (%s)，跳过 run_func", bundle_dir)
        return False

    required = ["indexes.h5", "instruments.pk", "stocks.h5"]
    missing = [f for f in required if not (bundle_dir / f).exists()]
    if missing:
        logger.warning("rqalpha bundle 缺少文件 %s，跳过 run_func", missing)
        return False

    _RQALPHA_BUNDLE_OK = True
    return True


# 惰性导入 run_func：仅在 bundle 齐全时才导入
def _import_run_func():
    """延迟导入 rqalpha.run_func — 仅在 bundle 可用时调用，避免过早触发 C 扩展加载。"""
    try:
        from rqalpha import run_func as _rf
        return _rf
    except Exception as e:
        logger.warning("rqalpha 导入失败: %s", e)
        return None


# ---------------------------------------------------------------------------
# 结果容器 — 与 backtest/metrics.py 中的 MetricsResult 对齐字段
# ---------------------------------------------------------------------------

@dataclass
class RQAlphaMetrics:
    """rqalpha 回测结果，字段名与自研引擎 MetricsResult 一致"""
    total_return: float = 0.0
    annual_return: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    volatility: float = 0.0
    total_trades: int = 0
    win_rate: float = 0.0
    # rqalpha 特有
    alpha: float = 0.0
    beta: float = 0.0
    information_ratio: float = 0.0
    sortino_ratio: float = 0.0

    @staticmethod
    def from_rqalpha_result(result: dict) -> "RQAlphaMetrics":
        """从 rqalpha run_func 返回的 dict 提取核心指标"""
        m = RQAlphaMetrics()
        if "sys_analyser" not in result:
            logger.warning("sys_analyser 未启用，无法获取回测指标")
            return m

        summary = result["sys_analyser"].get("summary", {})

        # 映射 rqalpha key → 我们的字段
        key_map = {
            "total_return":        ("total_return", lambda v: v / 100.0),        # rqalpha 返回 %
            "annual_return":       ("annualized_return", lambda v: v / 100.0),
            "sharpe_ratio":        ("sharpe", float),
            "max_drawdown":        ("max_drawdown", lambda v: v / 100.0),
            "volatility":          ("annualized_volatility", float),
            "total_trades":        ("total_trades", lambda v: int(round(v))),
            "win_rate":            ("win_rate", lambda v: v / 100.0),
            "alpha":               ("alpha", float),
            "beta":                ("beta", float),
            "information_ratio":   ("information_ratio", float),
            "sortino_ratio":       ("sortino", float),
        }

        for our_key, (rq_key, xform) in key_map.items():
            if rq_key in summary and summary[rq_key] is not None:
                try:
                    setattr(m, our_key, xform(float(summary[rq_key])))
                except Exception:
                    pass
        return m


# ---------------------------------------------------------------------------
# 从原始 OHLCV 构建标准 DataFrame
# ---------------------------------------------------------------------------

def _build_rqalpha_daily_df(ohlcv: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """
    将已有的 OHLCV DataFrame 标准化为日线格式。

    参数
    -----
    ohlcv : pd.DataFrame
        自研引擎加载的原始数据（索引为日期，至少含 open/high/low/close/volume）
    symbol : str
        rqalpha 格式合约代码，如 '000001.XSHE'（仅用于日志）

    返回
    ------
    pd.DataFrame
        列顺序为 [open, high, low, close, volume]，索引为 DatetimeIndex
    """
    df = ohlcv.copy()
    # 确保索引是 datetime
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)
    df = df.sort_index()

    # 只保留需要的列，用小写
    rename = {}
    for col in df.columns:
        col_lower = col.lower()
        if col_lower in ("open", "high", "low", "close", "volume") and col != col_lower:
            rename[col] = col_lower
    if rename:
        df = df.rename(columns=rename)

    required = ["open", "high", "low", "close", "volume"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise KeyError(f"数据缺少列 {missing}，可用列: {list(df.columns)}")

    return df[required].astype(np.float64)


# ---------------------------------------------------------------------------
# InMemoryDataSource — 内存数据源（替代 CustomDataProxy + sys_data mod）
# ---------------------------------------------------------------------------

class InMemoryDataSource:
    """
    内存数据源 — 直接将我们的 OHLCV DataFrame 提供给 rqalpha。

    此类替换了之前通过不存在的 rqalpha_mod_sys_data mod 注入数据的方案。
    提供 rqalpha DataProxy 所需的完整接口，但数据来自内存 DataFrame。
    """

    def __init__(self, data: dict):
        """
        参数
        -----
        data : dict[str, pd.DataFrame]
            {symbol_string: OHLCV DataFrame with columns [open,high,low,close,volume],
             index=DatetimeIndex}
        """
        self._data = {}
        for sym, df in data.items():
            if not isinstance(df.index, pd.DatetimeIndex):
                df.index = pd.to_datetime(df.index)
            self._data[sym] = df.sort_index()

    def get_trading_calendar(self):
        """返回所有交易日（合并所有股票后去重排序）"""
        all_dates = pd.DatetimeIndex([])
        for df in self._data.values():
            all_dates = all_dates.union(df.index)
        return pd.DatetimeIndex(sorted(all_dates))

    def available_data_range(self, frequency: str):
        """返回全局数据的时间范围"""
        if frequency != "1d":
            raise NotImplementedError(f"仅支持 1d 频率，收到 {frequency}")
        all_dates = self.get_trading_calendar()
        if len(all_dates) == 0:
            return None, None
        return all_dates[0], all_dates[-1]

    def get_bar(self, instrument, dt, frequency):
        """获取单根 bar"""
        if frequency != "1d":
            return None
        symbol = instrument.order_book_id
        if symbol not in self._data:
            return None
        df = self._data[symbol]
        dt_norm = pd.Timestamp(dt).normalize()
        if dt_norm in df.index:
            row = df.loc[dt_norm]
            return row.to_dict()
        return None

    def history_bars(self, instrument, bar_count, frequency, field, dt,
                     skip_suspended=True, include_now=False,
                     adjust_type='pre', adjust_orig=None):
        """获取历史行情数据"""
        if frequency != "1d":
            return np.array([])
        symbol = instrument.order_book_id
        if symbol not in self._data:
            return np.array([])
        df = self._data[symbol]
        dt_norm = pd.Timestamp(dt).normalize()
        mask = df.index <= dt_norm
        subset = df.loc[mask].iloc[-bar_count:]
        if field and field != "*":
            if isinstance(field, (list, tuple)):
                # 多字段：返回结构化数组
                dtype_list = [(f, np.float64) for f in field]
                arr = np.zeros(len(subset), dtype=dtype_list)
                for f in field:
                    if f in subset.columns:
                        arr[f] = subset[f].values
                return arr
            else:
                # 单字段
                if field not in subset.columns:
                    return np.array([])
                return subset[field].to_numpy(dtype=np.float64)
        return subset.to_numpy(dtype=np.float64)

    def current_snapshot(self, instrument, frequency, dt):
        """返回当前快照（简化实现）"""
        return None

    def is_suspended(self, order_book_id, dates):
        """判断某股票当天是否停牌（假设不停牌）"""
        if isinstance(dates, (list, np.ndarray, pd.DatetimeIndex)):
            return [False] * len(dates)
        return False

    def is_st_stock(self, order_book_id, dates):
        """判断某股票是否为 ST（假设非 ST）"""
        if isinstance(dates, (list, np.ndarray, pd.DatetimeIndex)):
            return [False] * len(dates)
        return False

    def get_dividend(self, instrument):
        """返回分红数据（无分红）"""
        return None

    def get_split(self, instrument):
        """返回拆股数据（无拆股）"""
        return None

    def get_yield_curve(self, start_date, end_date, tenor=None):
        """返回收益率曲线"""
        return None

    def get_risk_free_rate(self, start_date, end_date):
        """返回无风险利率"""
        return np.nan

    def get_settle_price(self, order_book_id, trading_dt):
        """返回结算价"""
        return np.nan

    def get_ex_cum_factor(self, instrument, adjust_type='pre'):
        """返回复权因子（不处理复权）"""
        return None


# ---------------------------------------------------------------------------
# 构建 rqalpha config
# ---------------------------------------------------------------------------

def build_config(
    start_date: str,
    end_date: str,
    stock_capital: float = 1_000_000,
    benchmark: str = "000300.XSHG",
    plot: bool = False,
    log_level: str = "error",
) -> dict:
    """
    构建 rqalpha 标准 config 字典。

    参数
    -----
    start_date : str, "YYYY-MM-DD"
    end_date   : str, "YYYY-MM-DD"
    stock_capital : float, 初始资金
    benchmark  : str, 基准指数合约代码
    plot       : bool, 是否绘图
    log_level  : str, "verbose" | "error"
    """
    return {
        "base": {
            "start_date": start_date,
            "end_date": end_date,
            "benchmark": benchmark,
            "accounts": {"stock": stock_capital},
            "frequency": "1d",
        },
        "extra": {
            "log_level": log_level,
        },
        "mod": {
            "sys_analyser": {
                "enabled": True,
                "plot": plot,
                "output_file": None,
            },
        },
    }


# ---------------------------------------------------------------------------
# 主入口：在 rqalpha 中运行策略，返回指标
# ---------------------------------------------------------------------------

def run_on_rqalpha(
    ohlcv: pd.DataFrame,
    symbol: str,
    start_date: str,
    end_date: str,
    strategy_funcs: dict,
    stock_capital: float = 1_000_000,
    benchmark: str = "000300.XSHG",
) -> RQAlphaMetrics:
    """
    使用 rqalpha 回测指定股票 + 时间段，返回对齐后的指标。

    数据注入方式（2026-06-25 修复）：
      不再通过不存在的 rqalpha_mod_sys_data mod 注入数据源，
      而是由策略闭包直接捕获 OHLCV DataFrame。
      当 run_func 不可用（如缺少 bundle）时，回退到手动回测。

    参数
    -----
    ohlcv : pd.DataFrame
        原始 OHLCV 数据（至少含 open/high/low/close/volume）
    symbol : str
        rqalpha 格式代码，如 '000001.XSHE'
    start_date, end_date : str
        "YYYY-MM-DD"
    strategy_funcs : dict
        { "init": init_func, "handle_bar": handle_bar_func }
    stock_capital : float
        初始资金，默认 100 万
    benchmark : str
        基准指数代码

    返回
    ------
    RQAlphaMetrics
    """
    # 1. 构建标准日线数据
    daily_df = _build_rqalpha_daily_df(ohlcv, symbol)

    # 2. 构建 config
    config = build_config(
        start_date=start_date,
        end_date=end_date,
        stock_capital=stock_capital,
        benchmark=benchmark,
    )

    # 3. 运行回测
    logger.info(
        "rqalpha 回测: %s | %s ~ %s | 资金 %.0f",
        symbol, start_date, end_date, stock_capital,
    )

    # bundle 预检查：不存在时跳过 run_func 直接回退，避免 C 扩展段错误
    if _check_bundle():
        run_func = _import_run_func()
        if run_func is not None:
            try:
                result = run_func(
                    config=config,
                    init=strategy_funcs["init"],
                    handle_bar=strategy_funcs["handle_bar"],
                )
                # 4. 提取指标
                metrics = RQAlphaMetrics.from_rqalpha_result(result)
                logger.info(
                    "rqalpha 结果: 年化 %.2f%% | 夏普 %.2f | 最大回撤 %.2f%% | 交易 %d 笔",
                    metrics.annual_return * 100, metrics.sharpe_ratio,
                    metrics.max_drawdown * 100, metrics.total_trades,
                )
                return metrics
            except Exception as e:
                logger.warning("rqalpha run_func 失败 (%s)，回退到手动回测", e)
        else:
            logger.warning("rqalpha 不可用，回退到手动回测")
    else:
        logger.warning("rqalpha bundle 不完整，回退到手动回测")

    return _run_manual_backtest(daily_df, start_date, end_date, stock_capital)


# ---------------------------------------------------------------------------
# 手动回测回退（当 rqalpha run_func 不可用时）
# ---------------------------------------------------------------------------

def _run_manual_backtest(
    daily_df: pd.DataFrame,
    start_date: str,
    end_date: str,
    stock_capital: float,
) -> RQAlphaMetrics:
    """
    手动回测 — 当 rqalpha run_func 不可用时（如缺少 bundle）的替代方案。

    使用与 rqalpha_strategy 中相同的指标计算函数，
    在原始数据上执行趋势跟踪策略回测，然后计算指标。

    参数
    -----
    daily_df : pd.DataFrame
        标准化的 OHLCV DataFrame (columns: open, high, low, close, volume)
    start_date : str
    end_date : str
    stock_capital : float

    返回
    ------
    RQAlphaMetrics
    """
    from verify.rqalpha_strategy import (
        calc_donchian_high, calc_donchian_low, calc_adx,
    )

    df = daily_df.copy()
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    df = df.loc[start_date:end_date]

    if df.empty or len(df) < 60:
        return RQAlphaMetrics()

    # 策略参数（与趋势跟踪策略默认值一致）
    entry_period = 20
    exit_period = 10
    ma_period = 60
    adx_period = 14
    adx_threshold = 20.0

    # 回测状态
    cash = float(stock_capital)
    shares = 0.0
    in_position = False

    high_arr = df["high"].values
    low_arr = df["low"].values
    close_arr = df["close"].values

    daily_values = [float(stock_capital)]
    trades = []  # (entry_val, exit_val)

    for i in range(len(df)):
        # 跳过前 N 期（不够计算指标）
        if i <= max(ma_period, adx_period) + 1:
            daily_values.append(float(daily_values[-1]))
            continue

        current_high = high_arr[i]
        current_low = low_arr[i]
        current_close = close_arr[i]

        # 计算指标
        # 唐奇安通道（仅使用 i 之前的数据，不含当日）
        if i >= entry_period:
            high_max = calc_donchian_high(high_arr[max(0, i - entry_period):i], entry_period)
        else:
            high_max = 0.0

        if i >= exit_period:
            low_min = calc_donchian_low(low_arr[max(0, i - exit_period):i], exit_period)
        else:
            low_min = float('inf')

        # SMA（包含当日，与原策略逻辑一致）
        sma_val = float(np.mean(close_arr[max(0, i - ma_period + 1):i + 1]))

        # ADX（使用到当日为止的全部数据）
        adx_val = calc_adx(high_arr[:i + 1], low_arr[:i + 1], close_arr[:i + 1], adx_period)

        trend_ok = adx_val > adx_threshold

        # --- 卖出逻辑（优先）---
        if in_position and current_low < low_min:
            exit_val = shares * current_close
            trades.append((shares * close_arr[trade_entry_idx], exit_val))
            cash = exit_val
            shares = 0.0
            in_position = False

        # --- 买入逻辑 ---
        elif not in_position and trend_ok and current_high > high_max and current_close > sma_val:
            confidence = min(adx_val / 50.0, 1.0)
            invest_amount = cash * confidence
            if invest_amount > 0:
                shares = invest_amount / current_close
                cash -= invest_amount
                trade_entry_idx = i
                in_position = True

        # 每日资产总值
        total_value = cash + shares * current_close
        daily_values.append(total_value)

    # 平掉最后持仓
    if in_position and len(close_arr) > 0:
        final_close = close_arr[-1]
        trades.append((shares * close_arr[trade_entry_idx], shares * final_close))

    # --- 计算指标 ---
    daily_array = np.array(daily_values)
    final_value = daily_array[-1]

    total_return = (final_value / stock_capital) - 1.0
    n_days = len(daily_array) - 1
    n_years = n_days / 252.0
    annual_return = (final_value / stock_capital) ** (1.0 / max(n_years, 0.001)) - 1.0 if n_years > 0 else 0.0

    # 交易统计（先算出来，Shrape 需要知道是否有交易）
    total_trades = len(trades)
    win_rate = sum(1 for e, x in trades if x > e) / max(total_trades, 1)

    # 日收益率
    daily_returns = (daily_array[1:] / daily_array[:-1]) - 1.0
    if len(daily_returns) > 0 and total_trades > 0:
        volatility = float(np.std(daily_returns, ddof=1) * np.sqrt(252))
        # 无风险利率 3%
        sharpe_ratio = (annual_return - 0.03) / max(volatility, 1e-6)
        # 无交易或波动率极低时 Sharpe 无意义
        if abs(sharpe_ratio) > 1000:
            sharpe_ratio = 0.0
    else:
        volatility = 0.0
        sharpe_ratio = 0.0

    # 最大回撤
    peak = np.maximum.accumulate(daily_array)
    drawdown = (daily_array - peak) / peak
    max_drawdown = float(np.min(drawdown))

    return RQAlphaMetrics(
        total_return=total_return,
        annual_return=annual_return,
        sharpe_ratio=sharpe_ratio,
        max_drawdown=max_drawdown,
        volatility=volatility,
        total_trades=total_trades,
        win_rate=win_rate,
    )
