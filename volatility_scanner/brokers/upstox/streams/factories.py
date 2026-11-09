"""User-facing market stream factory using the existing Protobuf capture transport."""
from types import SimpleNamespace

from .market import VerifiedStreamer
from ..api.client import RequestError

MODES = {'full', 'full_d30', 'option_greeks', 'ltpc'}


class MarketStream(VerifiedStreamer):
    def __init__(self, client, *, instrument_keys=(), mode='full', on_message=None):
        if client.profile == 'sandbox':
            raise RequestError('Upstox does not document a sandbox market WebSocket')
        if mode not in MODES:
            raise RequestError('Unknown market feed mode')
        if isinstance(instrument_keys, (str, bytes)):
            raise RequestError('Instrument keys must be a collection, not a string')
        keys = set(instrument_keys)
        self._validate({name: keys if name == mode else set() for name in MODES})
        if any(not isinstance(key, str) or '|' not in key or len(key) > 160 for key in keys):
            raise RequestError('Invalid instrument key')
        credential = client.credential
        config = SimpleNamespace(auth_settings=lambda: {'OAUTH2': {'value': 'Bearer ' + credential.access_token}})
        super().__init__(SimpleNamespace(configuration=config), list(keys), mode)
        if on_message:
            self.on('message', on_message)

    def subscribe(self, instrumentKeys, mode):
        if mode not in MODES:
            raise RequestError('Unknown market feed mode')
        candidate = {name: set(keys) for name, keys in self.subscriptions.items()}
        candidate[mode].update(instrumentKeys)
        self._validate(candidate)
        super().subscribe(instrumentKeys, mode)

    @staticmethod
    def _validate(subscriptions):
        active = [mode for mode, keys in subscriptions.items() if keys]
        limits = {'ltpc': 5000, 'option_greeks': 3000, 'full': 2000, 'full_d30': 50}
        if len(active) > 1:
            limits.update(ltpc=2000, option_greeks=2000, full=1500)
        for mode, keys in subscriptions.items():
            if len(keys) > limits[mode]:
                raise RequestError('Subscription exceeds documented ' + mode + ' capacity')

    def connect(self):
        self._validate(self.subscriptions)
        super().connect()

    def close(self):
        self.auto_reconnect(False)
        try:
            if self.feeder is not None:
                self.disconnect()
        finally:
            self.accepting_frames = False
            self.stop_decoder()

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *_):
        self.close()
