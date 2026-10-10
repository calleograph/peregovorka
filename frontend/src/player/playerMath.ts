/** Чистая логика плавающего плеера записей: волна, субтитры, окно. Проверяется тестами (`playerMath.test.ts`). */

export interface Cue { id: number; start: number; end: number; speaker: string; text: string }

export function decodePeaks(b64: string): Uint8Array {
  if (!b64) return new Uint8Array(0);
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

/** Свести массив пиков (по одному байту на 100 мс) к `bars` столбикам по ширине экрана: у каждого — максимум своей группы, 0…1. Короткая запись растягивается. */
export function reducePeaks(peaks: Uint8Array, bars: number): number[] {
  const n = Math.max(1, Math.floor(bars));
  const out = new Array<number>(n).fill(0);
  if (!peaks.length) return out;
  for (let i = 0; i < n; i++) {
    const a = Math.floor((i * peaks.length) / n);
    const b = Math.max(a + 1, Math.floor(((i + 1) * peaks.length) / n));
    let m = 0;
    for (let j = a; j < b && j < peaks.length; j++) if (peaks[j] > m) m = peaks[j];
    out[i] = m / 255;
  }
  return out;
}

export const timeAtX = (x: number, width: number, duration: number) => (width <= 0 || duration <= 0 ? 0 : Math.min(duration, Math.max(0, (x / width) * duration)));
export const xAtTime = (t: number, width: number, duration: number) => (duration <= 0 ? 0 : Math.min(width, Math.max(0, (t / duration) * width)));

/** Номер последней реплики, начавшейся не позже `t` (-1 — ещё ни одна). Реплики отсортированы по началу. */
export function currentIndex(cues: Cue[], t: number): number {
  let lo = 0, hi = cues.length - 1, ans = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (cues[mid].start <= t) { ans = mid; lo = mid + 1; } else hi = mid - 1;
  }
  return ans;
}

/**
 * Реплики, видимые в момент `t`: уже начались и ещё не закончились (плюс короткая задержка `hold`, чтобы текст не мигал между близкими фразами). Старый текст в паузе не висит:
 * после `end + hold` он исчезает. При одновременной речи возвращается до `max` реплик (самые свежие), по порядку начала.
 */
export function activeCues(cues: Cue[], t: number, hold = 0.8, max = 2): Cue[] {
  const i = currentIndex(cues, t);
  const out: Cue[] = [];
  for (let k = i; k >= 0 && k > i - 40 && out.length < max; k--) {
    if (t < cues[k].end + hold) out.push(cues[k]);
  }
  return out.reverse();
}

/** Поиск по тексту и имени говорящего (без учёта регистра): номера найденных реплик. */
export function searchCues(cues: Cue[], q: string): number[] {
  const s = q.trim().toLowerCase();
  if (!s) return [];
  const out: number[] = [];
  cues.forEach((c, i) => { if (c.text.toLowerCase().includes(s) || c.speaker.toLowerCase().includes(s)) out.push(i); });
  return out;
}

export interface Rect { x: number; y: number; w: number; h: number; auto?: boolean }          // auto — окно ещё не двигали: прижато к правому нижнему углу средствами CSS

/** Окно не должно уйти за экран: целиком вне видимой области оставить нельзя — заголовок (за него тянут) остаётся доступным. */
export function clampRect(r: Rect, vw: number, vh: number, minVisible = 90, header = 40): Rect {
  const w = Math.min(Math.max(r.w, 0), Math.max(vw, 0));
  const h = Math.min(Math.max(r.h, 0), Math.max(vh, 0));
  const x = Math.min(Math.max(r.x, minVisible - w), Math.max(vw - minVisible, minVisible - w));
  const y = Math.min(Math.max(r.y, 0), Math.max(vh - header, 0));
  return { x, y, w, h };
}

export function defaultRect(vw: number, vh: number, mode: "full" | "mini"): Rect {
  const w = mode === "full" ? Math.min(560, vw - 24) : Math.min(360, vw - 24);
  const h = mode === "full" ? Math.min(330, vh - 24) : 92;
  return { ...clampRect({ x: vw - w - 16, y: vh - h - 16, w, h }, vw, vh), auto: true };
}

export const MIN_FULL = { w: 340, h: 220 } as const;

export interface Prefs { volume: number; muted: boolean; speed: number; cc: boolean; transcript: boolean; follow: boolean }
export const DEFAULT_PREFS: Prefs = { volume: 1, muted: false, speed: 1, cc: false, transcript: false, follow: true };

export function parsePrefs(raw: string | null): Prefs {
  try {
    const o = raw ? JSON.parse(raw) : {};
    return {
      volume: typeof o.volume === "number" ? Math.min(1, Math.max(0, o.volume)) : 1, muted: !!o.muted,
      speed: [0.75, 1, 1.25, 1.5, 2].includes(o.speed) ? o.speed : 1, cc: !!o.cc, transcript: !!o.transcript, follow: o.follow !== false,
    };
  } catch { return { ...DEFAULT_PREFS }; }
}
