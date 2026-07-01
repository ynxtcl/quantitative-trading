# Backtest Test Log

| Run | Description | Ann.Ret | Sharpe | MaxDD | Trades | Final | Risk Triggers |
|:---:|:---|:---:|:---:|:---:|:---:|:---:|:---|
| R001 | default | 4.08% | 0.22 | -34.17% | 236 | 121,213 | Breaker382, Clip252, SL659 |

---
### R001: default

**Time**: 2026-06-27T20:09:20.828970  
**Symbols**: 000001, 000333, 000858  
**Notes**: 

| Metric | Value |
|:---|---:|
| Total Return | 21.21% |
| Annual Return | 4.08% |
| Sharpe Ratio | 0.22 |
| Max Drawdown | -34.17% |
| Volatility | 13.51% |
| Win Rate | 43.64% |
| Profit Factor | 0.97 |
| Calmar Ratio | 0.12 |
| Final Value | 121,213.31 |
| Total Trades | 236 |
| Trading Days | 1212 |
| Traded Symbols | 000001, 000333, 000858 |

**Risk Control Stats**:

| Rule | Count |
|:---|---:|
| total_signals_in | 1746 |
| single_weight_clipped | 252 |
| daily_symbol_rejected | 0 |
| total_position_clipped | 0 |
| total_position_rejected | 0 |
| drawdown_breaker_triggered | 382 |
| industry_clipped | 0 |
| signals_out | 607 |
| max_daily_ratio_used | 92.2% |
| stop_loss_triggered | 659 |

**Config**:

```
{
  "max_single_weight": 0.3,
  "max_total_position": 0.95,
  "stop_loss": 0.08,
  "max_daily_loss": 0.03,
  "max_daily_symbols": 5,
  "max_drawdown": 0.25,
  "enforce_stop_loss": true,
  "vol_adaptive": true,
  "vol_low": 0.15,
  "vol_high": 0.4,
  "max_industry_weight": 0.5,
  "industry_map": {
    "000001": "银行",
    "000333": "家电",
    "000858": "白酒",
    "002415": "科技",
    "300750": "新能源"
  }
}
```

| R002 | default | 4.08% | 0.22 | -34.17% | 236 | 121,213 | Breaker382, Clip252, SL659 |

---
### R002: default

**Time**: 2026-06-27T20:10:03.499389  
**Symbols**: 000001, 000333, 000858  
**Notes**: 

| Metric | Value |
|:---|---:|
| Total Return | 21.21% |
| Annual Return | 4.08% |
| Sharpe Ratio | 0.22 |
| Max Drawdown | -34.17% |
| Volatility | 13.51% |
| Win Rate | 43.64% |
| Profit Factor | 0.97 |
| Calmar Ratio | 0.12 |
| Final Value | 121,213.31 |
| Total Trades | 236 |
| Trading Days | 1212 |
| Traded Symbols | 000001, 000333, 000858 |

**Risk Control Stats**:

| Rule | Count |
|:---|---:|
| total_signals_in | 1746 |
| single_weight_clipped | 252 |
| daily_symbol_rejected | 0 |
| total_position_clipped | 0 |
| total_position_rejected | 0 |
| drawdown_breaker_triggered | 382 |
| industry_clipped | 0 |
| signals_out | 607 |
| max_daily_ratio_used | 92.2% |
| stop_loss_triggered | 659 |

**Config**:

```
{
  "max_single_weight": 0.3,
  "max_total_position": 0.95,
  "stop_loss": 0.08,
  "max_daily_loss": 0.03,
  "max_daily_symbols": 5,
  "max_drawdown": 0.25,
  "enforce_stop_loss": true,
  "vol_adaptive": true,
  "vol_low": 0.15,
  "vol_high": 0.4,
  "max_industry_weight": 0.5,
  "industry_map": {
    "000001": "银行",
    "000333": "家电",
    "000858": "白酒",
    "002415": "科技",
    "300750": "新能源"
  }
}
```

| R003 | default | 3.77% | 0.20 | -34.55% | 111 | 119,479 | Breaker466, Clip237, SL538 |

---
### R003: default

**Time**: 2026-06-29T14:46:26.342211  
**Symbols**: 000001, 000333, 000858  
**Notes**: 

| Metric | Value |
|:---|---:|
| Total Return | 19.48% |
| Annual Return | 3.77% |
| Sharpe Ratio | 0.20 |
| Max Drawdown | -34.55% |
| Volatility | 13.02% |
| Win Rate | 36.94% |
| Profit Factor | 1.06 |
| Calmar Ratio | 0.11 |
| Final Value | 119,479.05 |
| Total Trades | 111 |
| Trading Days | 1212 |
| Traded Symbols | 000001, 000333, 000858 |

**Risk Control Stats**:

| Rule | Count |
|:---|---:|
| total_signals_in | 1625 |
| single_weight_clipped | 237 |
| daily_symbol_rejected | 0 |
| total_position_clipped | 0 |
| total_position_rejected | 0 |
| drawdown_breaker_triggered | 466 |
| industry_clipped | 0 |
| signals_out | 574 |
| max_daily_ratio_used | 90.8% |
| stop_loss_triggered | 538 |

**Config**:

```
{
  "max_single_weight": 0.3,
  "max_total_position": 0.95,
  "stop_loss": 0.08,
  "max_daily_loss": 0.03,
  "max_daily_symbols": 5,
  "max_drawdown": 0.25,
  "enforce_stop_loss": true,
  "vol_adaptive": true,
  "vol_low": 0.15,
  "vol_high": 0.4,
  "max_industry_weight": 0.5,
  "industry_map": {
    "000001": "银行",
    "000333": "家电",
    "000858": "白酒",
    "002415": "科技",
    "300750": "新能源"
  }
}
```

