"""Metrics computation"""
import numpy as np

def compute_metrics(df, initial_capital):
    ret = df['net_ret'] if 'net_ret' in df else df['daily_ret']
    total_ret = (1 + ret).prod() - 1
    n_years = len(df) / 252
    annual_ret = (1 + total_ret) ** (1 / n_years) - 1 if n_years > 0 else 0
    sharpe = np.sqrt(252) * ret.mean() / ret.std() if ret.std() > 0 else 0
    cummax = (1 + ret).cumprod().cummax()
    drawdown = ((1 + ret).cumprod() - cummax) / cummax
    max_dd = drawdown.min()
    win_rate = len(ret[ret > 0]) / len(ret[ret != 0]) if len(ret[ret != 0]) > 0 else 0
    return {'total_return': total_ret, 'annual_return': annual_ret, 'sharpe_ratio': sharpe, 'max_drawdown': max_dd, 'volatility': ret.std() * np.sqrt(252), 'win_rate': win_rate}
