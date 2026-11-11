// Calma en el repunte (como calma3.mjs de la revalidación): en cada hora de día con la corriente del modelo < 1 km/h,
// dos «tranquila»; en las 5 horas siguientes con ≥ 2 km/h, ¿cuántas salen en verde? Con la 1.2 de antes (v12), el
// motor de 20f42116 (previo) y el nuevo, cada uno con sus datos. Todos los spots europeos con lo habitual.
import fs from 'node:fs';
import * as V from './motor-v12.mjs';
import * as P from './motor-20f42116.mjs';
import * as N from './motor-revalida.mjs';
const H = 3600e3, R = '/Volumes/MacExt/Divy-cache/T124-europa-revalidacion', E = '/Volumes/MacExt/Divy-cache/T124/europa';
const CV = JSON.parse(fs.readFileSync(`${R}/corr-smoc.json`, 'utf8'));
const CP = JSON.parse(fs.readFileSync(`${R}/corr-f335014.json`, 'utf8'));
const CN = JSON.parse(fs.readFileSync(`${E}/salida/corriente-spots-rojo.json`, 'utf8'));
const tot = { fuertes: 0, v12: 0, previo: 0, nuevo: 0 }, porSpot = {};
for (const r of ['es', 'med', 'eu']) {
  const p = JSON.parse(fs.readFileSync(`${E}/prev/${r}-1110T17.json`, 'utf8'));
  const cat = JSON.parse(fs.readFileSync(`${E}/cat/${r}.json`, 'utf8')).spots;
  const from = Date.parse(p.window.from);
  for (const M of [V, P, N]) { M.resetForecast(); M.setForecast(p); }
  for (const s of cat) {
    if (!CN[s.id]?.hab) continue;
    const sv = { ...s, distanceKm: 0, corr: CV[s.id] }, sp = { ...s, distanceKm: 0, corr: CP[s.id] }, sn = { ...s, distanceKm: 0, corr: CN[s.id] };
    for (let i = 0; i < p.window.hours - 6; i++) {
      const t0 = from + i * H;
      const b0 = N.evaluate(sn, t0, [], t0);
      if (!b0.current || b0.current.mean >= 1 || b0.night) continue;
      const rep = [0, 1].map((n) => ({ spotId: s.id, diveTime: t0 - 10 * 60e3, author: `x${n}`, current: 1 }));
      for (let k = 1; k <= 5; k++) {
        const t = t0 + k * H;
        const sin = N.evaluate(sn, t, [], t0);
        if (!(sin.current?.mean >= 2)) continue;
        tot.fuertes++;
        const o = (porSpot[s.id] ??= { name: s.name, fuertes: 0, v12: 0, previo: 0, nuevo: 0 });
        o.fuertes++;
        for (const [k2, M, sx] of [['v12', V, sv], ['previo', P, sp], ['nuevo', N, sn]]) if (M.evaluate(sx, t, rep, t0).level === 'buena') { tot[k2]++; o[k2]++; }
      }
    }
  }
}
fs.writeFileSync(`${E}/salida/calma-revalida.json`, JSON.stringify({ tot, porSpot }, null, 1));
console.log(tot, Object.entries(porSpot).filter(([, o]) => o.v12 || o.previo || o.nuevo).map(([k, o]) => [k, o.fuertes, o.v12, o.previo, o.nuevo]));
