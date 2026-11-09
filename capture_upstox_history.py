"""Command entry point; implementation lives in volatility_scanner.apps."""
if __name__ == "__main__":
    from runpy import run_module
    run_module("volatility_scanner.apps.capture_upstox_history", run_name="__main__")
