"""Factor rebalancer"""
class FactorRebalancer:
    def __init__(self, config):
        self.config = config; self.last_rebalance = None
    def get_rebalance_signals(self, data_dict, current_date):
        """Generate rebalance signals monthly"""
        import pandas as pd
        if self.last_rebalance and (current_date - self.last_rebalance).days < 20:
            return {}
        self.last_rebalance = current_date
        scores = {}
        for sym, df in data_dict.items():
            if sym not in df.index or df.empty: continue
            row = df.loc[min(df.index, key=lambda x: abs((pd.Timestamp(current_date) - x).days))]
            score = 0
            for factor in self.config.get('factors', []):
                val = row.get(factor['name'], 0) if hasattr(row, 'get') else 0.5
                score += val * factor['weight'] * factor['direction']
            scores[sym] = score
        sorted_syms = sorted(scores, key=scores.get, reverse=True)[:self.config.get('top_n',10)]
        signals = {}
        for sym in data_dict:
            signals[sym] = 0.1 if sym in sorted_syms else -0.1
        return signals
