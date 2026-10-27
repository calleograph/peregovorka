/** Математика «стеклянных кристаллов» на странице входа (components/LoginGlass.tsx, Three.js). Отдельно от компонента, чтобы проверяться тестами. */

export interface Shard { x: number; y: number; z: number; r: number; octa: boolean; spin: number; phase: number }

/** Детерминированный генератор (mulberry32): раскладка одинакова при каждом открытии страницы и в тестах. */
export function rng(seed: number): () => number {
  let a = seed >>> 0;
  return () => { a = (a + 0x6d2b79f5) >>> 0; let t = a; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
}

/** Кристаллы по краям окна: середина (форма входа) свободна. `halfW`/`halfH` — половина видимой области сцены в единицах сцены. */
export function shardLayout(halfW: number, halfH: number, count: number, seed = 7): Shard[] {
  const rand = rng(seed);
  const out: Shard[] = [];
  for (let i = 0; i < count; i++) {
    const side = i % 2 === 0 ? -1 : 1;
    const x = side * halfW * (0.46 + rand() * 0.62);                 // не ближе 46 % полуширины к центру
    const y = (rand() * 2 - 1) * halfH * 0.95;
    out.push({ x, y, z: -2 + rand() * 4, r: 0.55 + rand() * 0.95, octa: rand() > 0.6, spin: (rand() - 0.5) * 0.5, phase: rand() * Math.PI * 2 });
  }
  return out;
}

/** Плавное следование за целью (экспоненциальное сглаживание): не зависит от частоты кадров. */
export function follow(cur: number, target: number, dtSec: number, rate = 3): number {
  return cur + (target - cur) * (1 - Math.exp(-rate * Math.max(0, dtSec)));
}

/** Нужна ли сцена WebGL: нет — «уменьшить движение», экономия трафика, слабое устройство или узкий телефон (там остаётся плоская сетка). */
export function wantsWebGL(env: { reduced: boolean; cores?: number; saveData?: boolean; width: number }): boolean {
  if (env.reduced || env.saveData) return false;
  if ((env.cores ?? 4) <= 2) return false;
  return env.width >= 560;
}

/** Число кристаллов по ширине окна. */
export function shardCount(width: number): number {
  return width < 900 ? 6 : width < 1500 ? 9 : 12;
}
