"""Reporter"""
class Reporter:
    def generate(self, df, metrics, symbol):
        print(f"\n{'='*60}\n  Backtest Report - {symbol}\n{'='*60}")
        for k, v in metrics.items():
            print(f"  {k}: {v:.4f}" if isinstance(v, float) else f"  {k}: {v}")
