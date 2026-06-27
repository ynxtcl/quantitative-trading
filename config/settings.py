"""Global config"""
import os
from pathlib import Path
PROJECT_ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_SYMBOLS = ["000001","000333","000858","002415","300750"]
BACKTEST_CONFIG = {"initial_capital": 100000.0, "commission": 0.0003, "stamp_tax": 0.001, "slippage": 0.001, "freq": "1d", "start_date": "2020-01-01", "end_date": "2025-01-01"}
RISK_CONFIG = {"max_single_weight": 0.30, "max_total_position": 0.95, "stop_loss": 0.08, "max_daily_loss": 0.03, "max_daily_symbols": 5, "max_drawdown": 0.25, "enforce_stop_loss": True, "vol_adaptive": True, "vol_low": 0.15, "vol_high": 0.40, "max_industry_weight": 0.50}
INDUSTRY_MAP = {"000001":"Bank","000333":"Appliance","000858":"Liquor","002415":"Tech","300750":"New Energy"}
