"""Risk manager - 8 layer risk control"""
class RiskManager:
    def __init__(self, config=None):
        defaults = {"max_single_weight":0.30,"max_total_position":0.95,"max_daily_symbols":5,"max_drawdown":0.25,"vol_adaptive":True,"vol_low":0.15,"vol_high":0.40,"max_industry_weight":0.50}
        if config: defaults.update(config)
        self.config = defaults
    def filter(self, signals, positions, position_ratios, daily_count, industry_exposure, drawdown, volatility):
        """Apply all risk rules. Returns filtered signals."""
        # Drawdown circuit breaker
        if drawdown > self.config.get('max_drawdown', 0.25):
            return {}  # stop all trading
        # Volatility adaptive
        vol_factor = 1.0
        if self.config.get('vol_adaptive'):
            vol_low = self.config['vol_low']; vol_high = self.config['vol_high']
            if volatility > vol_high: vol_factor = vol_low / volatility
            elif volatility < vol_low: vol_factor = 1.0
            else: vol_factor = 1.0 - (volatility - vol_low) / (vol_high - vol_low) * 0.5
        filtered = {}
        for sym, weight in signals.items():
            if weight <= 0: filtered[sym]=weight; continue
            # Single position cap
            existing = position_ratios.get(sym, 0)
            adj_weight = min(weight * vol_factor, self.config['max_single_weight'] - existing)
            if adj_weight <= 0: continue
            # Industry concentration
            ind = self.config.get('industry_map',{}).get(sym)
            if ind:
                ind_exp = industry_exposure.get(ind, 0)
                if ind_exp + adj_weight > self.config.get('max_industry_weight', 0.50):
                    adj_weight = max(0, self.config['max_industry_weight'] - ind_exp)
            # Daily symbol limit
            if daily_count >= self.config.get('max_daily_symbols',5): break
            filtered[sym] = adj_weight
        return filtered
