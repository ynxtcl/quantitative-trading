"""快速测试新权重组合效果（跳过WF验证）"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.proxy import safe_clean_proxy; safe_clean_proxy()
from data.loader import DataLoader
from data.cleaner import clean_daily_data
from config.settings import DEFAULT_SYMBOLS, BACKTEST_CONFIG, RISK_CONFIG
from config.strategy_config import TREND_FOLLOWING_CONFIG as TFC
from config.strategy_config import MEAN_REVERSION_CONFIG as MRC
from config.strategy_config import FACTOR_SELECTION_CONFIG as FSC
from strategies.trend_following.strategy import TrendFollowingStrategy
from strategies.mean_reversion.strategy import MeanReversionStrategy
from strategies.factor_rebalancer import FactorRebalancer
from portfolio.combiner import PortfolioCombiner
from portfolio.risk_manager import RiskManager
from portfolio.engine import PortfolioEngine

# 加载
loader = DataLoader()
data_dict = loader.load_multiple(DEFAULT_SYMBOLS[:3], start='2020-01-01', end='2025-01-01')
cleaned = {sym: clean_daily_data(df) for sym, df in data_dict.items()}

# 策略
tf = {}
mr = {}
for sym in DEFAULT_SYMBOLS[:3]:
    c = dict(TFC); c['symbol'] = sym
    tf[sym] = TrendFollowingStrategy('trend_following', c)
    c = dict(MRC); c['symbol'] = sym
    mr[sym] = MeanReversionStrategy('mean_reversion', c)

rebalancer = FactorRebalancer(FSC)
combiner = PortfolioCombiner()
combiner.set_weights({'trend_following': TFC['weight'], 'mean_reversion': MRC['weight'], 'factor_selection': FSC['weight']})

# 跑
engine = PortfolioEngine(BACKTEST_CONFIG)
result = engine.run(cleaned, tf, mr, rebalancer, combiner, RiskManager(dict(RISK_CONFIG)))

# 输出
print(f"权重: TF={TFC['weight']:.0%} / MR={MRC['weight']:.0%} / FS={FSC['weight']:.0%}")
print(f"最终: {result.final_value():,.2f}")
print(f"总收益: {result.total_return():+.2%}")
print(f"年化: {result.annual_return():+.2%}")
print(f"交易: {len(result.trades)}笔")
print(f"净值天数: {len(result.daily_records)}")

# 计算最大回撤
vals = [r.total_value for r in result.daily_records]
peak = vals[0]
max_dd = 0
for v in vals:
    if v > peak: peak = v
    dd = (peak - v) / peak
    if dd > max_dd: max_dd = dd
print(f"最大回撤: {max_dd:.2%}")
