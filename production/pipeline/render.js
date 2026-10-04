// Renders the motion-graphics timeline frame by frame with headless Chromium.
//   node pipeline/render.js --start 0 --end 30 --out build/seg_000.mp4
//   node pipeline/render.js --stills 12.5,40,61 --outdir build/stills
const { chromium } = require('playwright');
const http = require('http');
const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');

const ROOT = path.resolve(__dirname, '..');
const args = Object.fromEntries(process.argv.slice(2).reduce((acc, a, i, arr) => {
  if (a.startsWith('--')) acc.push([a.slice(2), arr[i + 1] && !arr[i + 1].startsWith('--') ? arr[i + 1] : true]);
  return acc;
}, []));
const FPS = Number(args.fps || 30);

const MIME = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.woff2': 'font/woff2',
  '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png', '.json': 'application/json', '.svg': 'image/svg+xml' };
function serve() {
  return new Promise((resolve) => {
    const srv = http.createServer((req, res) => {
      const p = path.join(ROOT, decodeURIComponent(req.url.split('?')[0]));
      if (!p.startsWith(ROOT) || !fs.existsSync(p) || fs.statSync(p).isDirectory()) { res.writeHead(404); res.end(); return; }
      res.writeHead(200, { 'Content-Type': MIME[path.extname(p).toLowerCase()] || 'application/octet-stream' });
      fs.createReadStream(p).pipe(res);
    });
    srv.listen(0, '127.0.0.1', () => resolve(srv));
  });
}

(async () => {
  const srv = await serve();
  const port = srv.address().port;
  const browser = await chromium.launch({ args: ['--force-color-profile=srgb', '--font-render-hinting=none', '--disable-lcd-text', '--hide-scrollbars'] });
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1 });
  page.on('console', (m) => { if (m.type() === 'error') console.error('[page]', m.text()); });
  page.on('pageerror', (e) => console.error('[pageerror]', e.message));
  await page.goto(`http://127.0.0.1:${port}/web/index.html`);
  const shoot = () => page.screenshot({ type: 'jpeg', quality: 93, clip: { x: 0, y: 0, width: 1920, height: 1080 } });

  if (args.stills) {
    const times = String(args.stills).split(',').map(Number);
    const info = await page.evaluate(([a, b]) => window.setupRange(a, b), [Math.min(...times), Math.max(...times)]);
    if (info.missing.length) console.error('MISSING IMAGES', info.missing);
    const outdir = path.resolve(ROOT, args.outdir || 'build/stills');
    fs.mkdirSync(outdir, { recursive: true });
    for (const t of times) {
      await page.evaluate((tt) => window.renderAt(tt), t);
      fs.writeFileSync(path.join(outdir, `t${t.toFixed(2).padStart(7, '0')}.jpg`), await shoot());
    }
    console.log('stills', times.length, '->', outdir);
  } else {
    const start = Number(args.start || 0), end = Number(args.end);
    const f0 = Math.round(start * FPS), f1 = Math.round(end * FPS);
    const info = await page.evaluate(([a, b]) => window.setupRange(a, b), [start, end]);
    if (info.missing.length) console.error('MISSING IMAGES', info.missing);
    console.log(`segment ${start}-${end}s: ${info.shots} shots, ${info.images} images`);
    const out = path.resolve(ROOT, args.out);
    const ff = spawn('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'image2pipe', '-framerate', String(FPS), '-c:v', 'mjpeg', '-i', '-',
      '-c:v', 'libx264', '-preset', 'medium', '-crf', '15', '-pix_fmt', 'yuv420p', '-r', String(FPS), out], { stdio: ['pipe', 'inherit', 'inherit'] });
    const t0 = Date.now();
    for (let f = f0; f < f1; f++) {
      await page.evaluate((tt) => window.renderAt(tt), f / FPS);
      const buf = await shoot();
      if (!ff.stdin.write(buf)) await new Promise((r) => ff.stdin.once('drain', r));
      if ((f - f0) % 300 === 0) console.log(`  frame ${f - f0}/${f1 - f0}  ${(((Date.now() - t0) / 1000) / Math.max(f - f0, 1) * 1000).toFixed(0)} ms/frame`);
    }
    ff.stdin.end();
    await new Promise((r) => ff.on('close', r));
    console.log('done', out, ((Date.now() - t0) / 1000).toFixed(0) + 's');
  }
  await browser.close();
  srv.close();
})().catch((e) => { console.error(e); process.exit(1); });
