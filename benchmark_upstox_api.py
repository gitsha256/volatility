"""Offline Upstox API benchmark entry point."""
if __name__=='__main__':
    from runpy import run_module
    run_module('volatility_scanner.apps.benchmark_upstox_api',run_name='__main__')
