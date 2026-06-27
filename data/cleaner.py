"""Data cleaner"""
import pandas as pd
def clean_daily_data(df):
    df = df.copy()
    df = df[df['volume'] > 0]  # remove suspended
    df = df.dropna(subset=['open','high','low','close','volume'])
    return df
def check_data_quality(df, symbol):
    return {'total_days': len(df), 'date_range': f"{df.index[0].date()}~{df.index[-1].date()}"}
