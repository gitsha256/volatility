const assert=require('node:assert/strict');
const {test}=require('node:test');
const {Studies,MetricOHLC}=require('../../volatility_scanner/dashboard/static/studies.js');
const bar=(time,close,high=close+1,low=close-1)=>({time,close,high,low});
test('DEMA periods match independent recursive EMA calculation',()=>{
  const bars=Array.from({length:400},(_,i)=>bar(i,100+Math.sin(i/10)*5+i*.1));
  const outputs=new Studies().replace(bars);
  for(const period of [25,55,105]){let first,second;const alpha=2/(period+1);for(let i=0;i<bars.length;i++){first=i?alpha*bars[i].close+(1-alpha)*first:bars[i].close;second=i?alpha*first+(1-alpha)*second:first;assert(Math.abs(outputs[i]['dema'+period]-(2*first-second))<1e-10);}}
});
test('Supertrend uses Wilder ATR 10 and multiplier 3, with direction changes',()=>{
  const study=new Studies();let value;for(let i=0;i<10;i++)value=study.update(bar(i,100));
  assert.equal(value.supertrend,106);assert.equal(value.direction,1);
  value=study.update(bar(10,110));assert(Math.abs(value.supertrend-101.3)<1e-10);assert.equal(value.direction,-1);
  value=study.update(bar(11,80));assert(Math.abs(value.supertrend-97.13)<1e-10);assert.equal(value.direction,1);
});
test('Replacing the forming bar never counts it twice',()=>{
  const bars=Array.from({length:20},(_,i)=>bar(i,100+i));const live=new Studies();live.replace(bars);
  const changed=bar(19,140,142,118);const replaced=live.update(changed);const batch=new Studies().replace([...bars.slice(0,-1),changed]).at(-1);
  assert.deepEqual(replaced,batch);assert.equal(live.update(changed).emaBars,20);
});
test('Close-only history supports DEMA but never fabricated Supertrend',()=>{
  const s=new Studies();let result;for(let i=0;i<50;i++)result=s.update({time:i,close:100+i,ohlcAvailable:false});
  assert(Number.isFinite(result.dema105));assert.equal(result.supertrend,null);
  for(let i=50;i<59;i++)assert.equal(s.update(bar(i,110)).supertrend,null);
  assert(Number.isFinite(s.update(bar(59,110)).supertrend));
  s.update({time:60});assert.equal(s.update(bar(61,100)).emaBars,1);
});
test('Metric OHLC preserves observed ranges, rejects duplicates and marks gaps',()=>{
  const b=new MetricOHLC(2);b.observe(120,10);b.observe(121,15);b.observe(122,8);b.observe(123,12);b.observe(123,99);
  const first=b.aggregate(60).get(120);assert.deepEqual([first.open,first.high,first.low,first.close],[10,15,8,12]);assert.equal(first.samples,4);assert.equal(first.ohlcAvailable,true);
  b.observe(130,11);assert.equal(b.aggregate(60).get(120).ohlcAvailable,false);
  b.observe(180,9);b.observe(240,10);assert.equal(b.bars.size,2);
  assert.equal(b.aggregate(300).get(0).ohlcAvailable,false);
});
