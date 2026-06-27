"""Reporter"""
class PortfolioReporter:
    def generate(self, result, title):
        print(f"\n{'='*60}\n  {title}\n{'='*60}")
        print(f"  Final: {result.final_value():,.2f}")
        print(f"  Return: {result.total_return():+.2%}")
