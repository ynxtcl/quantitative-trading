"""Portfolio combiner"""
class PortfolioCombiner:
    def __init__(self):
        self.strategy_weights = {}
    def set_weights(self, weights):
        self.strategy_weights = weights
    def combine(self, signals):
        """Combine signals from multiple strategies"""
        combined = {}
        for sym, strat_signals in signals.items():
            net = 0
            for strat_name, signal in strat_signals.items():
                w = self.strategy_weights.get(strat_name, 0)
                net += signal * w
            combined[sym] = net
        return combined
