(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.NiftyStudies=api;})(typeof globalThis!=='undefined'?globalThis:this,function(){
  'use strict';
  const periods=[25,55,105];
  const finite=v=>typeof v==='number'&&Number.isFinite(v);
  function emptyAtr(){return {count:0,sum:0,value:null,close:null,upper:null,lower:null,trend:null,direction:1};}
  function step(previous,bar){
    if(!finite(bar.close))return {ema:[],atr:emptyAtr(),output:{time:bar.time,dema25:null,dema55:null,dema105:null,supertrend:null,direction:1,atrBars:0,emaBars:0}};
    const ema=periods.map((period,i)=>{const old=previous?.ema[i],alpha=2/(period+1),first=old?alpha*bar.close+(1-alpha)*old.first:bar.close,second=old?alpha*first+(1-alpha)*old.second:first;return {first,second,count:(old?.count||0)+1};});
    const output={time:bar.time,dema25:2*ema[0].first-ema[0].second,dema55:2*ema[1].first-ema[1].second,dema105:2*ema[2].first-ema[2].second,supertrend:null,direction:1,atrBars:0,emaBars:ema[0].count};
    let atr=emptyAtr();
    if(bar.ohlcAvailable!==false&&finite(bar.high)&&finite(bar.low)&&bar.high>=bar.low){
      const old=previous?.atr||emptyAtr(),tr=old.close===null?bar.high-bar.low:Math.max(bar.high-bar.low,Math.abs(bar.high-old.close),Math.abs(bar.low-old.close));
      atr={...old,count:old.count+1,close:bar.close};
      if(old.value===null){atr.sum=old.sum+tr;atr.value=atr.count>=10?atr.sum/10:null;}else atr.value=(old.value*9+tr)/10;
      if(atr.value!==null){
        const middle=(bar.high+bar.low)/2,up=middle+3*atr.value,down=middle-3*atr.value;
        atr.upper=old.upper===null||up<old.upper||old.close>old.upper?up:old.upper;
        atr.lower=old.lower===null||down>old.lower||old.close<old.lower?down:old.lower;
        atr.direction=old.value===null?1:old.trend===old.upper?(bar.close>atr.upper?-1:1):(bar.close<atr.lower?1:-1);
        atr.trend=atr.direction===-1?atr.lower:atr.upper;
        output.supertrend=atr.trend;output.direction=atr.direction;
      }
      output.atrBars=Math.min(10,atr.count);
    }
    return {ema,atr,output};
  }
  class Studies{
    constructor(){this.previous=null;this.current=null;this.lastTime=null;}
    update(bar){if(!finite(bar.time))throw new Error('Finite bar timestamp required');if(this.lastTime!==null&&bar.time<this.lastTime)throw new Error('Out-of-order study bar; rebuild required');if(this.lastTime!==bar.time){this.previous=this.current;this.lastTime=bar.time;}this.current=step(this.previous,bar);return this.current.output;}
    replace(bars){this.previous=this.current=null;this.lastTime=null;return bars.map(bar=>this.update(bar));}
  }
  class MetricOHLC{
    constructor(capacity=1440){this.capacity=capacity;this.bars=new Map();this.lastEpoch=null;}
    observe(epoch,value){
      if(!finite(epoch)||this.lastEpoch!==null&&epoch<=this.lastEpoch)return;
      const time=Math.floor(epoch/60)*60;let bar=this.bars.get(time);
      if(!bar){bar={time,open:null,high:null,low:null,close:null,firstEpoch:epoch,lastEpoch:epoch,samples:0,gap:epoch-time>1.5};this.bars.set(time,bar);}
      if(this.lastEpoch!==null&&epoch-this.lastEpoch>2.5)bar.gap=true;
      if(finite(value)){if(bar.open===null)bar.open=bar.high=bar.low=value;bar.high=Math.max(bar.high,value);bar.low=Math.min(bar.low,value);bar.close=value;bar.samples++;}else{bar.gap=true;bar.close=null;}
      bar.lastEpoch=epoch;this.lastEpoch=epoch;
      while(this.bars.size>this.capacity)this.bars.delete(this.bars.keys().next().value);
    }
    aggregate(interval){
      const groups=new Map();
      for(const bar of this.bars.values()){
        const time=Math.floor(bar.time/interval)*interval;let group=groups.get(time);
        if(!group){group={time,open:bar.open,high:bar.high,low:bar.low,close:bar.close,ohlcAvailable:bar.time===time&&!bar.gap,samples:0,lastMinute:bar.time,lastEpoch:bar.lastEpoch};groups.set(time,group);}
        else{if(bar.time!==group.lastMinute+60||group.lastEpoch<group.lastMinute+57.5)group.ohlcAvailable=false;group.lastMinute=bar.time;group.lastEpoch=bar.lastEpoch;group.high=finite(group.high)&&finite(bar.high)?Math.max(group.high,bar.high):null;group.low=finite(group.low)&&finite(bar.low)?Math.min(group.low,bar.low):null;group.close=bar.close;group.ohlcAvailable=group.ohlcAvailable&&!bar.gap;}
        group.samples+=bar.samples;
      }
      return groups;
    }
  }
  return {Studies,MetricOHLC,periods};
});
