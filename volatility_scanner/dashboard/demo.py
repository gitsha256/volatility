"""Isolated, explicitly labeled synthetic preview using the real algorithm; no broker or journal."""
from datetime import datetime, timedelta, time as clock
import math
from types import SimpleNamespace

from volatility_scanner.analytics.exposure import spot_greeks
from volatility_scanner.analytics.pcr_time import YEAR_SECONDS
from volatility_scanner.brokers.upstox.market.coverage import expiry_epoch
from volatility_scanner.brokers.upstox.market.pcr_time import StreamingAnalytics
from volatility_scanner.storage.journal import IST
from .candles import CandleBook


class DemoFeed:
    def __init__(self):
        day=datetime.now(IST).date()
        while day.weekday()>4:
            day-=timedelta(days=1)
        self.epoch=int(datetime.combine(day,clock(14),IST).timestamp())-120*60
        expiries=[str(day+timedelta(days=d)) for d in (3,10)]
        self.contracts=[dict(symbol=f'DEMO|{expiry}_{strike}_{kind}',expiry_date=expiry,
                            strike_price=strike,option_type=kind)
                        for expiry in expiries for strike in (24950,25000,25050) for kind in ('CE','PE')]
        self.state=SimpleNamespace(contracts={r['symbol']:r for r in self.contracts},latest={},received={},price_received={},
            symbols={r['symbol']:(f"{expiry_epoch(r['expiry_date'])}_{r['strike_price']:g}",r['option_type']) for r in self.contracts},
            index={},index_received=None,connected=True,analytics_dirty=set(),analytics_generation=0)
        self.book=CandleBook()
        self.book.set_market_status({'NSE_EQ':'NORMAL_OPEN'})
        self.scanner=StreamingAnalytics(exposure=True)
        self.events=[]
        self.step=0
        for _ in range(7201):
            self.advance()

    def advance(self):
        self.epoch+=1
        self.step+=1
        spot=25000+26*math.sin(self.step/540)+12*math.sin(self.step/80)+self.step*.002
        self.state.index={'ltp':spot}
        self.state.index_received=self.epoch
        self.book.tick(spot,self.epoch)
        for i,row in enumerate(self.contracts):
            key=row['symbol']
            iv=19+1.2*math.sin(self.step/700+i*.1)
            oi=round(140000+(30000 if row['option_type']=='PE' else 0)+22000*math.sin(self.step/420+i))
            price=spot_greeks(spot,row['strike_price'],iv/100,(expiry_epoch(row['expiry_date'])-self.epoch)/YEAR_SECONDS,row['option_type'])['price']
            self.state.latest[key]=dict(oi=oi,iv=iv,ltp=max(.05,price+.05*math.sin(self.step/25+i)),trade_epoch=self.epoch)
            self.state.received[key]=self.state.price_received[key]=self.epoch
            self.state.analytics_dirty.add(key)
        self.scanner.consume(self.state,self.epoch)
        if self.epoch%60==0:
            for bucket,source,data in self.scanner.latest_output:
                self.events.append(dict(bucket=bucket,source=source,data=data))
            self.events=self.events[-10000:]

    def history(self,targets,points):
        result=[]
        for bucket,source in targets:
            records=[e for e in self.events if e['bucket']==bucket and e['source']==source]
            result.extend(records[-points*(2 if source=='premium_minute' else 1):])
        return result
