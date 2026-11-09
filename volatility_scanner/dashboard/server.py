"""Local WebSocket dashboard; bounded snapshots, no orders or broker secrets."""
import asyncio
from datetime import datetime
from importlib.resources import files
import json
import math
import time
from urllib.parse import urlsplit

from aiohttp import web, WSMsgType

from volatility_scanner.brokers.upstox.market.coverage import expiry_epoch
from .candles import CandleBook
from .store import DashboardStore


@web.middleware
async def loopback_only(request,handler):
    if urlsplit('http://'+request.host).hostname not in ('localhost','127.0.0.1','::1'):
        raise web.HTTPForbidden(text='Local dashboard host required')
    response=await handler(request)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Referrer-Policy']='no-referrer'
    response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'"
    return response


class DashboardServer:
    def __init__(self,directory,*,recorder=None,demo=None):
        self.store=DashboardStore(directory)
        self.recorder=recorder
        self.demo=demo
        self.book=recorder.dashboard_book if recorder else demo.book if demo else CandleBook()
        self.clients={}
        self.runner=self.task=self.seed_task=None
        self.seed_warning=None
        self._epoch=None
        self._index={}
        self.candle_revision=0
        self.app=web.Application(client_max_size=16384,middlewares=[loopback_only])
        self.app.router.add_get('/',self.index)
        self.app.router.add_get('/ws',self.websocket)
        self.app.router.add_get('/health',self.health)
        self.app.router.add_static('/static/',str(files('volatility_scanner.dashboard').joinpath('static')))

    async def index(self,request):
        return web.Response(text=files('volatility_scanner.dashboard').joinpath('static/index.html').read_text(),
                            content_type='text/html',headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})

    def contracts(self):
        return list(self.recorder.contracts.values()) if self.recorder else self.demo.contracts if self.demo else self.store.contracts()

    def catalog(self):
        result={}
        for row in self.contracts():
            expiry=row.get('expiry_date',row.get('expiry'))
            strike=row.get('strike_price')
            if isinstance(expiry,str) and type(strike) in (int,float) and math.isfinite(strike):
                result.setdefault(expiry,set()).add(strike)
        return {k:sorted(v) for k,v in sorted(result.items())}

    def select(self,value):
        catalog=self.catalog()
        dates=list(catalog)
        active=[d for d in dates if expiry_epoch(d)>time.time()]
        expiry=value.get('expiry') or (active or dates or [None])[0]
        if expiry is not None and expiry not in catalog:
            raise ValueError('Select an available expiry')
        strike=value.get('strike')
        if strike in ('',None,'all'):
            strike=None
        else:
            strike=float(strike)
            if not math.isfinite(strike) or strike not in catalog.get(expiry,[]):
                raise ValueError('Select an available strike')
        points=value.get('points',240)
        if type(points) is not int or not 30<=points<=1440:
            raise ValueError('History must be between 30 and 1440 bars')
        return dict(expiry=expiry,strike=strike,points=points)

    def targets(self,selection):
        expiry=selection['expiry']
        if expiry is None:
            return []
        targets=[(expiry+'_PCR_TIME','pcr_time_minute'),(expiry+'_EXPOSURE','exposure_minute')]
        if selection['strike'] is not None:
            bucket=f"{expiry_epoch(expiry)}_{selection['strike']:g}"
            targets.extend((bucket,s) for s in ('pcr_time_minute','exposure_minute','premium_minute'))
        return targets

    def metadata(self):
        consumer=self.recorder.algorithm_consumer if self.recorder else self.demo.scanner if self.demo else None
        health=consumer.health() if consumer else dict(latest_epoch=None,calculation_ms=None,ready_results=0)
        bars=self.book.snapshot(1)
        spot=self.recorder.index.get('ltp') if self.recorder else self.demo.state.index.get('ltp') if self.demo else bars[-1]['close'] if bars else None
        return dict(mode='demo' if self.demo else 'live' if self.recorder else 'journal',
            spot=spot,
            connected=bool(self.recorder.connected) if self.recorder else bool(self.demo),
            now=time.time(),market_status=dict(self.book.market_status),
            last_tick_epoch=self.recorder.index_received if self.recorder else self.demo.epoch if self.demo else None,
            seed_warning=self.seed_warning,**health)

    def current(self,selection):
        consumer=self.recorder.algorithm_consumer if self.recorder else self.demo.scanner if self.demo else None
        if consumer is None:
            return []
        if consumer.latest_epoch!=self._epoch:
            index={}
            for bucket,source,payload in consumer.latest_output:
                index.setdefault((bucket,source),[]).append(dict(bucket=bucket,source=source,data=payload))
            self._index,self._epoch=index,consumer.latest_epoch
        return [event for target in self.targets(selection) for event in self._index.get(target,[])]

    async def snapshot(self,selection):
        if self.demo:
            history=self.demo.history(self.targets(selection),selection['points'])
        else:
            history=await asyncio.to_thread(self.store.history,self.targets(selection),selection['points'])
        return dict(type='snapshot',selection=selection,catalog=self.catalog(),meta=self.metadata(),
            candles=self.book.snapshot(selection['points']),history=history,current=self.current(selection))

    async def websocket(self,request):
        origin=request.headers.get('Origin')
        if origin and (urlsplit(origin).netloc!=request.host or urlsplit(origin).scheme!='http'):
            raise web.HTTPForbidden(text='Same-origin connection required')
        if len(self.clients)>=16:
            raise web.HTTPServiceUnavailable(text='Dashboard client limit reached')
        ws=web.WebSocketResponse(heartbeat=20,max_msg_size=16384,compress=False)
        await ws.prepare(request)
        self.clients[ws]=None
        try:
            async for msg in ws:
                if msg.type==WSMsgType.TEXT:
                    try:
                        value=json.loads(msg.data)
                        if not isinstance(value,dict):
                            raise ValueError('Expected a selection object')
                        selection=self.select(value)
                        self.clients[ws]=None
                        await ws.send_str(json.dumps(await self.snapshot(selection),allow_nan=False,separators=(',',':')))
                        self.clients[ws]=selection
                    except (ValueError,TypeError):
                        await ws.send_json(dict(type='error',message='Invalid expiry, strike or history selection'))
                elif msg.type==WSMsgType.ERROR:
                    break
        finally:
            self.clients.pop(ws,None)
        return ws

    async def broadcast(self):
        candle_revision=self.candle_revision
        while True:
            if self.demo:
                self.demo.advance()
            cache={}
            sends=[]
            reset=candle_revision!=self.candle_revision
            for ws,selection in list(self.clients.items()):
                if selection is None or ws.closed:
                    continue
                key=tuple(selection.values())
                if key not in cache:
                    current=self.current(selection)
                    if not self.recorder and not self.demo:
                        current=await asyncio.to_thread(self.store.history,self.targets(selection),1)
                        self.book.seed(await asyncio.to_thread(self.store.candles,selection['points']))
                    cache[key]=json.dumps(dict(type='update',selection=selection,catalog=self.catalog(),meta=self.metadata(),current=current,
                        candles=self.book.snapshot(selection['points'] if reset else 3),candles_reset=reset),allow_nan=False,separators=(',',':'))
                sends.append(self.send_update(ws,cache[key]))
            if sends:
                await asyncio.gather(*sends)
            candle_revision=self.candle_revision
            await asyncio.sleep(1)

    async def send_update(self,ws,payload):
        try:
            await asyncio.wait_for(ws.send_str(payload),1)
        except (TimeoutError,asyncio.TimeoutError,ConnectionError,RuntimeError):
            await ws.close()

    async def seed_history(self,rest):
        try:
            result=await asyncio.to_thread(rest.get,'v3/historical-candle/intraday/NSE_INDEX%7CNifty%2050/minutes/1')
            bars=[dict(time=int(datetime.fromisoformat(row[0]).timestamp()),open=row[1],high=row[2],low=row[3],close=row[4],
                       source='broker_history',partial=False) for row in result.get('data',{}).get('candles',[]) if len(row)>=5]
            self.book.seed(bars)
            self.candle_revision+=1
        except Exception:
            self.seed_warning='Broker candle history unavailable; showing captured candles only'

    async def health(self,request):
        return web.json_response(self.metadata())

    async def start(self,port):
        if not self.demo:
            self.book.seed(await asyncio.to_thread(self.store.candles))
        self.runner=web.AppRunner(self.app,access_log=None)
        await self.runner.setup()
        await web.TCPSite(self.runner,'127.0.0.1',port).start()
        self.task=asyncio.create_task(self.broadcast())

    async def close(self):
        for task in (self.task,self.seed_task):
            if task:
                task.cancel()
        await asyncio.gather(*(t for t in (self.task,self.seed_task) if t),return_exceptions=True)
        await asyncio.gather(*(ws.close() for ws in list(self.clients)),return_exceptions=True)
        if self.runner:
            await self.runner.cleanup()
