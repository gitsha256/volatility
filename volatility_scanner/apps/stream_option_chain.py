"""Dedicated Upstox WebSocket -> minute PCR/time and exposure streamer."""
import sys

from .record_upstox import main


if __name__ == '__main__':
    main(['--algorithm-stream', '--feed-mode', 'option_greeks',
          '--analytics', 'pcr-time,exposure', '--output', 'D:/market-data/volatility/algorithm-stream',
          *sys.argv[1:]])
