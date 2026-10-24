// Одно событие — одно уведомление: при переподключении сокета, повторной доставке или нескольких окнах одной организации событие приходит повторно.
// Ключ — организация + метка события (например, `chat:<id>:<номер сообщения>`); помним последние ключи ограниченное время.

export class Dedupe {
  private seen = new Map<string, number>();
  constructor(private ttlMs = 10 * 60_000, private max = 2000) {}

  /** true — показывать (ключ ещё не встречался в пределах срока). */
  fresh(key: string, now = Date.now()): boolean {
    const t = this.seen.get(key);
    if (t !== undefined && now - t < this.ttlMs) return false;
    this.seen.delete(key);
    this.seen.set(key, now);
    if (this.seen.size > this.max) {
      for (const [k, v] of this.seen) {
        if (this.seen.size <= this.max * 0.8 && now - v < this.ttlMs) break;
        this.seen.delete(k);
      }
    }
    return true;
  }
}

export type NotifyPrefs = { enabled: boolean; sound: boolean; hideContent: boolean; dndUntil: number };

/** Показывать ли уведомление и в каком виде: «Не беспокоить» (до времени или бессрочно: -1) глушит всё; скрытие содержимого убирает текст. */
export function shapeNotification(n: { title: string; body?: string; kind: string }, p: NotifyPrefs, now = Date.now()): { title: string; body: string; silent: boolean } | null {
  if (!p.enabled) return null;
  if (p.dndUntil === -1 || p.dndUntil > now) return null;
  const hidden = p.hideContent && (n.kind === "chat" || n.kind === "mention");
  return { title: hidden ? (n.kind === "mention" ? "Вас упомянули" : "Новое сообщение") : n.title, body: hidden ? "Откройте Peregovorka, чтобы прочитать" : (n.body ?? ""), silent: !p.sound };
}
