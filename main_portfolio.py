import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils.proxy import safe_clean_proxy; safe_clean_proxy()
from data.loader import DataLoader
from data.cleaner import clean_daily_data, check_data_quality
from config.settings import DEFAULT_SYMBOLS, BACKTEST_CONFIG, RISK_CONFIG
from config.strategy_config import TREND_FOLLOWING_CONFIG, MEAN_REVERSION_CONFIG, FACTOR_SELECTION_CONFIG
from strategies.trend_following.strategy import TrendFollowingStrategy
from strategies.mean_reversion.strategy import MeanReversionStrategy
from strategies.factor_rebalancer import FactorRebalancer
from portfolio.combiner import PortfolioCombiner
from portfolio.risk_manager import RiskManager
from portfolio.engine import PortfolioEngine
from backtest.walk_forward import WalkForwardValidator
from backtest.portfolio_reporter import PortfolioReporter
import warnings; warnings.filterwarnings('ignore')
