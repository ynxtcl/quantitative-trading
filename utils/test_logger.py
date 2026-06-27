# -*- coding: utf-8 -*-
"""
TestLogger - auto-run backtest and log all metrics.
"""

import sys, os, json, datetime
from typing import Dict, List
from dataclasses import dataclass, asdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

if sys.platform == 'win32' and hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

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
from backtest.metrics import calculate_metrics
from utils.proxy import safe_clean_proxy

try:
    from debug_risk import InstrumentedRiskManager
except ImportError:
    InstrumentedRiskManager = None

REPORT_DIR = os.path.join(ROOT, 'data_storage', 'reports')
os.makedirs(REPORT_DIR, exist_ok=True)
MD_LOG = os.path.join(REPORT_DIR, 'test_results.md')
JSON_LOG = os.path.join(REPORT_DIR, 'test_results.json')
@dataclass
class TestRunRecord:
    run_id: str
    timestamp: str
    description: str
    symbols: List[str]
    config_snapshot: dict
    total_return: float = 0.0
    annual_return: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    volatility: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    calmar_ratio: float = 0.0
    final_value: float = 0.0
    total_trades: int = 0
    total_days: int = 0
    risk_stats: Dict[str, int] = None
    stop_loss_triggered: int = 0
    traded_symbols: List[str] = None
    notes: str = ""

class TestLogger:
    def __init__(self, log_to_md=True, log_to_json=True):
        self.log_to_md = log_to_md
        self.log_to_json = log_to_json
        self._run_counter = self._load_run_counter()

    def _load_run_counter(self) -> int:
        if os.path.exists(JSON_LOG):
            try:
                with open(JSON_LOG, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        return len(data)
            except:
                pass
        return 0

    def run_and_log(self, description='', symbols=None, risk_config=None, notes=''):
        safe_clean_proxy()
        symbols = symbols or DEFAULT_SYMBOLS[:3]
        risk_config = risk_config or dict(RISK_CONFIG)

        print()
        print('=' * 60)
        print(f'  TestLogger - Run #{self._run_counter + 1}')
        print(f'  {description}')
        print(f'  Symbols: {symbols}')
        print('=' * 60)

        loader = DataLoader()
        data_dict = loader.load_multiple(
            symbols,
            start=BACKTEST_CONFIG['start_date'],
            end=BACKTEST_CONFIG['end_date'],
        )
        if not data_dict:
            raise RuntimeError('Data loading failed')
        cleaned = {sym: clean_daily_data(df) for sym, df in data_dict.items()}

        tf_strategies = {}
        mr_strategies = {}
        for sym in symbols:
            c = dict(TFC); c['symbol'] = sym
            tf_strategies[sym] = TrendFollowingStrategy('trend_following', c)
            c = dict(MRC); c['symbol'] = sym
            mr_strategies[sym] = MeanReversionStrategy('mean_reversion', c)

        rebalancer = FactorRebalancer(FSC)
        combiner = PortfolioCombiner()
        combiner.set_weights({
            'trend_following': TFC.get('weight', 0.4),
            'mean_reversion': MRC.get('weight', 0.3),
            'factor_selection': FSC.get('weight', 0.3),
        })

        if InstrumentedRiskManager is not None:
            risk_mgr = InstrumentedRiskManager(dict(risk_config))
        else:
            risk_mgr = RiskManager(dict(risk_config))

        engine = PortfolioEngine(BACKTEST_CONFIG)
        result = engine.run(cleaned, tf_strategies, mr_strategies,
                           rebalancer, combiner, risk_mgr)

        daily_values = [r.total_value for r in result.daily_records]
        paired_trades = self._pair_trades(result.trades)
        metrics = calculate_metrics(
            daily_values=daily_values,
            trades=paired_trades,
            initial_capital=result.initial_capital,
        )

        risk_stats = {}
        stop_loss_count = 0
        if hasattr(risk_mgr, 'stats'):
            risk_stats = dict(risk_mgr.stats)
        if hasattr(engine, 'stop_loss_triggered'):
            stop_loss_count = engine.stop_loss_triggered

        traded_symbols = sorted(set(t.symbol for t in result.trades))

        self._run_counter += 1
        timestamp = datetime.datetime.now().isoformat()

        record = TestRunRecord(
            run_id=f'R{self._run_counter:03d}',
            timestamp=timestamp,
            description=description,
            symbols=list(symbols),
            config_snapshot=dict(risk_config),
            total_return=metrics.total_return,
            annual_return=metrics.annual_return,
            sharpe_ratio=metrics.sharpe_ratio,
            max_drawdown=metrics.max_drawdown,
            volatility=metrics.volatility,
            win_rate=metrics.win_rate,
            profit_factor=metrics.profit_factor,
            calmar_ratio=metrics.calmar_ratio,
            final_value=result.final_value(),
            total_trades=metrics.total_trades,
            total_days=len(result.daily_records),
            risk_stats=risk_stats,
            stop_loss_triggered=stop_loss_count,
            traded_symbols=traded_symbols,
            notes=notes,
        )

        if self.log_to_md:
            self._append_md(record)
        if self.log_to_json:
            self._append_json(record)

        self._print_summary(record)
        return record

    def _pair_trades(self, trades) -> list:
        paired = []
        buy_map = {}
        for t in trades:
            if t.direction == 1:
                key = t.symbol
                if key not in buy_map:
                    buy_map[key] = []
                buy_map[key].append(t)
            elif t.direction == -1:
                buys = buy_map.get(t.symbol, [])
                matching = [b for b in buys if str(b.date) <= str(t.date)]
                if matching:
                    buy = matching[-1]
                    cost = buy.value + buy.cost
                    proceeds = t.value - t.cost
                    if cost > 0:
                        paired.append({
                            'date': str(t.date), 'symbol': t.symbol,
                            'direction': -1, 'price': t.price,
                            'quantity': t.quantity, 'value': t.value,
                            'cost': t.cost, 'strategy': t.strategy,
                            'pnl': proceeds - cost,
                        })
        return paired

    def _append_md(self, record):
        header = not os.path.exists(MD_LOG) or os.path.getsize(MD_LOG) == 0
        desc = record.description.replace('"', '').replace("'", '')

        with open(MD_LOG, 'a', encoding='utf-8') as f:
            if header:
                f.write('# Backtest Test Log\n\n')
                f.write('| Run | Description | Ann.Ret | Sharpe | MaxDD | Trades | Final | Risk Triggers |\n')
                f.write('|:---:|:---|:---:|:---:|:---:|:---:|:---:|:---|\n')

            triggers = []
            if record.risk_stats:
                if record.risk_stats.get('drawdown_breaker_triggered', 0):
                    triggers.append(f"Breaker{record.risk_stats['drawdown_breaker_triggered']}")
                if record.risk_stats.get('single_weight_clipped', 0):
                    triggers.append(f"Clip{record.risk_stats['single_weight_clipped']}")
                if record.stop_loss_triggered:
                    triggers.append(f"SL{record.stop_loss_triggered}")
            trig_str = ', '.join(triggers) if triggers else '-'

            f.write(f"| {record.run_id} | {desc} | {record.annual_return:.2%} | {record.sharpe_ratio:.2f} | {record.max_drawdown:.2%} | {record.total_trades} | {record.final_value:,.0f} | {trig_str} |\n")

            f.write('\n---\n')
            f.write(f'### {record.run_id}: {desc}\n\n')
            f.write(f'**Time**: {record.timestamp}  \n')
            f.write(f'**Symbols**: {', '.join(record.symbols)}  \n')
            f.write(f'**Notes**: {record.notes}\n\n')
            f.write('| Metric | Value |\n')
            f.write('|:---|---:|\n')
            f.write(f'| Total Return | {record.total_return:.2%} |\n')
            f.write(f'| Annual Return | {record.annual_return:.2%} |\n')
            f.write(f'| Sharpe Ratio | {record.sharpe_ratio:.2f} |\n')
            f.write(f'| Max Drawdown | {record.max_drawdown:.2%} |\n')
            f.write(f'| Volatility | {record.volatility:.2%} |\n')
            f.write(f'| Win Rate | {record.win_rate:.2%} |\n')
            f.write(f'| Profit Factor | {record.profit_factor:.2f} |\n')
            f.write(f'| Calmar Ratio | {record.calmar_ratio:.2f} |\n')
            f.write(f'| Final Value | {record.final_value:,.2f} |\n')
            f.write(f'| Total Trades | {record.total_trades} |\n')
            f.write(f'| Trading Days | {record.total_days} |\n')

            f.write(f'| Traded Symbols | {', '.join(record.traded_symbols) if record.traded_symbols else "-"} |\n')
            if record.risk_stats:
                f.write('\n**Risk Control Stats**:\n\n')
                f.write('| Rule | Count |\n')
                f.write('|:---|---:|\n')
                for k, v in record.risk_stats.items():
                    if k == 'max_daily_ratio_used':
                        f.write(f"| {k} | {v:.1%} |\n")
                    else:
                        f.write(f"| {k} | {v} |\n")
                f.write(f"| stop_loss_triggered | {record.stop_loss_triggered} |\n")

            f.write('\n**Config**:\n\n')
            f.write(f'```\n{json.dumps(record.config_snapshot, indent=2, ensure_ascii=False)}\n```\n\n')

    def _append_json(self, record):
        data = []
        if os.path.exists(JSON_LOG):
            try:
                with open(JSON_LOG, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if not isinstance(data, list):
                        data = []
            except:
                data = []
        data.append(asdict(record))
        with open(JSON_LOG, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)

    def _print_summary(self, record):
        print()
        print('=' * 60)
        print(f'  TestLogger - Run #{record.run_id} Complete')
        print('=' * 60)
        print(f'  {record.description}')
        print(f'  {"-" * 50}')
        print(f'  [Performance Metrics]')
        print(f'  Total Return:      {record.total_return:>+8.2%}')
        print(f'  Annual Return:     {record.annual_return:>+8.2%}')
        print(f'  Sharpe Ratio:      {record.sharpe_ratio:>8.2f}')
        print(f'  Max Drawdown:      {record.max_drawdown:>8.2%}')
        print(f'  Volatility:        {record.volatility:>8.2%}')
        print(f'  Win Rate:          {record.win_rate:>8.2%}')
        print(f'  Profit Factor:     {record.profit_factor:>8.2f}')
        print(f'  Calmar Ratio:      {record.calmar_ratio:>8.2f}')
        print(f'  Final Value:       {record.final_value:>10,.2f}')
        print(f'  Trading Days:      {record.total_days:>8d}')
        print(f'  Total Trades:      {record.total_trades:>8d}')
        print(f'  Traded Symbols:    {', '.join(record.traded_symbols)}')
        if record.risk_stats:
            print(f'  {"-" * 50}')
            print(f'  [Risk Control Stats]')
            for k, v in record.risk_stats.items():
                if k == 'max_daily_ratio_used':
                    print(f'  {k:<30} {v:.1%}')
                else:
                    print(f'  {k:<30} {v:>8d}')
        if record.stop_loss_triggered:
            print(f'  stop_loss_triggered:        {record.stop_loss_triggered:>8d}')
        print(f'  {"-" * 50}')
        print(f'  Log: {MD_LOG}')
        print('=' * 60)


if __name__ == '__main__':
    logger = TestLogger()
    logger.run_and_log(description='default test (3 stocks)')

