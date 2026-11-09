"""Command entry point; implementation lives in volatility_scanner.apps."""
if __name__ == "__main__":
    from runpy import run_module
    run_module("volatility_scanner.apps.authenticate_upstox", run_name="__main__")
