// T124 en Europa: efecto en el color de comparar la corriente con lo habitual también en Europa.
// Antes: el motor de v1.2 (Europa con la regla fija y openWater). Después: el motor nuevo con el corr europeo.
// Uso: node medir.mjs  → salida/medir.json y un resumen por consola.
import fs from 'node:fs';
import * as A from './motor-antes.mjs';
import * as B from './motor-despues.mjs';

const E = '/Volumes/MacExt/Divy-cache/T124/europa';
const H = 3600e3;
const corrNuevo = JSON.parse(fs.readFileSync(`${E}/salida/corriente-spots.json`, 'utf8'));
const corrAntes = JSON.parse(fs.readFileSync('/Volumes/MacExt/Divy-cache/divy-datos-t124/datos/corriente-spots.json', 'utf8'));
const europa = JSON.parse(fs.readFileSync(`${E}/salida/corr-europa.json`, 'utf8'));
// Previsiones reales (producción salvo la de España del 8 nov, de staging, con la misma corriente regional).
const PREV = {
  es: [`/Volumes/MacExt/Divy-cache/T124/staging/pr-es.json`, ...fs.readdirSync(`${E}/prev`).filter((f) => f.startsWith('es-')).map((f) => `${E}/prev/${f}`)],
  med: ['/Volumes/MacExt/Divy-cache/T126-validacion/prevision/prev-med.json', ...fs.readdirSync(`${E}/prev`).filter((f) => f.startsWith('med-')).map((f) => `${E}/prev/${f}`)],
  eu: ['/Volumes/MacExt/Divy-cache/T126-validacion/prevision/prev-eu.json', ...fs.readdirSync(`${E}/prev`).filter((f) => f.startsWith('eu-')).map((f) => `${E}/prev/${f}`)],
};
const N = ['buena', 'regular', 'dificil', 'sin-datos'];
const res = {};
const filas = [];
for (const [r, files] of Object.entries(PREV)) {
  const cat = JSON.parse(fs.readFileSync(`${E}/cat/${r}.json`, 'utf8')).spots;
  // Por (spot, hora), la previsión más reciente que la cubre.
  const horas = new Map();
  for (const f of files) {
    const p = JSON.parse(fs.readFileSync(f, 'utf8'));
    const from = Date.parse(p.window.from), n = p.window.hours;
    for (let i = 0; i < n; i++) horas.set(from + i * H, f);
  }
  const porPrev = {};
  for (const [t, f] of horas) (porPrev[f] ??= []).push(t);
  const t = (res[r] = { spots: cat.length, horas: 0, horasDia: 0, cambian: 0, horasCambian: 0, horasCambianDia: 0, trans: {}, transDia: {}, confCambia: 0, verde2Antes: 0, verde2Despues: 0, dias: [Math.min(...horas.keys()), Math.max(...horas.keys())].map((x) => new Date(x).toISOString()) });
  const cambiaSpot = new Set();
  for (const [f, ts] of Object.entries(porPrev)) {
    const p = JSON.parse(fs.readFileSync(f, 'utf8'));
    const now = Date.parse(p.generatedAt);
    A.resetForecast(); A.setForecast(p);
    B.resetForecast(); B.setForecast(p);
    for (const s of cat) {
      const sa = { ...s, distanceKm: 0, ...(corrAntes[s.id] ? { corr: corrAntes[s.id] } : {}) };
      const sb = { ...s, distanceKm: 0, ...(corrNuevo[s.id] ? { corr: corrNuevo[s.id] } : {}) };
      if (!corrNuevo[s.id]) delete sb.corr;
      for (const h of ts) {
        const a = A.evaluate(sa, h, [], now), b = B.evaluate(sb, h, [], now);
        if (a.level === 'sin-datos' && b.level === 'sin-datos') continue;
        t.horas++;
        if (!a.night) t.horasDia++;
        if (a.confidence !== b.confidence) t.confCambia++;
        // Verde con la corriente del modelo de 2 km/h o más (lo que el tope de Europa evita).
        if (a.level === 'buena' && (a.current?.mean ?? 0) >= 2) t.verde2Antes++;
        if (b.level === 'buena' && (b.current?.mean ?? 0) >= 2) { t.verde2Despues++; const k = (europa[s.id]?.hab ? 'hab' : corrNuevo[s.id]?.hab ? 'habGlobal' : 'sinHab') + (s.openWater ? '-abierto' : '-cala'); t.verde2Por = t.verde2Por ?? {}; t.verde2Por[k] = (t.verde2Por[k] ?? 0) + 1; }
        if (a.level === b.level) continue;
        const k = `${a.level}→${b.level}`;
        t.horasCambian++; t.trans[k] = (t.trans[k] ?? 0) + 1;
        if (!a.night) { t.horasCambianDia++; t.transDia[k] = (t.transDia[k] ?? 0) + 1; }
        cambiaSpot.add(s.id);
        const e = europa[s.id];
        filas.push({ r, id: s.id, name: s.name, h: new Date(h).toISOString(), noche: a.night, de: a.level, a: b.level, motivoAntes: a.motivo, motivoDespues: b.motivo, limitaAntes: a.limita, limitaDespues: b.limita,
          cur: b.current?.mean, min: b.current?.min, max: b.current?.max, p90: e?.hab?.[1], conf: e?.conf, por: e?.por, openWater: s.openWater, modelos: e?.modelos, celdaKm: e && Object.values(e.celdas ?? {}).map((c) => c.km), confA: a.confidence, confB: b.confidence });
      }
    }
  }
  t.cambian = cambiaSpot.size;
  console.log(r, JSON.stringify(t));
}
fs.writeFileSync(`${E}/salida/medir.json`, JSON.stringify({ res, filas }));
// Por spot: horas que cambian y sentido.
const porSpot = {};
for (const f of filas) {
  const k = `${f.r}/${f.id}`;
  const o = (porSpot[k] ??= { r: f.r, id: f.id, name: f.name, n: 0, dia: 0, trans: {}, p90: f.p90, conf: f.conf, por: f.por, openWater: f.openWater, modelos: f.modelos, celdaKm: f.celdaKm, curMax: 0, curMin: 99 });
  o.n++; if (!f.noche) o.dia++;
  o.trans[`${f.de}→${f.a}`] = (o.trans[`${f.de}→${f.a}`] ?? 0) + 1;
  o.curMax = Math.max(o.curMax, f.cur ?? 0); o.curMin = Math.min(o.curMin, f.cur ?? 99);
}
fs.writeFileSync(`${E}/salida/medir-spots.json`, JSON.stringify(Object.values(porSpot).sort((a, b) => b.n - a.n), null, 1));
console.log('spots que cambian', Object.keys(porSpot).length);
