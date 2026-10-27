import { useEffect, useRef, useState } from "react";
import { AmbientLight, Color, DirectionalLight, EdgesGeometry, Group, IcosahedronGeometry, LineBasicMaterial, LineSegments, Mesh, MeshStandardMaterial, OctahedronGeometry,
  PerspectiveCamera, Scene, WebGLRenderer, type BufferGeometry } from "three";
import { follow, shardCount, shardLayout, wantsWebGL } from "../glassMath";

const FPS = 30;
const Z = 14;                                   // расстояние камеры; видимая область считается из него и из угла обзора
const FOV = 40;

/** Цвет из CSS-переменной темы; незнакомый формат (color-mix, oklch) не ломает сцену — берётся запасной. */
function themeColor(name: string, fallback: string): Color {
  const c = new Color(fallback);
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  try { if (v) c.setStyle(v); } catch { /* запасной цвет */ }
  return c;
}

/**
 * Стеклянные кристаллы на странице входа (Three.js): несколько полупрозрачных граней по краям окна медленно вращаются и мягко следуют за курсором.
 * Подгружается отдельным чанком только здесь. Не показывается (остаётся плоская сетка LoginBackdrop), если: в системе «уменьшить движение» или экономия трафика,
 * слабое устройство, узкий экран, нет WebGL или он отказал. Не рисует во вкладке в фоне, 30 кадров/с, разрешение не выше 1,5×, при выходе освобождает видеопамять.
 */
export default function LoginGlass() {
  const host = useRef<HTMLDivElement>(null);
  const [on, setOn] = useState(() => wantsWebGL({
    reduced: window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false,
    cores: navigator.hardwareConcurrency,
    saveData: (navigator as Navigator & { connection?: { saveData?: boolean } }).connection?.saveData ?? false,
    width: window.innerWidth,
  }));

  useEffect(() => {
    const parent = host.current;
    if (!on || !parent) return;
    // canvas создаётся здесь, а не в разметке: после forceContextLoss тот же элемент больше не годится (повторный запуск эффекта в режиме разработки, смена темы и т. п.)
    const canvas = document.createElement("canvas");
    canvas.className = "login-glass"; canvas.setAttribute("aria-hidden", "true");
    if (!document.createElement("canvas").getContext("webgl2")) { setOn(false); return; }       // WebGL нет или отключён: без шума в консоли остаётся плоская сетка
    let renderer: WebGLRenderer;
    try {
      renderer = new WebGLRenderer({ canvas, alpha: true, antialias: window.devicePixelRatio < 2, powerPreference: "low-power" });
    } catch { setOn(false); return; }                                    // видеокарта отказала: остаётся плоская сетка
    parent.appendChild(canvas);
    const scene = new Scene();
    const camera = new PerspectiveCamera(FOV, 1, 0.1, 60);
    camera.position.z = Z;
    scene.add(new AmbientLight(0xffffff, 1.1));
    const key = new DirectionalLight(0xffffff, 2.2); key.position.set(-4, 6, 8); scene.add(key);
    const rim = new DirectionalLight(0xffffff, 1.2); rim.position.set(6, -3, 4); scene.add(rim);
    const group = new Group();
    scene.add(group);
    const geos: BufferGeometry[] = [];
    const edgeGeos: BufferGeometry[] = [];
    const faceMat = new MeshStandardMaterial({ transparent: true, opacity: 0.26, roughness: 0.18, metalness: 0.1, flatShading: true, depthWrite: false });
    const edgeMat = new LineBasicMaterial({ transparent: true, opacity: 0.42, depthWrite: false });
    const meshes: { m: Mesh; e: LineSegments; s: ReturnType<typeof shardLayout>[number] }[] = [];
    let raf = 0, last = 0, t0 = performance.now(), px = 0, py = 0, tx = 0, ty = 0;
    const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;

    const colors = () => {
      const a = themeColor("--accent", "#2457d6");
      faceMat.color.copy(a).lerp(new Color("#ffffff"), 0.25);
      edgeMat.color.copy(a);
    };
    const build = () => {
      for (const { m, e } of meshes) group.remove(m, e);
      meshes.length = 0;
      geos.forEach((g) => g.dispose()); edgeGeos.forEach((g) => g.dispose()); geos.length = 0; edgeGeos.length = 0;
      const w = window.innerWidth, h = window.innerHeight;
      const halfH = Math.tan((FOV / 2) * Math.PI / 180) * Z, halfW = halfH * (w / h);
      for (const s of shardLayout(halfW, halfH, shardCount(w))) {
        const g = s.octa ? new OctahedronGeometry(s.r * 1.15) : new IcosahedronGeometry(s.r);
        const eg = new EdgesGeometry(g);
        geos.push(g); edgeGeos.push(eg);
        const m = new Mesh(g, faceMat), e = new LineSegments(eg, edgeMat);
        m.position.set(s.x, s.y, s.z); e.position.copy(m.position);
        group.add(m, e);
        meshes.push({ m, e, s });
      }
    };
    const resize = () => {
      const w = window.innerWidth, h = window.innerHeight;
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
      renderer.setSize(w, h, false);
      canvas.style.width = `${w}px`; canvas.style.height = `${h}px`;
      camera.aspect = w / h; camera.updateProjectionMatrix();
      build();
      draw(performance.now());
    };
    const draw = (now: number) => {
      const t = (now - t0) / 1000;
      for (const { m, e, s } of meshes) {
        m.rotation.set(t * s.spin + s.phase, t * s.spin * 1.3, 0);
        m.position.y = s.y + Math.sin(t * 0.35 + s.phase) * 0.22;
        e.rotation.copy(m.rotation); e.position.copy(m.position);
      }
      group.rotation.y = px * 0.12; group.rotation.x = -py * 0.08;
      renderer.render(scene, camera);
    };
    const loop = (now: number) => {
      raf = requestAnimationFrame(loop);
      if (document.hidden || now - last < 1000 / FPS) return;
      const dt = Math.min(0.1, (now - last) / 1000); last = now;
      px = follow(px, tx, dt); py = follow(py, ty, dt);
      try { draw(now); } catch { cancelAnimationFrame(raf); setOn(false); }       // любой сбой отрисовки — тихо возвращаемся к плоскому фону, страница входа работает
    };
    const onMove = (e: PointerEvent) => { tx = (e.clientX / window.innerWidth) * 2 - 1; ty = (e.clientY / window.innerHeight) * 2 - 1; };
    const onTheme = () => { colors(); draw(performance.now()); };
    const mo = new MutationObserver(onTheme);
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme", "style"] });
    const onLost = (e: Event) => { e.preventDefault(); cancelAnimationFrame(raf); setOn(false); };     // видеокарта сбросила контекст: возвращаемся к плоскому фону
    canvas.addEventListener("webglcontextlost", onLost);
    colors(); resize();
    window.addEventListener("resize", resize);
    if (!reduce) { window.addEventListener("pointermove", onMove, { passive: true }); raf = requestAnimationFrame(loop); }
    return () => {
      cancelAnimationFrame(raf); mo.disconnect();
      window.removeEventListener("resize", resize); window.removeEventListener("pointermove", onMove); canvas.removeEventListener("webglcontextlost", onLost);
      geos.forEach((g) => g.dispose()); edgeGeos.forEach((g) => g.dispose()); faceMat.dispose(); edgeMat.dispose();
      renderer.dispose(); renderer.forceContextLoss(); canvas.remove();
    };
  }, [on]);

  return on ? <div ref={host} aria-hidden="true" /> : null;
}
