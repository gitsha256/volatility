"""Sandbox verification entry point."""
if __name__=='__main__':
    from runpy import run_module
    run_module('volatility_scanner.apps.verify_upstox_sandbox',run_name='__main__')
