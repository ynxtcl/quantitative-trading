"""
========================================
  RealTimeData — 实时数据（回放模式）
========================================

【定位】
在 mock 模式下，通过回放历史数据来模拟实时行情。
实盘模式下，由 XtQuantData 或其他数据源替换。

【工作模式】
1. 回放模式（mock）：逐日回放历史数据，每次调用返回下一天的行情
2. 实盘模式（live）：连接行情源，返回当前最新行情

【回放逻辑】
- 加载历史数据（复用 data/loader.py）
- 维护一个"当前日期"指针
- 每次 get_latest() 推进一天，返回当日数据
- 可以控制速度（实时/加速/跳跃）
"""
import time
from typing import Dict, Optional, List
from datetime import datetime, timedelta
import pandas as pd

from data.loader import DataLoader
from data.cleaner import clean_daily_data
from config.settings import DEFAULT_SYMBOLS, BACKTEST_CONFIG
from ops.logger import get_logger

log = get_logger('real_time_data')


class MarketDataReplay:
    """历史数据回放器 — 模拟实时行情推送"""

    def __init__(self, symbols: List[str] = None,
                 start_date: str = None, end_date: str = None,
                 speed: float = 1.0):
        self.symbols = symbols or DEFAULT_SYMBOLS[:3]
        self.start_date = start_date or BACKTEST_CONFIG['start_date']
        self.end_date = end_date or BACKTEST_CONFIG['end_date']
        self.speed = speed  # 速度倍率：1.0=实时, 10.0=10x加速, 0=瞬间完成

        # 内部状态
        self._data: Dict[str, pd.DataFrame] = {}      # symbol → OHLCV
        self._dates: List[pd.Timestamp] = []            # 所有交易日
        self._current_idx: int = 0                      # 当前日期索引
        self._is_loaded = False
        self._market_open = False
        self._current_date: Optional[pd.Timestamp] = None

    def load_data(self) -> bool:
        """加载并缓存历史数据"""
        loader = DataLoader()
        raw_data = loader.load_multiple(
            self.symbols,
            start=self.start_date,
            end=self.end_date
        )
        if not raw_data:
            log.error('历史数据加载失败')
            return False

        # 清洗数据
        for sym, df in raw_data.items():
            self._data[sym] = clean_daily_data(df)

        # 构建统一交易日历
        all_dates = set()
        for df in self._data.values():
            all_dates.update(df['date'].unique())
        self._dates = sorted(all_dates)

        self._is_loaded = True
        log.info(f'历史数据加载完成', symbols=len(self._data),
                 trading_days=len(self._dates),
                 date_range=f'{self._dates[0].date()}~{self._dates[-1].date()}')
        return True

    def get_current_date(self) -> Optional[pd.Timestamp]:
        """获取当前回放日期"""
        if self._current_idx < len(self._dates):
            return self._dates[self._current_idx]
        return None

    def get_latest(self) -> Dict[str, Dict]:
        """获取当前时刻的行情数据（模拟")

        返回格式: {symbol: {open, high, low, close, volume, amount, date}}
        """
        if not self._is_loaded:
            return {}

        if self._current_idx >= len(self._dates):
            log.info('回放结束')
            return {}

        current_date = self._dates[self._current_idx]
        self._current_date = current_date

        result = {}
        for sym in self.symbols:
            df = self._data.get(sym)
            if df is None:
                continue
            row = df[df['date'] == current_date]
            if row.empty:
                continue
            row = row.iloc[-1]
            result[sym] = {
                'open': float(row['open']),
                'high': float(row['high']),
                'low': float(row['low']),
                'close': float(row['close']),
                'volume': float(row['volume']),
                'amount': float(row.get('amount', 0)),
                'date': current_date,
            }

        return result

    def next_day(self) -> bool:
        """推进到下一天
        
        Returns:
            True 如果还有下一天, False 如果已结束
        """
        if self._current_idx >= len(self._dates) - 1:
            return False
        self._current_idx += 1

        # 速度控制
        if self.speed > 0 and self.speed < 100:
            time.sleep(1.0 / self.speed)

        return True

    def reset(self):
        """重置回放指针"""
        self._current_idx = 0
        log.info('回放已重置')

    def get_progress(self) -> float:
        """获取回放进度 (0.0 ~ 1.0)"""
        if not self._dates:
            return 0.0
        return self._current_idx / len(self._dates)

    def get_dates_snapshot(self) -> Dict:
        """获取当前快照信息"""
        return {
            'total_days': len(self._dates),
            'current_day': self._current_idx + 1,
            'current_date': str(self._dates[self._current_idx].date()) if self._current_idx < len(self._dates) else None,
            'progress': f'{self.get_progress():.1%}',
        }
