/**
 * Презентационная комната: кто на сцене, кто — зритель, как показывать длинный список. Чистые функции без React и LiveKit — проверяются тестами
 * (presentation.test.ts), в том числе на синтетическом списке из 1000 участников без единого WebRTC-соединения.
 *
 * Единая модель: руководители выступают, зрители смотрят, руководитель может дать и забрать слово. Число зрителей на модель не влияет: плитки
 * создаются только тем, кто что-то публикует или имеет слово; остальные — строки компактного списка, который рисует только видимую часть.
 */

export interface AudiencePerson {
  identity: string;
  name: string;
  local: boolean;
  mic: boolean;
  cam: boolean;
  screen: boolean;
  floor?: boolean;
  leader?: boolean;
  hand?: boolean;
  handOrder?: number;
}

/** Плитка на сцене нужна руководителю, тому, кому дали слово, и любому, кто сейчас публикует микрофон, камеру или экран. Обычному зрителю — нет. */
export function onStage(p: Pick<AudiencePerson, "mic" | "cam" | "screen" | "floor" | "leader">): boolean {
  return !!(p.leader || p.floor || p.cam || p.screen || p.mic);
}

export interface Split<T> { stage: T[]; audience: T[] }

/** Делит участников: в обычной комнате все на сцене (как раньше), в презентационной — только выступающие. */
export function splitStage<T extends AudiencePerson>(people: T[], presentation: boolean): Split<T> {
  if (!presentation) return { stage: people, audience: [] };
  const stage: T[] = [], audience: T[] = [];
  for (const p of people) (onStage(p) ? stage : audience).push(p);
  return { stage, audience };
}

const collator = typeof Intl !== "undefined" ? new Intl.Collator("ru", { sensitivity: "base", numeric: true }) : null;
const cmp = (a: string, b: string) => (collator ? collator.compare(a, b) : a < b ? -1 : a > b ? 1 : 0);

/** Порядок в списке зрителей: поднятые руки (по очереди), затем остальные по имени. Своя строка — первой. */
export function sortAudience<T extends AudiencePerson>(list: T[]): T[] {
  return [...list].sort((a, b) =>
    Number(!!b.local) - Number(!!a.local)
    || Number(!!b.hand) - Number(!!a.hand)
    || (a.hand && b.hand ? (a.handOrder ?? 0) - (b.handOrder ?? 0) : 0)
    || cmp(a.name, b.name));
}

/** Поиск по подстроке имени без учёта регистра и «ё/е»; пустой запрос — весь список. */
export function filterAudience<T extends { name: string }>(list: T[], query: string): T[] {
  const q = norm(query);
  return q ? list.filter((p) => norm(p.name).includes(q)) : list;
}
const norm = (s: string) => s.toLocaleLowerCase("ru").replace(/ё/g, "е").trim();

export interface Windowed { start: number; end: number; padTop: number; padBottom: number }

/** Какие строки реально рисовать: окно прокрутки плюс запас; остальное заменяют два пустых блока высотой в сумму скрытых строк. */
export function windowRange(total: number, rowH: number, scrollTop: number, viewH: number, overscan = 6): Windowed {
  if (total <= 0 || rowH <= 0) return { start: 0, end: 0, padTop: 0, padBottom: 0 };
  const top = Math.min(Math.max(0, scrollTop), Math.max(0, total * rowH - viewH));       // прокрутка не дальше конца списка
  const first = Math.max(0, Math.floor(top / rowH) - overscan);
  const last = Math.min(total, Math.ceil((top + viewH) / rowH) + overscan);
  const end = Math.max(first, last);
  return { start: first, end, padTop: first * rowH, padBottom: (total - end) * rowH };
}

/** «1 000 зрителей», «1 зритель», «23 зрителя». */
export function audienceLabel(n: number): string {
  const m10 = n % 10, m100 = n % 100;
  const word = m10 === 1 && m100 !== 11 ? "зритель" : m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14) ? "зрителя" : "зрителей";
  return `${n.toLocaleString("ru-RU")} ${word}`;
}

/** Сводка для заголовка: число зрителей (без выступающих) и сколько из них подняли руку. */
export function audienceCounts<T extends AudiencePerson>(audience: T[]): { viewers: number; hands: number } {
  let hands = 0;
  for (const p of audience) if (p.hand) hands++;
  return { viewers: audience.length, hands };
}

/** Не обновлять состояние, если участники не изменились: возвращает прежний массив (тот же объект), когда все записи совпадают. */
export function sameList<T extends object>(a: readonly T[], b: readonly T[], keys: readonly (keyof T)[]): boolean {
  if (a === b) return true;
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    const x = a[i], y = b[i];
    if (x === y) continue;
    for (const k of keys) if (x[k] !== y[k]) return false;
  }
  return true;
}
