export interface HelpItem { q: string; alt: string; page: string; sec: string; roles: string[]; also?: boolean }

const STOP = new Set("как где что почему зачем мне меня мой моя мои не ни на в во и а по с со для это у к от до ли же я ты мы вы мне можно нужно надо или".split(" "));

/** Слова запроса без служебных; основа — первые 5 букв: «подключить», «подключение» и «подключения» совпадают без словаря. */
export function stems(text: string): string[] {
  return text.toLowerCase().replace(/ё/g, "е").split(/[^\p{L}\p{N}]+/u).filter((w) => w && !STOP.has(w)).map((w) => w.slice(0, 5));
}

/** Лучшие статьи по запросу обычными словами. Вопрос весит больше других формулировок; статья показывается один раз. */
export function searchHelp(items: HelpItem[], query: string, limit = 12): HelpItem[] {
  const words = stems(query);
  if (!words.length) return [];
  const best = new Map<string, { item: HelpItem; score: number }>();
  for (const item of items) {
    const q = new Set(stems(item.q)), alt = new Set(stems(item.alt));
    let hit = 0, score = 0;
    for (const w of words) {
      const inQ = q.has(w), inAlt = alt.has(w);
      if (inQ || inAlt) hit += 1;
      score += inQ ? 3 : inAlt ? 2 : 0;
    }
    if (!hit) continue;
    score = score * (hit / words.length) + (item.also ? 0 : item.sec === "Статьи" ? 0 : 1);
    const was = best.get(item.page);
    if (!was || score > was.score) best.set(item.page, { item, score });
  }
  return [...best.values()].sort((a, b) => b.score - a.score).slice(0, limit).map((x) => x.item);
}
