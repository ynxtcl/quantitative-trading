"""Data loader"""
from pathlib import Path
import pandas as pd
class DataLoader:
    def __init__(self):
        self.cache_dir = Path("data_storage/cache"); self.cache_dir.mkdir(parents=True, exist_ok=True)
    def load_multiple(self, symbols, start, end):
        result = {}
        for sym in symbols:
            try:
                cache = self.cache_dir / f"{sym}_daily.parquet"
                if cache.exists():
                    df = pd.read_parquet(cache)
                else:
                    import akshare as ak
                    df = ak.stock_zh_a_hist(sym, "daily", start, end, adjust="qfq")
                    df.to_parquet(cache)
                df.columns = [c.lower() for c in df.columns]
                if '日期' in df.columns: df = df.rename(columns={'日期':'date','开盘':'open','收盘':'close','最高':'high','最低':'low','成交量':'volume'})
                df['date'] = pd.to_datetime(df['date'])
                df = df.set_index('date').sort_index()
                result[sym] = df
            except Exception as e:
                print(f"  {sym}: load failed - {e}")
        return result
