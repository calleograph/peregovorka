/** Решения о фоне страницы входа (components/LoginNetwork.tsx). Отдельно от компонента, чтобы проверяться тестами. */

export interface NetNode { id: string; x: number; y: number; w: number; h: number; delay: number; dur: number; cam: boolean }

/** Участники по краям окна (холст 1200×800): середина — форма входа — свободна. Раскладка постоянная: одинакова при каждом открытии и в тестах. */
export const NET_NODES: NetNode[] = [
  { id: "a", x: 70, y: 90, w: 150, h: 100, delay: 0, dur: 9, cam: true },
  { id: "b", x: 250, y: 300, w: 120, h: 82, delay: 1.6, dur: 11, cam: false },
  { id: "c", x: 60, y: 520, w: 140, h: 94, delay: 3.1, dur: 10, cam: true },
  { id: "d", x: 230, y: 650, w: 110, h: 76, delay: 0.8, dur: 12, cam: false },
  { id: "e", x: 980, y: 80, w: 140, h: 94, delay: 2.2, dur: 10, cam: false },
  { id: "f", x: 850, y: 320, w: 130, h: 88, delay: 0.4, dur: 11, cam: true },
  { id: "g", x: 990, y: 520, w: 150, h: 100, delay: 3.6, dur: 9, cam: false },
  { id: "h", x: 820, y: 660, w: 120, h: 80, delay: 1.2, dur: 12, cam: true },
];

/** Связи «кто с кем на связи»: пары идентификаторов узлов. */
export const NET_LINKS: [string, string][] = [["a", "b"], ["b", "c"], ["c", "d"], ["b", "d"], ["e", "f"], ["f", "g"], ["g", "h"], ["f", "h"], ["a", "e"], ["c", "g"], ["d", "h"]];

/** Кривая связи между центрами двух узлов (лёгкий изгиб; середина холста обходится стороной). */
export function linkPath(a: NetNode, b: NetNode): string {
  const ax = a.x + a.w / 2, ay = a.y + a.h / 2, bx = b.x + b.w / 2, by = b.y + b.h / 2;
  const mx = (ax + bx) / 2, my = (ay + by) / 2;
  const nx = -(by - ay), ny = bx - ax, len = Math.hypot(nx, ny) || 1;
  const bend = Math.min(60, len * 0.18) * (mx < 600 ? -1 : 1);       // изгиб наружу, от центра
  return `M${ax} ${ay} Q${Math.round(mx + (nx / len) * bend)} ${Math.round(my + (ny / len) * bend)} ${bx} ${by}`;
}

/** Нужна ли движущаяся картинка: нет — «уменьшить движение», экономия трафика, слабое устройство (остаётся спокойная статичная композиция). */
export function wantsMotion(env: { reduced: boolean; cores?: number; saveData?: boolean }): boolean {
  if (env.reduced || env.saveData) return false;
  return (env.cores ?? 4) > 2;
}

/** Показывать ли композицию совсем: на узком телефоне её заменяет спокойный фон (форма занимает всю ширину). */
export function wantsNetwork(width: number): boolean { return width >= 560; }
