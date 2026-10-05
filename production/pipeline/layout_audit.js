// Checks every subtitle against the on-screen graphics: reports any visible element
// that overlaps the subtitle text at any moment (sampled every 0.1 s).
//   node pipeline/layout_audit.js [--start 0 --end 700]
const { chromium } = require('playwright');
const http = require('http');
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const args = Object.fromEntries(process.argv.slice(2).reduce((acc, a, i, arr) => {
  if (a.startsWith('--')) acc.push([a.slice(2), arr[i + 1]]);
  return acc;
}, []));
const MIME = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.woff2': 'font/woff2', '.jpg': 'image/jpeg', '.png': 'image/png' };

(async () => {
  const srv = http.createServer((req, res) => {
    const p = path.join(ROOT, decodeURIComponent(req.url.split('?')[0]));
    if (!p.startsWith(ROOT) || !fs.existsSync(p) || fs.statSync(p).isDirectory()) { res.writeHead(404); res.end(); return; }
    res.writeHead(200, { 'Content-Type': MIME[path.extname(p).toLowerCase()] || 'application/octet-stream' });
    fs.createReadStream(p).pipe(res);
  });
  await new Promise((r) => srv.listen(0, '127.0.0.1', r));
  const browser = await chromium.launch({ args: ['--font-render-hinting=none'] });
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  page.on('pageerror', (e) => console.error('[pageerror]', e.message));
  await page.goto(`http://127.0.0.1:${srv.address().port}/web/index.html`);
  const start = Number(args.start || 0), end = Number(args.end || 9999);
  const res = await page.evaluate(async ([a, b]) => {
    const dur = Math.min(b, window.TL.duration);
    await window.setupRange(a, dur);
    const found = {};
    let maxW = 0, samples = 0;
    for (let t = a; t < dur; t += 0.1) {
      const r = window.auditAt(t);
      if (!r) continue;
      samples++;
      maxW = Math.max(maxW, r.box.r - r.box.l);
      for (const h of r.hits) {
        const key = `${h.shot.toFixed(2)}|${h.kind}|${h.text}|${h.top}`;
        if (!found[key]) found[key] = { ...h, t0: t, t1: t, n: 0 };
        found[key].t1 = t; found[key].n++;
      }
    }
    return { hits: Object.values(found), maxW, samples };
  }, [start, end]);
  console.log(`samples with subtitles: ${res.samples}; widest subtitle ${Math.round(res.maxW)} px`);
  res.hits.sort((x, y) => x.t0 - y.t0);
  for (const h of res.hits) console.log(`${h.t0.toFixed(1)}-${h.t1.toFixed(1)}s  shot@${h.shot.toFixed(1)}  ${h.kind}  [${h.top}-${h.bottom}] x[${h.left}-${h.right}]  ${h.text}`);
  console.log(`${res.hits.length} overlapping elements`);
  await browser.close();
  srv.close();
})().catch((e) => { console.error(e); process.exit(1); });
