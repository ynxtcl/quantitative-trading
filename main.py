import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warnings; warnings.filterwarnings('ignore')
from utils.proxy import safe_clean_proxy; safe_clean_proxy()
from data.loader import DataLoader
from data.cleaner import clean_daily_data, check_data_quality
from strategies.trend_following.strategy import TrendFollowingStrategy
from config.settings import DEFAULT_SYMBOLS, BACKTEST_CONFIG
from config.strategy_config import TREND_FOLLOWING_CONFIG
from backtest.walk_forward import WalkForwardValidator, FoldResult

# Read from main.py - full content at:
# https://github.com/ynxtcl/quantitative-trading
