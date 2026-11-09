"""Bounded, thread-safe OHLC accumulator; consumes every received NIFTY update."""
from collections import OrderedDict
from datetime import datetime, time as clock
import math
import threading

from volatility_scanner.storage.journal import IST


class CandleBook:
    def __init__(self, capacity=1440):
        self.capacity=capacity
        self.bars=OrderedDict()
        self.dirty=set()
        self.lock=threading.Lock()
        self.market_status={}

    def set_market_status(self, values):
        with self.lock:
            self.market_status=dict(values)

    def tick(self, price, epoch):
        if not all(type(v) in (int,float) and math.isfinite(v) for v in (price,epoch)) or price<=0:
            return
        moment=datetime.fromtimestamp(epoch,IST)
        if moment.weekday()>4 or not clock(9,15)<=moment.time()<clock(15,30):
            return
        minute=int(epoch//60)*60
        with self.lock:
            status=self.market_status.get('NSE_INDEX',self.market_status.get('NSE_EQ'))
            if status is not None and status!='NORMAL_OPEN':
                return
            bar=self.bars.get(minute)
            if bar is None:
                bar=dict(time=minute,open=price,high=price,low=price,close=price,
                         partial=epoch-minute>1,source='received_stream',updated_epoch=epoch)
                self.bars[minute]=bar
            else:
                bar['high']=max(bar['high'],price)
                bar['low']=min(bar['low'],price)
                if epoch>=bar['updated_epoch']:
                    bar['close']=price
                    bar['updated_epoch']=epoch
            self.dirty.add(minute)
            self._bound()

    def _bound(self):
        while len(self.bars)>self.capacity:
            first=min(self.bars)
            del self.bars[first]
            self.dirty.discard(first)

    def seed(self, bars):
        with self.lock:
            for incoming in bars:
                values=[incoming.get(k) for k in ('time','open','high','low','close')]
                if not all(type(v) in (int,float) and math.isfinite(v) for v in values):
                    continue
                minute,o,h,l,c=values
                if min(o,h,l,c)<=0 or h<max(o,l,c) or l>min(o,h,c):
                    continue
                minute=int(minute//60)*60
                new=dict(time=minute,open=o,high=h,low=l,close=c,partial=bool(incoming.get('partial',False)),
                         source=incoming.get('source','broker_history'),updated_epoch=incoming.get('updated_epoch',minute))
                old=self.bars.get(minute)
                if old and old['updated_epoch']>new['updated_epoch']:
                    new.update(high=max(old['high'],h),low=min(old['low'],l),close=old['close'],
                               updated_epoch=old['updated_epoch'],source='broker_history+stream')
                self.bars[minute]=new
                self.dirty.add(minute)
            self._bound()

    def snapshot(self, limit=240):
        with self.lock:
            return [dict(self.bars[k]) for k in sorted(self.bars)[-limit:]]

    def drain(self):
        with self.lock:
            result=[dict(self.bars[k]) for k in sorted(self.dirty) if k in self.bars]
            self.dirty.clear()
            return result
