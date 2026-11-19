// T124 Europa tras la revalidación (11 nov): calma que caduca y `corr.rojo`. Compara dos motores con dos corr, sin
// reportes, en Europa (es, med, eu) y en el resto del mundo. Uso: node medir-revalida.mjs <motorA> <corrA> <motorB> <corrB> <nombre>
import fs from 'node:fs';
const [ma, ca, mb, cb, nombre] = process.argv.slice(2);
const A = await import(ma), B = await import(mb);
const E = '/Volumes/MacExt/Divy-cache/T124/europa', T = '/Volumes/MacExt/Divy-cache/T126-validacion', H = 3600e3;
const CA = JSON.parse(fs.readFileSync(ca, 'utf8')), CB = JSON.parse(fs.readFileSync(cb, 'utf8'));
const PREV = {
  es: { cat: `${E}/cat/es.json`, prev: ['/Volumes/MacExt/Divy-cache/T124/staging/pr-es.json', ...fs.readdirSync(`${E}/prev`).filter((f) => f.startsWith('es-')).map((f) => `${E}/prev/${f}`)] },
  med: { cat: `${E}/cat/med.json`, prev: [`${T}/prevision/prev-med.json`, ...fs.readdirSync(`${E}/prev`).filter((f) => f.startsWith('med-')).map((f) => `${E}/prev/${f}`)] },
  eu: { cat: `${E}/cat/eu.json`, prev: [`${T}/prevision/prev-eu.json`, ...fs.readdirSync(`${E}/prev`).filter((f) => f.startsWith('eu-')).map((f) => `${E}/prev/${f}`)] },
  ...Object.fromEntries(['africa', 'caribe', 'filipinas', 'mrojo', 'norteamerica', 'oceania', 'sudamerica', 'sudeste'].map((r) => [r, { mundo: true, cat: `${T}/cat-main/${r}.json`, prev: [`${T}/prevision/prev-${r}.json`] }])),
};
const inc = (o, k, n = 1) => (o[k] = (o[k] ?? 0) + n);
const out = { europa: { spots: 0, horas: 0, cambian: 0, conf: 0, trans: {}, porSpot: {}, verde2: 0, rojoCorriente: 0 }, mundo: { spots: 0, horas: 0, cambian: 0, conf: 0, trans: {}, porSpot: {} } };
for (const [r, cfg] of Object.entries(PREV)) {
  const raw = JSON.parse(fs.readFileSync(cfg.cat, 'utf8'));
  const cat = Array.isArray(raw) ? raw : raw.spots;
  const o = cfg.mundo ? out.mundo : out.europa;
  o.spots += cat.length;
  const horas = new Map();
  for (const f of cfg.prev) {
    const p = JSON.parse(fs.readFileSync(f, 'utf8'));
    const from = Date.parse(p.window.from);
    for (let i = 0; i < p.window.hours; i++) horas.set(from + i * H, f);
  }
  const porPrev = {};
  for (const [t, f] of horas) (porPrev[f] ??= []).push(t);
  for (const [f, ts] of Object.entries(porPrev)) {
    const p = JSON.parse(fs.readFileSync(f, 'utf8'));
    const now = Date.parse(p.generatedAt);
    A.resetForecast(); A.setForecast(p); B.resetForecast(); B.setForecast(p);
    for (const s of cat) {
      const sa = { ...s, distanceKm: 0, ...(CA[s.id] ? { corr: CA[s.id] } : {}) };
      const sb = { ...s, distanceKm: 0, ...(CB[s.id] ? { corr: CB[s.id] } : {}) };
      for (const h of ts) {
        const a = A.evaluate(sa, h, [], now), b = B.evaluate(sb, h, [], now);
        if (a.level === 'sin-datos' && b.level === 'sin-datos') continue;
        o.horas++;
        if (a.confidence !== b.confidence) o.conf++;
        if (!cfg.mundo && CB[s.id]?.hab && b.current?.source === 'copernicus' && b.current.mean >= 2 && b.level === 'buena') o.verde2++;
        if (!cfg.mundo && b.level === 'dificil' && b.limita === 'corriente') o.rojoCorriente++;
        if (a.level === b.level) continue;
        o.cambian++; inc(o.trans, `${a.level}>${b.level}`);
        const ps = (o.porSpot[`${r}/${s.id}`] ??= { name: s.name, n: 0, trans: {} });
        ps.n++; inc(ps.trans, `${a.level}>${b.level}`);
      }
    }
  }
}
for (const o of Object.values(out)) o.spotsCambian = Object.keys(o.porSpot).length;
fs.writeFileSync(`${E}/salida/revalida-${nombre}.json`, JSON.stringify(out, null, 1));
const res = (o) => ({ ...o, porSpot: undefined });
console.log(nombre, JSON.stringify({ europa: res(out.europa), mundo: res(out.mundo) }));
