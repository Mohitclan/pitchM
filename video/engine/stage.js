/* PitchMirror film stage: builds one composition from window.COMPS and renders any
 * moment with window.renderAt(seconds). Pure function of time, so the renderer can step
 * frame by frame. Ideas borrowed (not code) from remotion-cinematic (camera rig, cursor),
 * remotion-product-video (browser frame, labels) and saas-product-demo-video (click math). */
(function () {
  'use strict';

  const W = 1920, H = 1080;
  const CAP = '../assets/captures/';
  const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
  const lerp = (a, b, t) => a + (b - a) * t;
  const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
  const easeOut = (t) => 1 - Math.pow(1 - t, 3);
  const ramp = (t, t0, t1) => ease(clamp((t - t0) / Math.max(1e-6, t1 - t0)));
  /** 0→1 over [t0, t0+fi], hold, 1→0 over [t1-fo, t1]. */
  const win = (t, t0, t1, fi = 0.5, fo = 0.5) => Math.min(ramp(t, t0, t0 + fi), 1 - ramp(t, t1 - fo, t1));

  const el = (tag, cls, parent, html) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (html != null) n.innerHTML = html;
    if (parent) parent.appendChild(n);
    return n;
  };

  const CURSOR_SVG = '<svg viewBox="0 0 30 30" width="30" height="30"><path d="M4 3 L4 25 L10 19.5 L14 28 L18 26.3 L14 18 L22 18 Z" fill="#111" stroke="#fff" stroke-width="2" stroke-linejoin="round"/></svg>';

  /* ── Scene builders ─────────────────────────────── */
  const builders = {
    title(s, root) {
      const box = el('div', 'headline', root, `<h1>${s.text}</h1>${s.sub ? `<p>${s.sub}</p>` : ''}`);
      return (t) => {
        const a = win(t, s.in ?? 0.25, s.out ?? s.dur - 0.1, 0.7, 0.5);
        box.style.opacity = a;
        box.style.transform = `translateY(calc(-50% + ${(1 - ramp(t, s.in ?? 0.25, (s.in ?? 0.25) + 0.9)) * 18}px))`;
        box.style.filter = `blur(${(1 - a) * 6}px)`;
      };
    },

    words(s, root) {
      const items = s.items.map((w) => ({ ...w, node: el('div', 'word', root, w.text) }));
      return (t) => items.forEach((w) => {
        const a = win(t, w.t0, w.t1, 0.35, 0.3);
        w.node.style.opacity = a;
        w.node.style.color = w.color || '';
        w.node.style.transform = `translateY(-50%) scale(${lerp(1.04, 1, ramp(t, w.t0, w.t0 + 0.6))})`;
        w.node.style.filter = `blur(${(1 - a) * 8}px)`;
      });
    },

    chips(s, root) {
      if (s.head) var head = el('div', 'chips-head', root, s.head);
      const row = el('div', 'chips', root);
      const items = s.items.map((c) => ({ ...c, node: el('div', 'chip', row, c.text) }));
      return (t) => {
        if (head) head.style.opacity = win(t, 0.2, s.dur, 0.6, 0.4);
        items.forEach((c) => {
          const a = ramp(t, c.t, c.t + 0.6);
          c.node.style.opacity = a * (1 - ramp(t, s.dur - 0.5, s.dur));
          c.node.style.transform = `translateY(${(1 - easeOut(clamp((t - c.t) / 0.7))) * 26}px)`;
          c.node.style.color = a > 0.99 && c.accent ? c.accent : '';
        });
      };
    },

    brand(s, root) {
      const box = el('div', 'brand', root, `
        <div class="mark"><span class="ring"></span><span>PitchMirror</span></div>
        ${s.tag ? `<div class="tag">${s.tag}</div>` : ''}
        ${s.cta ? `<div><span class="cta">${s.cta}</span></div>` : ''}`);
      const mark = box.querySelector('.mark');
      const tag = box.querySelector('.tag');
      const cta = box.querySelector('.cta');
      return (t) => {
        const end = s.holdToEnd ? s.dur + 1 : s.dur - 0.1;
        mark.style.opacity = win(t, 0.15, end, 0.8, 0.5);
        mark.style.transform = `scale(${lerp(0.94, 1, ramp(t, 0.15, 1.4))})`;
        if (tag) { tag.style.opacity = win(t, s.tagAt ?? 0.9, end, 0.7, 0.5); }
        if (cta) { cta.style.opacity = win(t, s.ctaAt ?? 1.6, end, 0.7, 0.5); }
        if (s.fadeOutAt != null) box.style.opacity = 1 - ramp(t, s.fadeOutAt, s.dur);
      };
    },

    screen(s, root) {
      // Layout: a browser frame centred on the stage. Capture coords are CSS px of the
      // 1920×1080 capture; k maps them into the frame.
      const fw = s.frameW || 1640;
      const k = fw / 1920;
      const chromeH = s.chrome === false ? 0 : 40;
      const fh = 1080 * k + chromeH;
      const fx = (W - fw) / 2;
      const fy = (H - fh) / 2 + (s.offsetY || 0);
      const sx = fx, sy = fy + chromeH;
      const toStage = (p) => [sx + p[0] * k, sy + p[1] * k];

      const cam = el('div', 'cam', root);
      const frame = el('div', 'frame', cam);
      Object.assign(frame.style, { left: `${fx}px`, top: `${fy}px`, width: `${fw}px`, height: `${fh}px` });
      if (chromeH) el('div', 'chrome', frame, `<i></i><i></i><i></i><span class="url">${s.url || 'pitchmirror · localhost'}</span>`);
      const screen = el('div', 'screen', frame);
      Object.assign(screen.style, { width: `${fw}px`, height: `${1080 * k}px` });
      const imgs = s.images.map((im) => {
        const node = el('img', null, screen);
        node.src = CAP + im.src + '.png';
        return { ...im, node };
      });
      const hls = (s.highlights || []).map((h) => ({ ...h, node: el('div', 'hl', cam) }));
      const ripple = el('div', 'ripple', cam);
      const cursor = s.cursor ? el('div', 'cursor', cam, CURSOR_SVG) : null;
      const labels = (s.labels || []).map((l) => ({ ...l, node: el('div', `label${l.right ? ' right' : ''}`, root, `<b>${l.text}</b>${l.sub ? `<span>${l.sub}</span>` : ''}`) }));

      function camAt(t) {
        const kf = s.cam || [{ t: 0, s: 1 }];
        let a = kf[0], b = kf[kf.length - 1];
        for (let i = 0; i < kf.length - 1; i++) if (t >= kf[i].t && t <= kf[i + 1].t) { a = kf[i]; b = kf[i + 1]; break; }
        if (t < kf[0].t) b = a;
        const u = a === b ? 0 : ease(clamp((t - a.t) / (b.t - a.t)));
        const fa = a.f ? toStage(a.f) : [W / 2, H / 2];
        const fb = b.f ? toStage(b.f) : [W / 2, H / 2];
        return { z: lerp(a.s, b.s, u), f: [lerp(fa[0], fb[0], u), lerp(fa[1], fb[1], u)] };
      }

      return (t) => {
        const c = camAt(t);
        // Keep the zoomed frame on screen: clamp the focus so edges never pull inside.
        let [Fx, Fy] = c.f;
        const halfW = W / (2 * c.z), halfH = H / (2 * c.z);
        if (c.z > 1.001) {
          Fx = clamp(Fx, Math.min(fx + halfW - 40, W / 2), Math.max(fx + fw - halfW + 40, W / 2));
          Fy = clamp(Fy, Math.min(fy + halfH - 40, H / 2), Math.max(fy + fh - halfH + 40, H / 2));
        }
        cam.style.transform = `translate(${W / 2}px, ${H / 2}px) scale(${c.z}) translate(${-Fx}px, ${-Fy}px)`;

        // Image sequence with short crossfades.
        let cur = 0;
        imgs.forEach((im, i) => { if (t >= im.t) cur = i; });
        imgs.forEach((im, i) => {
          let o = 0;
          if (i === cur) o = ramp(t, im.t, im.t + (im.xf ?? 0.22));
          if (i === cur - 1) o = 1;
          if (i === 0 && cur === 0) o = 1;
          im.node.style.opacity = o;
          im.node.style.zIndex = i === cur ? 2 : 1;
        });

        hls.forEach((h) => {
          const [x, y] = toStage([h.box[0], h.box[1]]);
          Object.assign(h.node.style, { left: `${x - 6}px`, top: `${y - 6}px`, width: `${h.box[2] * k + 12}px`, height: `${h.box[3] * k + 12}px`, borderRadius: `${(h.r ?? 12) * k + 6}px` });
          h.node.style.opacity = win(t, h.t0, h.t1, 0.4, 0.4);
        });

        if (cursor) {
          const path = s.cursor;
          let p = path[0];
          let x = p.x, y = p.y;
          for (let i = 0; i < path.length - 1; i++) {
            const a = path[i], b = path[i + 1];
            if (t >= a.t && t <= b.t) {
              const u = ease(clamp((t - a.t) / (b.t - a.t)));
              // Gentle arc, like a hand moving a mouse.
              const arc = Math.sin(u * Math.PI) * Math.min(60, Math.hypot(b.x - a.x, b.y - a.y) * 0.12);
              x = lerp(a.x, b.x, u); y = lerp(a.y, b.y, u) - arc;
              break;
            }
            if (t > b.t) { x = b.x; y = b.y; }
          }
          const [cx, cy] = toStage([x, y]);
          let press = 1;
          let rip = 0, ripX = 0, ripY = 0;
          path.forEach((pt) => {
            if (pt.click && t >= pt.t && t < pt.t + 0.6) {
              const d = t - pt.t;
              press = Math.min(press, d < 0.12 ? 1 - 0.18 * Math.sin((d / 0.12) * Math.PI) : 1);
              rip = d; [ripX, ripY] = toStage([pt.x, pt.y]);
            }
          });
          const vis = win(t, s.cursorIn ?? 0, s.cursorOut ?? s.dur, 0.3, 0.3);
          cursor.style.opacity = vis;
          cursor.style.transform = `translate(${cx - 3}px, ${cy - 3}px) scale(${press / Math.max(1, c.z * 0.55)})`;
          if (rip > 0) {
            Object.assign(ripple.style, { left: `${ripX}px`, top: `${ripY}px`, opacity: (1 - rip / 0.6) * 0.9, transform: `scale(${0.3 + rip * 1.6})` });
          } else ripple.style.opacity = 0;
        }

        labels.forEach((l) => {
          const a = win(t, l.t0, l.t1, 0.45, 0.4);
          l.node.style.opacity = a;
          l.node.style.transform = `translateY(${(1 - a) * 14}px)`;
        });
      };
    },
  };

  /* ── Composition ─────────────────────────────────── */
  const name = new URLSearchParams(location.search).get('comp') || 'ProductOverview60';
  const comp = window.COMPS[name];
  const stage = document.getElementById('stage');
  const scenes = comp.scenes.map((s) => {
    const root = el('div', `scene ${s.theme || 'light'}`, stage);
    const update = builders[s.type](s, root);
    return { ...s, root, update };
  });

  window.COMP = { name, duration: comp.duration, fps: comp.fps || 30 };

  window.renderAt = function (t) {
    scenes.forEach((s) => {
      const local = t - s.start;
      const xin = s.xin ?? 0.5, xout = s.xout ?? 0.5;
      if (local < -0.01 || local > s.dur + 0.01) { s.root.style.opacity = 0; s.root.style.visibility = 'hidden'; return; }
      s.root.style.visibility = 'visible';
      s.root.style.opacity = Math.min(xin ? ramp(local, 0, xin) : 1, xout ? 1 - ramp(local, s.dur - xout, s.dur) : 1);
      s.update(local);
    });
  };

  window.stageReady = (async () => {
    await document.fonts.ready;
    await Promise.all([...document.images].map((im) => (im.complete ? im.decode().catch(() => {}) : new Promise((r) => { im.onload = im.onerror = r; }).then(() => im.decode().catch(() => {})))));
    window.renderAt(0);
    return true;
  })();
})();
