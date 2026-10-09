import { useEffect, useRef } from "react";
import { drift, energyAt, gridSpec, pointerPush, qualityFor } from "../backdropMath";

const SETTLE_MS = 4000;      // за это время после последнего движения курсора фон «успокаивается»
const RADIUS = 150;

/**
 * Фон страницы входа: тонкая геометрическая сетка, узлы которой мягко расступаются перед курсором. Чистый Canvas без библиотек.
 * Бережёт ресурсы: 30 кадров/с (20 на слабых устройствах), останавливается совсем, когда всё успокоилось и пользователь ничего не делает,
 * не рисует во вкладке в фоне; при «уменьшении движения» в системе рисуется один статичный кадр. Используется ТОЛЬКО на странице входа.
 */
export default function LoginBackdrop() {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = ref.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;
    const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
    let w = 0, h = 0, dpr = 1, step = 56, minDt = 33;
    let cols = 0, rows = 0, ox = 0, oy = 0;
    let bx: Float32Array, by: Float32Array, x: Float32Array, y: Float32Array, phase: Float32Array;
    let raf = 0, last = 0, running = false;
    let lastActivity = performance.now();
    let target = { x: -9999, y: -9999 }, smooth = { x: -9999, y: -9999 };
    let presence = 0, hasPointer = false;
    let color = "#2457d6", line = "#9aa7bd";

    const readColors = () => {
      const cs = getComputedStyle(document.documentElement);
      color = cs.getPropertyValue("--accent").trim() || color;
      line = cs.getPropertyValue("--line-strong").trim() || line;
    };

    const resize = () => {
      w = window.innerWidth; h = window.innerHeight;
      const q = qualityFor(navigator.hardwareConcurrency, w);
      step = q.step; minDt = 1000 / q.fps; dpr = Math.min(window.devicePixelRatio || 1, 1.5);
      canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
      canvas.style.width = `${w}px`; canvas.style.height = `${h}px`;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const g = gridSpec(w, h, step);
      cols = g.cols; rows = g.rows; ox = g.ox; oy = g.oy;
      const n = cols * rows;
      bx = new Float32Array(n); by = new Float32Array(n); x = new Float32Array(n); y = new Float32Array(n); phase = new Float32Array(n);
      for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) {
        const i = r * cols + c;
        bx[i] = x[i] = ox + c * step; by[i] = y[i] = oy + r * step; phase[i] = (c * 12.9898 + r * 78.233) % 6.283;
      }
      readColors();
      frame(performance.now(), true);
    };

    const frame = (now: number, force = false) => {
      const since = now - lastActivity;
      const energy = reduce ? 0 : energyAt(since, SETTLE_MS);
      smooth.x += (target.x - smooth.x) * 0.18; smooth.y += (target.y - smooth.y) * 0.18;
      presence += ((hasPointer && since < SETTLE_MS * 0.6 ? 1 : 0) - presence) * 0.08;
      let moving = false;
      for (let i = 0; i < x.length; i++) {
        const [dx, dy] = drift(phase[i], now, 3.5, energy);
        const [px, py] = presence > 0.01 ? pointerPush(bx[i], by[i], smooth.x, smooth.y, RADIUS, 22 * presence) : [0, 0];
        const tx = bx[i] + dx + px, ty = by[i] + dy + py;
        const nx = x[i] + (tx - x[i]) * 0.14, ny = y[i] + (ty - y[i]) * 0.14;
        if (Math.abs(nx - x[i]) + Math.abs(ny - y[i]) > 0.01) moving = true;
        x[i] = nx; y[i] = ny;
      }
      ctx.clearRect(0, 0, w, h);
      ctx.lineWidth = 1;
      const glow = (i: number) => (presence > 0.01 ? Math.max(0, 1 - Math.hypot(x[i] - smooth.x, y[i] - smooth.y) / (RADIUS * 1.4)) * presence : 0);
      for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) {
        const i = r * cols + c, g = glow(i);
        if (c + 1 < cols) { const j = i + 1; ctx.globalAlpha = 0.24 + 0.3 * Math.max(g, glow(j)); ctx.strokeStyle = g > 0.05 ? color : line; ctx.beginPath(); ctx.moveTo(x[i], y[i]); ctx.lineTo(x[j], y[j]); ctx.stroke(); }
        if (r + 1 < rows) { const j = i + cols; ctx.globalAlpha = 0.24 + 0.3 * Math.max(g, glow(j)); ctx.strokeStyle = g > 0.05 ? color : line; ctx.beginPath(); ctx.moveTo(x[i], y[i]); ctx.lineTo(x[j], y[j]); ctx.stroke(); }
        ctx.globalAlpha = 0.34 + 0.5 * g; ctx.fillStyle = g > 0.05 ? color : line;
        ctx.beginPath(); ctx.arc(x[i], y[i], 1.3 + 0.9 * g, 0, 6.283); ctx.fill();
      }
      ctx.globalAlpha = 1;
      // всё успокоилось и курсор давно не двигался — полная остановка (нулевая нагрузка), пока пользователь снова не пошевелит мышью
      if (!force && !moving && energy === 0 && presence < 0.01) running = false;
    };

    const loop = (now: number) => {
      if (!running) return;
      raf = requestAnimationFrame(loop);
      if (document.hidden || now - last < minDt) return;
      last = now;
      frame(now);
    };
    const wake = () => { lastActivity = performance.now(); if (reduce || running) return; running = true; raf = requestAnimationFrame(loop); };

    const onMove = (e: PointerEvent) => { if (e.pointerType === "touch") return; target = { x: e.clientX, y: e.clientY }; if (!hasPointer) smooth = { ...target }; hasPointer = true; wake(); };
    const onLeave = () => { hasPointer = false; wake(); };
    let t = 0;
    const onResize = () => { window.clearTimeout(t); t = window.setTimeout(resize, 120); };
    const theme = window.matchMedia?.("(prefers-color-scheme: dark)");
    const onTheme = () => { readColors(); frame(performance.now(), true); };

    resize();
    if (!reduce) {
      window.addEventListener("pointermove", onMove, { passive: true });
      document.documentElement.addEventListener("pointerleave", onLeave);
      document.addEventListener("visibilitychange", wake);
      wake();                                   // первые секунды сетка едва заметно «оживает» и затихает
    }
    window.addEventListener("resize", onResize);
    theme?.addEventListener?.("change", onTheme);
    return () => {
      running = false; cancelAnimationFrame(raf); window.clearTimeout(t);
      window.removeEventListener("pointermove", onMove);
      document.documentElement.removeEventListener("pointerleave", onLeave);
      document.removeEventListener("visibilitychange", wake);
      window.removeEventListener("resize", onResize);
      theme?.removeEventListener?.("change", onTheme);
    };
  }, []);

  return <canvas ref={ref} className="login-backdrop" aria-hidden="true" />;
}
