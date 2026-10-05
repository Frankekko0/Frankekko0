/* Frame-accurate motion-graphics engine.
 * The page is driven externally: setupRange(t0, t1) mounts the shots that are
 * visible in that window, then renderAt(t) poses every element for time t.
 * Nothing animates on its own (no CSS transitions), so every frame is deterministic.
 */
(() => {
  const W = 1920, H = 1080;
  const TL = window.TL;
  const stage = document.getElementById('stage');

  // ---------- helpers ----------
  const clamp = (x, a = 0, b = 1) => Math.max(a, Math.min(b, x));
  const lerp = (a, b, t) => a + (b - a) * t;
  const EASE = {
    linear: (t) => t,
    in: (t) => t * t * t,
    out: (t) => 1 - Math.pow(1 - t, 3),
    inOut: (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2),
    sine: (t) => -(Math.cos(Math.PI * t) - 1) / 2,
    outExpo: (t) => (t >= 1 ? 1 : 1 - Math.pow(2, -10 * t)),
    outBack: (t) => { const c1 = 1.4, c3 = c1 + 1; return 1 + c3 * Math.pow(t - 1, 3) + c1 * Math.pow(t - 1, 2); },
  };
  const prog = (t, t0, d) => clamp((t - t0) / Math.max(d, 1e-6));
  // keyframes: [[t, v0, v1, ...], ...] -> interpolated values (eased per segment)
  function kf(keys, t, easeName = 'inOut') {
    if (!keys || !keys.length) return null;
    if (t <= keys[0][0]) return keys[0].slice(1);
    const last = keys[keys.length - 1];
    if (t >= last[0]) return last.slice(1);
    for (let i = 0; i < keys.length - 1; i++) {
      const a = keys[i], b = keys[i + 1];
      if (t >= a[0] && t <= b[0]) {
        const p = EASE[easeName]((t - a[0]) / Math.max(b[0] - a[0], 1e-6));
        return a.slice(1).map((v, j) => (typeof v === 'number' ? lerp(v, b[j + 1], p) : (p < 0.5 ? v : b[j + 1])));
      }
    }
    return last.slice(1);
  }
  function markup(s) {
    return String(s)
      .replace(/\*\*(.+?)\*\*/g, '<span class="hl">$1</span>')
      .replace(/~~(.+?)~~/g, '<span class="hl-r">$1</span>')
      .replace(/__(.+?)__/g, '<span class="hl-c">$1</span>')
      .replace(/\n/g, '<br>');
  }
  function segments(s) { // for typewriter: [{ch, cls}]
    const out = [];
    const re = /\*\*(.+?)\*\*|~~(.+?)~~|__(.+?)__|([^*~_]+|[*~_])/g;
    let m;
    while ((m = re.exec(s))) {
      const [txt, cls] = m[1] ? [m[1], 'hl'] : m[2] ? [m[2], 'hl-r'] : m[3] ? [m[3], 'hl-c'] : [m[4], ''];
      for (const ch of txt) out.push({ ch, cls });
    }
    return out;
  }
  function fmtNum(v, f = {}) {
    const dec = f.dec ?? 0;
    let s = Math.abs(v).toFixed(dec);
    if (f.comma) { const [i, d] = s.split('.'); s = i.replace(/\B(?=(\d{3})+(?!\d))/g, ',') + (d ? '.' + d : ''); }
    return (v < 0 ? '-' : '') + (f.pre || '') + s + (f.suf || '');
  }
  function css(el, style) { if (style) for (const k in style) el.style[k] = style[k]; }

  const imgs = [];
  function mkImg(src) {
    const im = new Image();
    im.decoding = 'sync';
    im.src = src;
    imgs.push(im);
    return im;
  }

  // ---------- stock data ----------
  const DAY = 86400000;
  const dnum = (s) => Date.parse(s + 'T00:00:00Z') / DAY;
  const SERIES = {};
  for (const k in (TL.data || {})) SERIES[k] = TL.data[k].map(([d, c]) => [dnum(d), c]);
  function priceAt(series, d) {
    let lo = 0, hi = series.length - 1;
    if (d <= series[0][0]) return series[0][1];
    if (d >= series[hi][0]) return series[hi][1];
    while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (series[mid][0] <= d) lo = mid; else hi = mid; }
    const a = series[lo], b = series[hi];
    return lerp(a[1], b[1], (d - a[0]) / (b[0] - a[0]));
  }
  const MONTHS = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'];

  // ---------- element factories ----------
  function mountEl(spec, parent, shot) {
    const e = { spec, shot };
    let node;
    const k = spec.kind || 'text';
    if (k === 'text' || k === 'counter' || k === 'html') {
      node = document.createElement('div');
      node.className = 'el ' + (spec.cls || '');
      if (spec.wrap) { node.classList.add('wrap'); node.style.width = spec.wrap + 'px'; }
      if (k === 'html') node.innerHTML = spec.html;
      else if (spec.anim === 'type') {
        e.chars = segments(spec.text).map(({ ch, cls }) => {
          if (ch === '\n') { node.appendChild(document.createElement('br')); return null; }
          const sp = document.createElement('span');
          sp.textContent = ch; if (cls) sp.className = cls; node.appendChild(sp); return sp;
        }).filter(Boolean);
      } else if (k === 'counter') node.textContent = fmtNum(spec.from ?? 0, spec.fmt);
      else node.innerHTML = markup(spec.text);
      if (spec.align) node.style.textAlign = spec.align;
    } else if (k === 'img') {
      node = document.createElement('div');
      node.className = 'el ' + (spec.cls || '');
      node.style.width = spec.w + 'px'; node.style.height = spec.h + 'px'; node.style.overflow = 'hidden';
      const im = mkImg(spec.src);
      im.style.cssText = 'width:100%;height:100%;object-fit:cover;display:block;transform-origin:50% 50%;';
      if (spec.pos) im.style.objectPosition = spec.pos;
      node.appendChild(im); e.im = im;
    } else if (k === 'rect' || k === 'block') {
      node = document.createElement('div');
      node.className = (k === 'block' ? 'el block ' : 'el ') + (spec.cls || '');
      node.style.width = spec.w + 'px'; node.style.height = spec.h + 'px';
      if (spec.html || spec.text) node.innerHTML = spec.html || markup(spec.text);
    } else if (k === 'path' || k === 'circle') {
      const ns = 'http://www.w3.org/2000/svg';
      node = document.createElementNS(ns, 'svg');
      node.setAttribute('class', 'el'); node.setAttribute('width', W); node.setAttribute('height', H);
      node.style.left = '0px'; node.style.top = '0px';
      if (spec.arrow) {
        const defs = document.createElementNS(ns, 'defs');
        const mk = document.createElementNS(ns, 'marker');
        const id = 'ah' + Math.random().toString(36).slice(2);
        mk.setAttribute('id', id); mk.setAttribute('viewBox', '0 0 10 10'); mk.setAttribute('refX', '7'); mk.setAttribute('refY', '5');
        mk.setAttribute('markerWidth', '5'); mk.setAttribute('markerHeight', '5'); mk.setAttribute('orient', 'auto-start-reverse');
        const p = document.createElementNS(ns, 'path'); p.setAttribute('d', 'M 0 0 L 10 5 L 0 10 z'); p.setAttribute('fill', spec.stroke || '#fff');
        mk.appendChild(p); defs.appendChild(mk); node.appendChild(defs); e.markerId = id;
      }
      let g;
      if (k === 'path') { g = document.createElementNS(ns, 'path'); g.setAttribute('d', spec.d); g.setAttribute('fill', spec.fill || 'none'); }
      else { g = document.createElementNS(ns, 'circle'); g.setAttribute('cx', spec.cx); g.setAttribute('cy', spec.cy); g.setAttribute('r', spec.r); g.setAttribute('fill', spec.fill || 'none'); }
      g.setAttribute('stroke', spec.stroke || '#fff'); g.setAttribute('stroke-width', spec.sw || 4);
      g.setAttribute('stroke-linecap', 'round'); g.setAttribute('stroke-linejoin', 'round');
      if (spec.dash) g.setAttribute('stroke-dasharray', spec.dash);
      if (e.markerId) g.setAttribute('marker-end', `url(#${e.markerId})`);
      node.appendChild(g); e.g = g;
      if (spec.anim === 'draw') e.len = null;
    } else if (k === 'stock') {
      node = document.createElement('canvas');
      node.className = 'el'; node.width = spec.w; node.height = spec.h;
      e.ctx = node.getContext('2d');
    } else if (k === 'doc') {
      node = document.createElement('div');
      node.className = 'el'; node.style.left = '0px'; node.style.top = '0px'; node.style.width = W + 'px'; node.style.height = H + 'px';
      const wrap = document.createElement('div'); wrap.className = 'docwrap';
      wrap.style.width = spec.pw + 'px'; wrap.style.height = spec.ph + 'px';
      const page = document.createElement('div'); page.className = 'docpage';
      page.style.width = spec.pw + 'px'; page.style.height = spec.ph + 'px';
      page.appendChild(mkImg(spec.src)); wrap.appendChild(page);
      e.markers = [];
      for (const hl of spec.hl || []) {
        for (const [x0, y0, x1, y1] of hl.boxes) {
          const m = document.createElement('div'); m.className = 'marker ' + (hl.cls || '');
          const pad = 7;
          css(m, { left: (x0 - pad) + 'px', top: (y0 - pad + 2) + 'px', width: (x1 - x0 + 2 * pad) + 'px', height: (y1 - y0 + 2 * pad - 2) + 'px' });
          wrap.appendChild(m); e.markers.push({ m, hl });
        }
      }
      node.appendChild(wrap); e.wrap = wrap;
    } else if (k === 'ticker') {
      node = document.createElement('div');
      node.className = 'el mono';
      css(node, { left: '0px', top: spec.y + 'px', width: W + 'px', height: (spec.h || 64) + 'px', overflow: 'hidden', background: spec.bg || 'rgba(0,0,0,0.78)', borderTop: '1px solid rgba(255,255,255,0.15)', borderBottom: '1px solid rgba(255,255,255,0.15)' });
      const inner = document.createElement('div');
      inner.innerHTML = markup((spec.text + '  ').repeat(spec.repeat || 8));
      css(inner, { position: 'absolute', left: '0px', top: '0px', lineHeight: (spec.h || 64) + 'px', fontSize: (spec.fs || 30) + 'px', whiteSpace: 'nowrap', color: '#e9e6df', letterSpacing: '0.04em' });
      node.appendChild(inner); e.inner = inner;
    } else if (k === 'gridbg') {
      node = document.createElement('div'); node.className = 'grid'; node.style.position = 'absolute';
    } else {
      throw new Error('unknown kind ' + k);
    }
    if (spec.style) css(node, spec.style);
    e.baseOp = spec.style && spec.style.opacity != null ? parseFloat(spec.style.opacity) : 1;
    if (!['path', 'circle', 'doc', 'ticker', 'gridbg'].includes(k)) {
      if (spec.x != null) node.style.left = spec.x + 'px';
      if (spec.y != null) node.style.top = spec.y + 'px';
    }
    e.anchor = spec.anchor || (k === 'text' || k === 'counter' || k === 'html' ? 'c' : 'tl');
    e.node = node;
    parent.appendChild(node);
    return e;
  }

  const ANCHOR = { c: '-50%,-50%', tl: '0,0', l: '0,-50%', r: '-100%,-50%', t: '-50%,0', b: '-50%,-100%', bl: '0,-100%', br: '-100%,-100%', tr: '-100%,0' };

  function drawStock(e, lt) {
    const s = e.spec, ctx = e.ctx, w = s.w, h = s.h;
    const series = SERIES[s.series || 'ene'];
    const pad = s.pad || { l: 110, r: 150, t: 60, b: 80 };
    if (!e.viewN) {
      const toN = (d) => (typeof d === 'string' ? dnum(d) : d);
      e.viewN = s.view.map((k) => [k[0], toN(k[1]), toN(k[2]), k[3], k[4]]);
      e.revN = s.reveal.map(([t, d]) => [t, toN(d)]);
    }
    const view = kf(e.viewN, lt, s.viewEase || 'inOut');
    const v0 = view[0], vv1 = view[1];
    const ymin = view[2], ymax = view[3];
    const rv = kf(e.revN, lt, s.revealEase || 'linear')[0];
    const X = (d) => pad.l + (d - v0) / (vv1 - v0) * (w - pad.l - pad.r);
    const Y = (p) => pad.t + (1 - (p - ymin) / (ymax - ymin)) * (h - pad.t - pad.b);
    ctx.clearRect(0, 0, w, h);
    // grid + y labels
    const step = s.ystep || 10;
    ctx.font = '500 24px "IBM Plex Mono"'; ctx.textBaseline = 'middle';
    for (let p = Math.ceil(ymin / step) * step; p <= ymax + 1e-6; p += step) {
      const y = Y(p);
      ctx.strokeStyle = 'rgba(255,255,255,0.08)'; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(w - pad.r + 40, y); ctx.stroke();
      ctx.fillStyle = 'rgba(255,255,255,0.45)'; ctx.textAlign = 'right';
      ctx.fillText('$' + (step < 1 ? p.toFixed(2) : p.toFixed(0)), pad.l - 18, y);
    }
    // x labels: years or months
    const span = vv1 - v0;
    ctx.textAlign = 'center'; ctx.textBaseline = 'top'; ctx.fillStyle = 'rgba(255,255,255,0.5)';
    const dt0 = new Date(v0 * DAY), dt1 = new Date(vv1 * DAY);
    if (span > 500) {
      for (let y = dt0.getUTCFullYear(); y <= dt1.getUTCFullYear() + 1; y++) {
        const d = Date.UTC(y, 0, 1) / DAY; if (d < v0 || d > vv1) continue;
        const x = X(d);
        ctx.strokeStyle = 'rgba(255,255,255,0.06)'; ctx.beginPath(); ctx.moveTo(x, pad.t); ctx.lineTo(x, h - pad.b); ctx.stroke();
        ctx.fillText(String(y), x, h - pad.b + 18);
      }
    } else {
      for (let y = dt0.getUTCFullYear(); y <= dt1.getUTCFullYear(); y++) for (let m = 0; m < 12; m++) {
        const d = Date.UTC(y, m, 1) / DAY; if (d < v0 || d > vv1) continue;
        if (span > 200 && m % 2) continue;
        const x = X(d);
        ctx.strokeStyle = 'rgba(255,255,255,0.06)'; ctx.beginPath(); ctx.moveTo(x, pad.t); ctx.lineTo(x, h - pad.b); ctx.stroke();
        ctx.fillText(MONTHS[m] + (m === 0 ? " '" + String(y).slice(2) : ''), x, h - pad.b + 18);
      }
    }
    // line
    const pts = series.filter(([d]) => d >= v0 - 3 && d <= Math.min(rv, vv1 + 3));
    if (pts.length < 2) return;
    const head = [rv, priceAt(series, rv)];
    const redFrom = s.redFrom ? dnum(s.redFrom) : Infinity;
    const col = s.color || '#e8b04b';
    const drawSeg = (filterFn, color) => {
      ctx.beginPath(); let started = false;
      for (const [d, p] of pts) { if (!filterFn(d)) continue; const x = X(d), y = Y(p); if (!started) { ctx.moveTo(x, y); started = true; } else ctx.lineTo(x, y); }
      if (head[0] > pts[pts.length - 1][0] && filterFn(head[0])) ctx.lineTo(X(head[0]), Y(head[1]));
      ctx.strokeStyle = color; ctx.lineWidth = s.lw || 5; ctx.lineJoin = 'round'; ctx.lineCap = 'round';
      ctx.shadowColor = color; ctx.shadowBlur = 18; ctx.stroke(); ctx.shadowBlur = 0;
    };
    // area fill
    const grad = ctx.createLinearGradient(0, pad.t, 0, h - pad.b);
    grad.addColorStop(0, (head[0] >= redFrom ? 'rgba(229,72,77,0.22)' : 'rgba(232,176,75,0.20)'));
    grad.addColorStop(1, 'rgba(0,0,0,0)');
    ctx.beginPath(); ctx.moveTo(X(pts[0][0]), Y(ymin));
    for (const [d, p] of pts) ctx.lineTo(X(d), Y(p));
    ctx.lineTo(X(head[0]), Y(head[1])); ctx.lineTo(X(head[0]), Y(ymin)); ctx.closePath(); ctx.fillStyle = grad; ctx.fill();
    drawSeg((d) => d <= redFrom + 0.5, col);
    if (redFrom < Infinity) drawSeg((d) => d >= redFrom - 0.5, '#e5484d');
    // annotations
    for (const a of s.ann || []) {
      if (lt < a.t0) continue;
      const p = EASE.out(prog(lt, a.t0, 0.5));
      const d = dnum(a.d), price = a.p ?? priceAt(series, d);
      const x = X(d), y = Y(price);
      const ly = y + (a.dy ?? -90), lx = x + (a.dx ?? 0);
      ctx.globalAlpha = p;
      ctx.strokeStyle = a.color || 'rgba(255,255,255,0.7)'; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(lerp(x, lx, p), lerp(y, ly, p)); ctx.stroke();
      ctx.fillStyle = a.color || '#fff'; ctx.beginPath(); ctx.arc(x, y, 9, 0, Math.PI * 2); ctx.fill();
      ctx.font = `600 ${a.fs || 30}px "Oswald"`; ctx.textAlign = a.align || 'center'; ctx.textBaseline = (a.dy ?? -90) < 0 ? 'bottom' : 'top';
      ctx.fillStyle = a.color || '#fff';
      const lines = a.label.split('\n');
      lines.forEach((ln, i) => ctx.fillText(ln, lx, ly + ((a.dy ?? -90) < 0 ? -8 - (lines.length - 1 - i) * (a.fs || 30) * 1.15 : 8 + i * (a.fs || 30) * 1.15)));
      ctx.globalAlpha = 1;
    }
    // head dot + price (the price label hides once an annotation sits on the same point)
    if (s.head !== false) {
      const hx = X(head[0]), hy = Y(head[1]);
      const hc = head[0] >= redFrom ? '#e5484d' : col;
      ctx.fillStyle = hc; ctx.shadowColor = hc; ctx.shadowBlur = 30;
      ctx.beginPath(); ctx.arc(hx, hy, 10, 0, Math.PI * 2); ctx.fill(); ctx.shadowBlur = 0;
      const covered = (s.ann || []).some((a) => lt >= a.t0 && Math.abs(dnum(a.d) - head[0]) < 2);
      if (s.headLabel !== false && !covered) {
        ctx.font = '600 40px "IBM Plex Mono"'; ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
        ctx.fillStyle = '#fff';
        const lbl = '$' + head[1].toFixed(2);
        ctx.fillText(lbl, Math.min(hx + 24, w - 210), clamp(hy - 34, pad.t + 20, h - pad.b - 20));
      }
    }
  }

  function updateEl(e, lt, shotDur) {
    const s = e.spec;
    const t0 = s.t0 ?? 0, ad = s.ad ?? (s.anim === 'type' ? 1.0 : 0.45);
    const t1 = s.t1 ?? Infinity, aod = s.aod ?? 0.25;
    const node = e.node;
    if (lt < t0 || lt > t1 + aod) { node.style.display = 'none'; return; }
    node.style.display = '';
    const pin = EASE[s.ease || 'out'](prog(lt, t0, ad));
    const pout = t1 === Infinity ? 0 : EASE.inOut(prog(lt, t1, aod));
    let op = 1, tx = 0, ty = 0, sc = 1, rot = 0, blur = 0, clip = null;
    const a = s.anim || 'up';
    if (a === 'fade') op = pin;
    else if (a === 'up') { op = pin; ty = (1 - pin) * (s.dist ?? 40); }
    else if (a === 'down') { op = pin; ty = -(1 - pin) * (s.dist ?? 40); }
    else if (a === 'left') { op = pin; tx = (1 - pin) * (s.dist ?? 60); }
    else if (a === 'right') { op = pin; tx = -(1 - pin) * (s.dist ?? 60); }
    else if (a === 'pop') { const p = EASE.outBack(prog(lt, t0, ad)); op = clamp(pin * 1.5); sc = lerp(0.82, 1, p); }
    else if (a === 'zoom') { op = pin; sc = lerp(s.from_s ?? 1.35, 1, pin); }
    else if (a === 'stamp') { const p = EASE.out(prog(lt, t0, ad * 0.6)); op = clamp(p * 2); sc = lerp(1.8, 1, p); rot = lerp(-4, s.rot ?? -2, p); }
    else if (a === 'blur') { op = pin; blur = (1 - pin) * 18; }
    else if (a === 'wipe') clip = `inset(-20px ${(1 - pin) * 100}% -20px -20px)`;
    else if (a === 'wipeU') clip = `inset(${(1 - pin) * 100}% -20px -20px -20px)`;
    else if (a === 'wipeD') clip = `inset(-20px -20px ${(1 - pin) * 100}% -20px)`;
    else if (a === 'type') {
      const n = Math.floor(prog(lt, t0, ad) * e.chars.length + 1e-6);
      e.chars.forEach((c, i) => { c.style.opacity = i < n ? 1 : 0; });
    } else if (a === 'grow') { node.style.transformOrigin = s.origin || '50% 100%'; const p = pin; sc = 1; node.style.clipPath = ''; e._grow = p; }
    else if (a === 'growX') { node.style.transformOrigin = '0% 50%'; e._growX = pin; }
    else if (a === 'flicker') { const f = Math.floor(lt * 30); op = lt - t0 < 0.35 ? ((f * 7919) % 3 === 0 ? 0.25 : 1) : 1; }
    else if (a === 'draw') { /* handled below */ }
    // out animation
    const ao = s.aout || 'fade';
    if (pout > 0) {
      if (ao === 'fade') op *= 1 - pout;
      else if (ao === 'up') { op *= 1 - pout; ty -= pout * 30; }
      else if (ao === 'down') { op *= 1 - pout; ty += pout * 30; }
      else if (ao === 'blur') { op *= 1 - pout; blur += pout * 14; }
      else if (ao === 'none') op = lt > t1 ? 0 : op;
    }
    // keyframed moves (relative to base position)
    if (s.kf) {
      const v = kf(s.kf, lt, s.kfEase || 'inOut');
      if (v) { tx += v[0] || 0; ty += v[1] || 0; sc *= v[2] ?? 1; if (v[3] != null) op *= v[3]; }
    }
    if (s.drift) { const p = (lt - t0) / Math.max(shotDur - t0, 0.5); tx += s.drift[0] * p; ty += s.drift[1] * p; }
    if (s.scale) { const p = clamp((lt - t0) / Math.max(shotDur - t0, 0.5)); sc *= lerp(s.scale[0], s.scale[1], p); }
    if (s.pulse) sc *= 1 + s.pulse * Math.sin((lt - t0) * Math.PI * 2 * (s.pulseHz || 1.2));
    node.style.opacity = op * e.baseOp;
    if (blur > 0.05) node.style.filter = `blur(${blur.toFixed(2)}px)`; else if (node.style.filter) node.style.filter = '';
    if (clip) node.style.clipPath = clip; else if (!['grow', 'growX'].includes(a) && node.style.clipPath) node.style.clipPath = '';
    const k = s.kind || 'text';
    if (k === 'path' || k === 'circle') {
      if (a === 'draw') {
        if (e.len == null) e.len = e.g.getTotalLength();
        e.g.style.strokeDasharray = s.dash ? s.dash : e.len;
        if (!s.dash) e.g.style.strokeDashoffset = e.len * (1 - pin);
        else node.style.clipPath = `inset(-50px ${(1 - pin) * 100}% -50px -50px)`;
        node.style.opacity = (1 - pout);
      }
      node.style.transform = `translate(${tx}px,${ty}px)`;
      return;
    }
    if (k === 'counter') {
      const p = EASE[s.cease || 'out'](prog(lt, t0 + (s.cdelay || 0), s.cdur ?? ad));
      node.textContent = fmtNum(lerp(s.from ?? 0, s.to, p), s.fmt);
    }
    if (k === 'stock') { drawStock(e, lt); }
    if (k === 'doc') {
      const v = kf(s.cam, lt, 'inOut');
      const [cx, cy, zoom, rotd] = v;
      const kk = (s.baseW || 900) / s.pw * zoom;
      e.wrap.style.transform = `translate(${W / 2 - cx * kk}px, ${H / 2 - cy * kk}px) scale(${kk}) rotate(${rotd || 0}deg)`;
      for (const { m, hl } of e.markers) {
        const p = EASE.inOut(prog(lt, hl.t0, hl.d ?? 0.8));
        m.style.clipPath = `inset(0 ${(1 - p) * 100}% 0 0)`;
        m.style.opacity = lt < hl.t0 ? 0 : 1;
      }
    }
    if (k === 'ticker') { e.inner.style.transform = `translateX(${-((lt * (s.speed || 120)) % 4000)}px)`; }
    if (k === 'img' && s.kb) { const p = clamp((lt - t0) / Math.max(shotDur - t0, 0.5)); e.im.style.transform = `scale(${lerp(s.kb[0], s.kb[1], EASE.sine(p))})`; }
    let tf = `translate(${ANCHOR[e.anchor]}) translate(${tx}px,${ty}px)`;
    if (sc !== 1) tf += ` scale(${sc})`;
    if (rot) tf += ` rotate(${rot}deg)`;
    if (a === 'grow') tf += ` scaleY(${Math.max(e._grow, 0.0001)})`;
    if (a === 'growX') tf += ` scaleX(${Math.max(e._growX, 0.0001)})`;
    node.style.transform = tf;
  }

  // ---------- shots ----------
  const shots = [];
  function mountShot(sp) {
    const div = document.createElement('div');
    div.className = 'shot';
    div.style.zIndex = sp.layer ? 10 + sp.layer : 1;
    const bg = document.createElement('div'); bg.className = 'bg';
    const sh = { sp, div, els: [] };
    const b = sp.bg || {};
    if (b.kind === 'photo') {
      const im = mkImg(b.src); im.className = 'photo';
      bg.appendChild(im); sh.photo = im;
      if (b.dim) { const d = document.createElement('div'); d.className = 'dim'; d.style.opacity = b.dim; bg.appendChild(d); }
      if (b.tint) { const d = document.createElement('div'); d.className = 'dim'; d.style.background = b.tint; bg.appendChild(d); }
    } else if (b.kind) {
      bg.classList.add('bg-' + b.kind);
      if (['dark', 'navy', 'ember'].includes(b.kind)) {
        const tint = b.kind === 'ember' ? '229,72,77' : b.kind === 'navy' ? '89,160,211' : '120,150,190';
        const g1 = document.createElement('div');
        g1.style.cssText = `position:absolute;left:-600px;top:-500px;width:1400px;height:1400px;border-radius:50%;background:radial-gradient(circle, rgba(${tint},0.13) 0%, rgba(${tint},0) 65%);`;
        const g2 = document.createElement('div');
        g2.style.cssText = `position:absolute;left:1100px;top:300px;width:1300px;height:1300px;border-radius:50%;background:radial-gradient(circle, rgba(232,176,75,0.07) 0%, rgba(232,176,75,0) 65%);`;
        bg.appendChild(g1); bg.appendChild(g2); sh.glows = [g1, g2];
      }
      if (b.grid) { const g = document.createElement('div'); g.className = 'grid'; bg.appendChild(g); sh.grid = g; }
    }
    if (sp.layer && !b.kind) bg.style.display = 'none';
    div.appendChild(bg);
    const cam = document.createElement('div'); cam.className = 'cam';
    div.appendChild(cam);
    for (const es of sp.els || []) sh.els.push(mountEl(es, es.fixed ? div : cam, sh));
    if (b.credit) { const c = document.createElement('div'); c.className = 'credit'; c.innerHTML = b.credit; div.appendChild(c); }
    if (b.tag) { const c = document.createElement('div'); c.className = 'tag'; c.innerHTML = b.tag; div.appendChild(c); }
    sh.cam = cam; sh.bg = bg;
    stage.appendChild(div);
    shots.push(sh);
  }

  function updateShot(sh, t) {
    const sp = sh.sp;
    const pre = sp.tin === 'fade' ? (sp.tinD || 0.5) : 0;
    if (t < sp.start - pre || t >= sp.end) { sh.div.style.display = 'none'; return; }
    sh.div.style.display = 'block';
    const lt = t - sp.start, dur = sp.end - sp.start;
    let op = 1;
    if (sp.tin === 'fade') op = EASE.sine(prog(t, sp.start - pre, pre));
    if (sp.fadeOut) op *= 1 - EASE.sine(prog(t, sp.end - sp.fadeOut, sp.fadeOut));
    if (sp.fadeIn) op *= EASE.sine(prog(lt, 0, sp.fadeIn));
    sh.div.style.opacity = op;
    let wt = '';
    if (sp.tin === 'whip') { const p = EASE.out(prog(lt, 0, 0.22)); wt = `translateX(${(1 - p) * 140}px)`; sh.div.style.filter = p < 1 ? `blur(${(1 - p) * 10}px)` : ''; }
    sh.div.style.transform = wt;
    const b = sp.bg || {};
    if (sh.photo && sh.photo.naturalWidth) {
      const im = sh.photo;
      const cover = Math.max(W / im.naturalWidth, H / im.naturalHeight);
      const kb = b.kb || [1.04, 0, 0, 1.12, 0, 0];
      const p = EASE[b.ease || 'sine'](clamp(lt / Math.max(dur, 0.1)));
      const s = cover * lerp(kb[0], kb[3], p), x = lerp(kb[1], kb[4], p), y = lerp(kb[2], kb[5], p);
      im.style.width = im.naturalWidth + 'px'; im.style.height = im.naturalHeight + 'px';
      im.style.transform = `translate(-50%,-50%) translate(${x}px,${y}px) scale(${s})`;
    }
    if (sh.glows) {
      const T0 = sp.start; // drift with absolute time so consecutive dark shots feel continuous
      sh.glows[0].style.transform = `translate(${Math.sin((T0 + lt) * 0.11) * 260}px, ${Math.cos((T0 + lt) * 0.08) * 160}px)`;
      sh.glows[1].style.transform = `translate(${Math.cos((T0 + lt) * 0.09) * -240}px, ${Math.sin((T0 + lt) * 0.12) * -180}px)`;
    }
    if (sh.grid) sh.grid.style.transform = `translate(${-(lt * (b.gridSpeed ?? 14)) % 80}px, ${-(lt * (b.gridSpeed ?? 14) * 0.5) % 80}px)`;
    // camera
    let ct = '';
    if (sp.cam) { const v = kf(sp.cam, lt, sp.camEase || 'inOut'); ct = `translate(${v[1]}px,${v[2]}px) scale(${v[0]})`; }
    if (sp.shake) for (const [a0, a1, amp] of sp.shake) if (lt >= a0 && lt < a1) {
      const f = Math.floor(lt * 30), k = 1 - (lt - a0) / (a1 - a0);
      ct += ` translate(${Math.sin(f * 12.9898) * amp * k}px,${Math.cos(f * 78.233) * amp * k}px)`;
    }
    sh.cam.style.transform = ct;
    for (const e of sh.els) updateEl(e, lt, dur);
  }

  // ---------- global overlays ----------
  const grain = document.getElementById('grain');
  const gctx = grain.getContext('2d');
  const GW = 480, GH = 270; grain.width = GW; grain.height = GH;
  const noiseFrames = [];
  let seed = 1337;
  const rnd = () => { seed = (seed * 16807) % 2147483647; return seed / 2147483647; };
  for (let f = 0; f < 8; f++) {
    const id = gctx.createImageData(GW, GH);
    for (let i = 0; i < GW * GH; i++) { const v = Math.floor(rnd() * 255); id.data[i * 4] = v; id.data[i * 4 + 1] = v; id.data[i * 4 + 2] = v; id.data[i * 4 + 3] = 255; }
    noiseFrames.push(id);
  }
  const flash = document.getElementById('flash');
  const black = document.getElementById('black');

  function globalFx(t) {
    gctx.putImageData(noiseFrames[Math.floor(t * 30) % noiseFrames.length], 0, 0);
    let fl = 0, bl = 0;
    for (const sh of shots) {
      const sp = sh.sp;
      if (sp.tin === 'flash') { const d = t - sp.start; if (d >= 0 && d < 0.3) fl = Math.max(fl, 0.85 * (1 - d / 0.3)); }
      if (sp.tin === 'dip') { const d = t - sp.start, D = sp.tinD || 0.45; if (d >= 0 && d < D) bl = Math.max(bl, 1 - EASE.sine(d / D)); }
      if (sp.tout === 'dip') { const D = sp.toutD || 0.45, d = sp.end - t; if (d > 0 && d <= D) bl = Math.max(bl, 1 - EASE.sine(d / D)); }
    }
    for (const [a, b, o] of TL.blackouts || []) if (t >= a && t < b) bl = Math.max(bl, o ?? 1);
    flash.style.opacity = fl; black.style.opacity = bl;
  }

  // ---------- burned-in subtitles ----------
  // One chunk at a time, centred low in frame. Every word is visible from the start of
  // its chunk (dimmed), lights up when spoken, and a gold bar slides under the word
  // being said. Keywords keep their colour and get a filled box while spoken.
  const SUBS = TL.subs || [];
  const subsEl = document.getElementById('subs');
  const scrim = document.getElementById('subscrim');
  let subLine = null, subPill = null, subBar = null, subIdx = -1, subWords = [];
  const KEY = { gold: ['#f2bd55', '#e8b04b', '#0d0f12'], red: ['#ff6d70', '#e5484d', '#ffffff'], cyan: ['#6fd8e2', '#59c9d3', '#0d0f12'] };
  function mountChunk(i) {
    subsEl.textContent = '';
    subLine = document.createElement('div'); subLine.className = 'subline';
    subBar = document.createElement('div'); subBar.className = 'subbar';
    subPill = document.createElement('div'); subPill.className = 'subpill';
    subLine.appendChild(subPill); subLine.appendChild(subBar);
    subWords = SUBS[i].words.map((wd) => {
      const sp = document.createElement('span'); sp.className = 'sw'; sp.textContent = wd.t;
      subLine.appendChild(sp); return { wd, sp };
    });
    subsEl.appendChild(subLine);
    for (const o of subWords) { o.x = o.sp.offsetLeft; o.w = o.sp.offsetWidth; }
    subIdx = i;
  }
  function subAt(t) {
    let lo = 0, hi = SUBS.length - 1, k = -1;
    while (lo <= hi) { const mid = (lo + hi) >> 1; if (SUBS[mid].s <= t) { k = mid; lo = mid + 1; } else hi = mid - 1; }
    return k;
  }
  function renderSubs(t) {
    const k = subAt(t);
    const c = k >= 0 ? SUBS[k] : null;
    const FADE = 0.16;
    let vis = 0;
    if (c && t < c.e + FADE) vis = t <= c.e ? 1 : 1 - (t - c.e) / FADE;
    // the scrim follows the subtitles but lingers through the short gaps between chunks
    let sc = 0;
    if (c) {
      const pv = SUBS[k - 1], nx = SUBS[k + 1];
      const fin = pv && c.s - pv.e < 1.2 ? 1 : clamp((t - c.s + 0.1) / 0.3);
      const fout = t <= c.e + 0.35 || (nx && nx.s - c.e < 1.2) ? 1 : clamp(1 - (t - c.e - 0.35) / 0.4);
      sc = Math.min(fin, fout);
    }
    scrim.style.opacity = sc.toFixed(3);
    if (!c || vis <= 0) { subsEl.style.display = 'none'; return null; }
    subsEl.style.display = '';
    if (subIdx !== k) mountChunk(k);
    const pin = EASE.outBack(prog(t, c.s, 0.24));
    subLine.style.opacity = (clamp(prog(t, c.s, 0.12)) * vis).toFixed(3);
    subLine.style.transform = `translateY(${((1 - pin) * 18).toFixed(2)}px) scale(${lerp(0.96, 1, clamp(pin)).toFixed(4)})`;
    // active word = last word already started
    let a = -1;
    subWords.forEach((o, i) => { if (t >= o.wd.s) a = i; });
    subWords.forEach((o, i) => {
      const key = KEY[o.wd.c];
      const spoken = i <= a;
      const active = i === a && t < o.wd.e + 0.25;
      let color = spoken ? (key ? key[0] : '#ffffff') : 'rgba(255,255,255,0.5)';
      if (active && key) color = key[2];
      o.sp.style.color = color;
      o.sp.classList.toggle('on', active && !!key);
      const p = prog(t, o.wd.s, 0.22);
      const bump = spoken && p < 1 ? Math.sin(Math.PI * p) : 0;
      o.sp.style.transform = bump ? `translateY(${(-4 * bump).toFixed(2)}px) scale(${(1 + 0.07 * bump).toFixed(4)})` : '';
    });
    // sliding bar under the active word; filled box behind an active keyword
    if (a >= 0) {
      const cur = subWords[a], prev = subWords[Math.max(a - 1, 0)];
      const m = EASE.out(prog(t, cur.wd.s, 0.11));
      const x = lerp(a > 0 ? prev.x : cur.x, cur.x, m), wdt = lerp(a > 0 ? prev.w : cur.w, cur.w, m);
      const key = KEY[cur.wd.c];
      subBar.style.opacity = '1';
      subBar.style.left = (x + 10).toFixed(1) + 'px'; subBar.style.width = Math.max(wdt - 20, 8).toFixed(1) + 'px';
      subBar.style.background = key ? key[1] : '#e8b04b';
      const on = key && t < cur.wd.e + 0.25;
      subPill.style.opacity = on ? '1' : '0';
      if (on) {
        const pp = EASE.outBack(prog(t, cur.wd.s, 0.18));
        subPill.style.left = cur.x + 'px'; subPill.style.width = cur.w + 'px'; subPill.style.background = key[1];
        subPill.style.transform = `scale(${lerp(0.82, 1, clamp(pp, 0, 1.2)).toFixed(4)})`;
      }
    } else { subBar.style.opacity = '0'; subPill.style.opacity = '0'; }
    return subLine;
  }

  // ---------- public API ----------
  window.setupRange = async (t0, t1) => {
    for (const sp of TL.shots) if (sp.end >= t0 - 1 && sp.start <= t1 + 1) mountShot(sp);
    // canvas text does not trigger webfont loading, so load every face explicitly
    const faces = ['500 24px "IBM Plex Mono"', '600 40px "IBM Plex Mono"', '400 24px "IBM Plex Mono"', '600 30px "Oswald"', '700 30px "Oswald"',
      '500 30px "Oswald"', '400 20px "Inter"', '500 20px "Inter"', '600 20px "Inter"', '700 20px "Inter"', '800 20px "Inter"',
      'italic 500 40px "Playfair Display"', '700 40px "Courier Prime"', '400 40px "Courier Prime"'];
    await Promise.all(faces.map((f) => document.fonts.load(f)));
    await document.fonts.ready;
    await Promise.all(imgs.map((im) => (im.complete ? Promise.resolve() : new Promise((r) => { im.onload = r; im.onerror = r; }))));
    await Promise.all(imgs.map((im) => im.decode().catch(() => {})));
    const missing = imgs.filter((im) => !im.naturalWidth).map((im) => im.src);
    return { shots: shots.length, images: imgs.length, missing };
  };
  window.renderAt = (t) => {
    for (const sh of shots) updateShot(sh, t);
    globalFx(t);
    renderSubs(t);
    return true;
  };
  // Layout audit: which visible elements touch the subtitle at time t.
  window.auditAt = (t) => {
    window.renderAt(t);
    const line = subsEl.style.display === 'none' ? null : subLine;
    if (!line || parseFloat(line.style.opacity) < 0.3) return null;
    const words = subWords.map((o) => o.sp.getBoundingClientRect());
    const R = { l: Math.min(...words.map((r) => r.left)) - 6, r: Math.max(...words.map((r) => r.right)) + 6,
                t: Math.min(...words.map((r) => r.top)) - 4, b: Math.max(...words.map((r) => r.bottom)) + 4 };
    const hits = [];
    const visible = (n) => { for (let p = n; p && p !== document.body; p = p.parentElement) { const cs = getComputedStyle(p); if (cs.display === 'none' || parseFloat(cs.opacity) < 0.08) return false; } return true; };
    for (const sh of shots) {
      if (sh.div.style.display === 'none') continue;
      for (const e of sh.els) {
        const k = e.spec.kind || 'text';
        if (k === 'doc' || k === 'gridbg') continue;
        let nodes = [e.node];
        if (k === 'path' || k === 'circle') nodes = [e.g];
        for (const n of nodes) {
          if (!visible(n)) continue;
          let r = n.getBoundingClientRect();
          if (k === 'stock') r = { left: r.left, right: r.right, top: r.top, bottom: r.bottom - 34 };
          if (r.right - r.left >= 1900 && r.bottom - r.top >= 1000) continue;  // full-frame washes
          if (r.right < R.l || r.left > R.r || r.bottom < R.t || r.top > R.b) continue;
          hits.push({ shot: sh.sp.start, kind: k, text: (e.spec.text || e.spec.html || '').toString().replace(/<[^>]+>/g, ' ').slice(0, 50), top: Math.round(r.top), bottom: Math.round(r.bottom), left: Math.round(r.left), right: Math.round(r.right) });
        }
      }
      for (const e of sh.els) if (e.markers) for (const { m } of e.markers) {
        if (!visible(m) || m.style.opacity === '0') continue;
        const r = m.getBoundingClientRect();
        if (r.right < R.l || r.left > R.r || r.bottom < R.t || r.top > R.b) continue;
        hits.push({ shot: sh.sp.start, kind: 'highlight', text: 'doc highlight', top: Math.round(r.top), bottom: Math.round(r.bottom) });
      }
    }
    return { box: R, hits };
  };
})();
