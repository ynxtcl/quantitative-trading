"""
========================================
 数据清洗 — 复权/去空值/去停牌
========================================

【为什么需要数据清洗？】
从 akshare 获取的原始数据并不"干净"：
1. 停牌日：成交量=0，价格不变，这些日期会扭曲指标计算
2. 空值：某些交易日可能缺失数据（节假日/系统故障）
3. 退市整理期：ST/*ST 股票的价格行为异常

【清洗原则】
- 宁可少数据，不要坏数据（false data > no data）
- 结构性缺失（停牌）直接删除
- 随机性缺失（个别字段null）用前向填充
"""

import pandas as pd
import numpy as np


def clean_daily_data(df: pd.DataFrame) -> pd.DataFrame:
    """
    清洗日K线数据

    执行步骤：
    1. 复制DataFrame（避免修改原始数据）
    2. 删除 open/high/low/close 为空的行
    3. 删除成交量为0的行（停牌日）
    4. 重置索引

    为什么不处理涨跌停？
    - 涨跌停日的价格是"堵住"的（想买/卖但无法成交）
    - 理论上应该排除，但实盘中可能出现连续涨停/跌停
    - 排除涨停日会过滤掉很多牛股信号，所以暂不处理
    """
    if df.empty:
        return df

    df = df.copy()
    df = df.dropna(subset=['open', 'high', 'low', 'close'])
    # dropna 的 subset 只检查关键列
    # volume/amount 有空值不会触发剔除
    # 为什么？因为策略逻辑主要依赖 OHLC，成交量为空可以视为停牌

    # 去停牌（成交量=0）
    df = df[df['volume'] > 0]

    df = df.reset_index(drop=True)
    return df


def check_data_quality(df: pd.DataFrame, symbol: str = "") -> dict:
    """
    数据质量检查

    返回内容包括：
    - total_days: 总交易日数
    - date_range: 数据覆盖的日期范围
    - missing_dates: 缺失的交易日数（理论交易日 - 实际交易日）
    - zero_volume_days: 零成交天数
    - null_values: 空值总数

    missing_dates 的意义：
    - A股实际交易天数每年约240-250天（非242/252的整数）
    - 如果 missing_dates 超过20天/年，说明数据源有问题
    """
    if df.empty:
        return {"symbol": symbol, "status": "空", "total": 0}

    report = {
        "symbol": symbol,
        "total_days": len(df),
        "date_range": f"{df['date'].min()} ~ {df['date'].max()}",
        "missing_dates": 0,
        "zero_volume_days": int((df['volume'] == 0).sum()),
        "null_values": int(df.isnull().sum().sum()),
    }

    # 检查缺失日期
    # 方法：生成完整的交易日历（pd.date_range freq='B'=Business Day）
    # 然后与实际数据日期做差集
    if 'date' in df.columns:
        full_range = pd.date_range(df['date'].min(), df['date'].max(), freq='B')
        missing = set(full_range) - set(pd.to_datetime(df['date']))
        report["missing_dates"] = len(missing)

    return report
