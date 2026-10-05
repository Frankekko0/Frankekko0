// Screenshot web/thumbnail*.html -> deliverables/thumbnail*.png (1280x720)
const { chromium } = require('playwright');
const http = require('http'); const fs = require('fs'); const path = require('path');
const ROOT = path.resolve(__dirname, '..');
const srv = http.createServer((q, r) => { const p = path.join(ROOT, decodeURIComponent(q.url.split('?')[0]));
  if (!fs.existsSync(p)) { r.writeHead(404); return r.end(); } r.end(fs.readFileSync(p)); });
srv.listen(0, async () => {
  const b = await chromium.launch(); const pg = await b.newPage({ viewport: { width: 1280, height: 720 } });
  for (const name of fs.readdirSync(path.join(ROOT, 'web')).filter((f) => /^thumbnail.*\.html$/.test(f))) {
    await pg.goto(`http://127.0.0.1:${srv.address().port}/web/${name}`);
    await pg.evaluate(() => document.fonts.ready); await pg.waitForTimeout(300);
    await pg.screenshot({ path: path.join(ROOT, 'deliverables', name.replace('.html', '.png')) });
    console.log('written', name.replace('.html', '.png'));
  }
  await b.close(); srv.close();
});
