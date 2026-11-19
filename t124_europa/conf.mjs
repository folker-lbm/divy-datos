import fs from 'node:fs';
import * as A from './motor-antes.mjs';
import * as B from './motor-despues.mjs';
const E='/Volumes/MacExt/Divy-cache/T124/europa', H=3600e3;
const corr=JSON.parse(fs.readFileSync(`${E}/salida/corriente-spots.json`,'utf8'));
for (const r of ['es','med','eu']) {
  const f=fs.readdirSync(`${E}/prev`).find(x=>x.startsWith(r+'-'));
  const p=JSON.parse(fs.readFileSync(`${E}/prev/${f}`,'utf8')); const now=Date.parse(p.generatedAt), from=Date.parse(p.window.from);
  const cat=JSON.parse(fs.readFileSync(`${E}/cat/${r}.json`,'utf8')).spots;
  A.resetForecast();A.setForecast(p);B.resetForecast();B.setForecast(p);
  const c={}; let n=0;
  for (const s of cat){ const sb={...s,distanceKm:0,...(corr[s.id]?{corr:corr[s.id]}:{})};
    for(let i=0;i<p.window.hours;i++){const t=from+i*H; const a=A.evaluate({...s,distanceKm:0},t,[],now), b=B.evaluate(sb,t,[],now);
      if(a.level==='sin-datos')continue; n++;
      if(a.confidence!==b.confidence){const k=`${a.confidence}→${b.confidence} ${b.current?.disagree?'discrepan':''} ${a.level===b.level?'mismo color':''}`; c[k]=(c[k]??0)+1;}}}
  console.log(r,n,c);
}
