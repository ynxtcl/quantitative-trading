"""
调试脚本：验证 verify 模块修复后能否正常运行
"""
import sys, logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("debug_verify")

root = Path(__file__).resolve().parent
sys.path.insert(0, str(root))

import warnings
warnings.filterwarnings('ignore')

# ---- 1. 导入验证 ----
logger.info("=" * 50)
logger.info("步骤 1：导入 verify 模块 (修复后)")

try:
    from verify.rqalpha_runner import run_on_rqalpha, RQAlphaMetrics, build_config
    logger.info("  ✅ rqalpha_runner 导入成功")
    logger.info("     run_on_rqalpha ✅, RQAlphaMetrics ✅, build_config ✅")
except Exception as e:
    logger.error("  ❌ rqalpha_runner 导入失败: %s", e)
    raise

try:
    from verify.rqalpha_strategy import (
        make_trend_following_strategy,
        make_self_contained_strategy,
        calc_adx, calc_donchian_high, calc_donchian_low, calc_sma,
    )
    logger.info("  ✅ rqalpha_strategy 导入成功")
    logger.info("     make_self_contained_strategy ✅ (新增)")
except Exception as e:
    logger.error("  ❌ rqalpha_strategy 导入失败: %s", e)
    raise

try:
    from verify.compare import compare_single_stock, print_report, CompareRow
    logger.info("  ✅ compare 导入成功")
except Exception as e:
    logger.error("  ❌ compare 导入失败: %s", e)
    raise

# ---- 2. 指标计算验证 ----
logger.info("=" * 50)
logger.info("步骤 2：测试策略函数 ADX/通道计算")

import numpy as np
import pandas as pd
np.random.seed(42)
fake_high = np.random.uniform(10, 12, 100)
fake_low = np.random.uniform(8, 10, 100)
fake_close = np.random.uniform(9, 11, 100)

adx = calc_adx(fake_high, fake_low, fake_close, 14)
dc_high = calc_donchian_high(fake_high, 20)
dc_low = calc_donchian_low(fake_low, 10)
sma = calc_sma(fake_close, 60)
logger.info("  ADX(14) = %.2f, Donchian(%.2f, %.2f), SMA(60) = %.2f",
            adx, dc_high, dc_low, sma)
logger.info("  ✅ 指标计算正常")

# ---- 3. 构建策略函数 ----
logger.info("=" * 50)
logger.info("步骤 3：创建自包含策略 (数据直接传入)")

rq_symbol = "000333.XSHE"
dates = pd.date_range("2020-01-01", periods=300, freq="B")
fake_df = pd.DataFrame({
    "open": np.random.uniform(10, 12, 300),
    "high": np.random.uniform(11, 13, 300),
    "low": np.random.uniform(9, 11, 300),
    "close": np.random.uniform(10, 12, 300),
    "volume": np.random.randint(1000, 10000, 300),
}, index=dates)

init_func, handle_func = make_self_contained_strategy(
    symbol=rq_symbol,
    ohlcv=fake_df,
    entry_period=20,
    adx_threshold=20.0,
)
logger.info("  ✅ make_self_contained_strategy() 返回正常")

# ---- 4. 运行 rqalpha 回测 ----
logger.info("=" * 50)
logger.info("步骤 4：run_on_rqalpha (可能回退到手动回测)")

rq_metrics = run_on_rqalpha(
    ohlcv=fake_df,
    symbol=rq_symbol,
    start_date="2020-02-01",
    end_date="2020-12-31",
    strategy_funcs={"init": init_func, "handle_bar": handle_func},
    stock_capital=1_000_000,
)
logger.info("  年化: %.2f%% | 夏普: %.2f | 回撤: %.2f%% | 交易: %d 笔 | 胜率: %.1f%%",
            rq_metrics.annual_return * 100, rq_metrics.sharpe_ratio,
            rq_metrics.max_drawdown * 100, rq_metrics.total_trades,
            rq_metrics.win_rate * 100)
logger.info("  ✅ 回测完成")

# ---- 5. 加载真实数据测试 ----
logger.info("=" * 50)
logger.info("步骤 5：加载真实数据做完整回测")

from utils.proxy import safe_clean_proxy
safe_clean_proxy()

from data.loader import DataLoader
loader = DataLoader()

sym = "000333"
raw = loader.load_daily(sym, start="2020-01-01", end="2024-01-01")
if raw is not None and not raw.empty:
    from data.cleaner import clean_daily_data
    df = clean_daily_data(raw)
    if 'date' in df.columns:
        df.set_index('date', inplace=True)
    df.index = pd.to_datetime(df.index)
    logger.info("  ✅ 真实数据 %s 加载成功: %d 行, %s ~ %s",
                sym, len(df), df.index[0].strftime('%Y-%m-%d'), df.index[-1].strftime('%Y-%m-%d'))

    rq_sym = f"{sym}.XSHE"
    init2, handle2 = make_self_contained_strategy(
        symbol=rq_sym, ohlcv=df,
    )

    metrics2 = run_on_rqalpha(
        ohlcv=df,
        symbol=rq_sym,
        start_date="2021-01-01",
        end_date="2023-12-31",
        strategy_funcs={"init": init2, "handle_bar": handle2},
        stock_capital=1_000_000,
    )
    logger.info("  %s 真实回测: 年化 %.2f%% | 夏普 %.2f | 回撤 %.2f%% | 交易 %d",
                sym, metrics2.annual_return * 100, metrics2.sharpe_ratio,
                metrics2.max_drawdown * 100, metrics2.total_trades)
else:
    logger.warning("  ⚠️ 真实数据加载失败，跳过")

# ---- 6. 单股票 compare（如果数据 ok）---
logger.info("=" * 50)
logger.info("步骤 6：对比自研引擎 vs rqalpha")

from verify.compare import compare_single_stock
try:
    rows = compare_single_stock(loader, sym, start_date="2020-01-01", end_date="2023-12-31")
    if rows:
        print_report(rows)
    else:
        logger.info("  无对比结果（窗口条件不足）")
except Exception as e:
    logger.error("  对比失败: %s", e)
    import traceback; traceback.print_exc()

logger.info("=" * 50)
logger.info("调试完成")
