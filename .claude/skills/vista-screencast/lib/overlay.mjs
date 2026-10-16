// The layer the player draws on top of VISTA (design D7): cursor, click ripple,
// captions and the camera. It is passed to page.addInitScript, so it runs in
// the browser and must not refer to anything outside itself.
//
// The layer is appended to <html>, outside <body>; the camera transforms
// <body>. So zooms never scale the cursor or captions, and rects read with
// getBoundingClientRect are already in screen space.
export function OVERLAY() {
  if (window.__screencast) return;
  const install = () => {
    if (window.__screencast || !document.body) return;
    const root = document.createElement("div");
    root.id = "sc-overlay";
    root.innerHTML = `
      <style>
        #sc-overlay { position: fixed; inset: 0; pointer-events: none; z-index: 2147483647; }
        #sc-cursor { position: fixed; left: 0; top: 0; width: 26px; height: 26px; will-change: transform;
          filter: drop-shadow(0 3px 6px rgba(0,0,0,.35)); }
        .sc-ripple { position: fixed; width: 14px; height: 14px; margin: -7px 0 0 -7px; border-radius: 50%;
          border: 3px solid #4fb3ff; animation: sc-rip .55s ease-out forwards; }
        @keyframes sc-rip { from { transform: scale(.4); opacity: .95; } to { transform: scale(3.6); opacity: 0; } }
        #sc-cap { position: fixed; left: 50%; display: flex; align-items: center; gap: 14px; max-width: 70vw;
          padding: 9px 22px 9px 10px; border-radius: 14px; background: rgba(6,22,38,.9); color: #eaf3ff;
          font: 600 19px -apple-system, "Segoe UI", "Helvetica Neue", sans-serif; letter-spacing: -.01em;
          box-shadow: 0 18px 50px rgba(6,22,38,.35); backdrop-filter: blur(8px);
          opacity: 0; transform: translate(-50%, var(--sc-hide, -14px));
          transition: opacity .45s, transform .45s cubic-bezier(.2,.8,.2,1), top .5s cubic-bezier(.65,0,.35,1); }
        #sc-cap.on { opacity: 1; transform: translate(-50%, 0); }
        #sc-cap b { flex: none; display: grid; place-items: center; width: 32px; height: 32px; border-radius: 9px;
          background: linear-gradient(135deg, #4fb3ff, #ff8a3d); color: #061626; font-size: 17px; }
        #sc-cap b:empty { display: none; }
        #sc-cap small { display: block; font-weight: 400; font-size: 14px; color: #a9c2dc; margin-top: 1px; }
      </style>
      <svg id="sc-cursor" viewBox="0 0 24 24"><path d="M4 2.5v17.2l4.7-4.4 3 6.6 2.9-1.3-3-6.5h6.3z"
        fill="#111" stroke="#fff" stroke-width="1.4" stroke-linejoin="round"/></svg>
      <div id="sc-cap"><b></b><span></span></div>`;
    document.documentElement.appendChild(root);
    const cursor = root.querySelector("#sc-cursor");
    const cap = root.querySelector("#sc-cap");
    const MARGIN = 10;
    const pos = { x: innerWidth * 0.6, y: innerHeight * 0.55 };
    const place = () => (cursor.style.transform = `translate(${pos.x - 4}px, ${pos.y - 2}px)`);
    place();
    const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
    const hits = (a, b) => a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height;
    let avoid = [];

    // Top slot unless it would cover something the viewer needs to see.
    const slot = () => {
      const h = cap.offsetHeight || 52, w = cap.offsetWidth || 400;
      const rect = (top) => ({ x: (innerWidth - w) / 2, y: top, width: w, height: h });
      const top = MARGIN, bottom = innerHeight - h - MARGIN;
      const clear = (t) => !avoid.some((r) => hits(rect(t), r));
      const y = clear(top) || !clear(bottom) ? top : bottom;
      cap.style.top = y + "px";
      cap.style.setProperty("--sc-hide", y === top ? "-14px" : "14px");
    };

    window.__screencast = {
      move(x, y, ms = 700) {
        const from = { ...pos };
        const bend = (Math.random() - 0.5) * 80; // a slight arc reads as a hand
        return new Promise((done) => {
          const t0 = performance.now();
          const step = (now) => {
            const t = Math.min(1, (now - t0) / ms), e = ease(t);
            pos.x = from.x + (x - from.x) * e + Math.sin(Math.PI * e) * bend * 0.3;
            pos.y = from.y + (y - from.y) * e - Math.sin(Math.PI * e) * bend * 0.2;
            place();
            t < 1 ? requestAnimationFrame(step) : done();
          };
          requestAnimationFrame(step);
        });
      },
      ripple() {
        const r = document.createElement("div");
        r.className = "sc-ripple";
        r.style.left = pos.x + "px";
        r.style.top = pos.y + "px";
        root.appendChild(r);
        setTimeout(() => r.remove(), 700);
      },
      showCursor(on) {
        cursor.style.display = on ? "" : "none";
      },
      caption(num, text, sub) {
        if (!text) return cap.classList.remove("on");
        cap.querySelector("b").textContent = num ?? "";
        const span = cap.querySelector("span");
        span.textContent = text;
        if (sub) {
          const small = document.createElement("small");
          small.textContent = sub;
          span.appendChild(small);
        }
        slot();
        cap.classList.add("on");
      },
      /** Rects (screen space) the caption must not cover; re-slots it at once. */
      avoid(rects) {
        avoid = rects.filter(Boolean);
        slot();
      },
      captionRect() {
        return cap.classList.contains("on") ? cap.getBoundingClientRect().toJSON() : null;
      },
      camera(rect, scale, ms = 1100) {
        const b = document.body;
        b.style.transformOrigin = "0 0";
        b.style.transition = `transform ${ms}ms cubic-bezier(.65,0,.35,1)`;
        if (!rect || !scale || scale === 1) {
          b.style.transform = "none";
          return;
        }
        const cx = rect.x + rect.width / 2, cy = rect.y + rect.height / 2;
        const clamp = (v, lo) => Math.max(lo, Math.min(0, v));
        const tx = clamp(innerWidth / 2 - cx * scale, innerWidth - innerWidth * scale);
        const ty = clamp(innerHeight / 2 - cy * scale, innerHeight - innerHeight * scale);
        b.style.transform = `translate(${tx}px, ${ty}px) scale(${scale})`;
      },
    };
  };
  install();
  document.addEventListener("DOMContentLoaded", install);
}
