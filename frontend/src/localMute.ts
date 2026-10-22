/** «Заглушить для себя»: локальное выключение звука конкретного участника ТОЛЬКО на этом устройстве. Не серверный mute и не действие руководителя:
 *  у остальных участник звучит как обычно. Состояние хранится в sessionStorage на время встречи (переживает переподключение и перезагрузку страницы). */
const KEY = (meetingId: string) => `pg:lmute:${meetingId}`;

type Store = Pick<Storage, "getItem" | "setItem">;
const store = (): Store | null => { try { return window.sessionStorage; } catch { return null; } };

export function loadLocalMuted(meetingId: string, st: Pick<Storage, "getItem"> | null = store()): Set<string> {
  try {
    const arr = JSON.parse(st?.getItem(KEY(meetingId)) ?? "[]");
    return new Set(Array.isArray(arr) ? arr.filter((x): x is string => typeof x === "string").slice(0, 500) : []);
  } catch { return new Set(); }
}

export function saveLocalMuted(meetingId: string, set: Set<string>, st: Pick<Storage, "setItem"> | null = store()): void {
  try { st?.setItem(KEY(meetingId), JSON.stringify([...set])); } catch { /* хранилище недоступно — на время страницы состояние всё равно держится в памяти */ }
}

/** Новое множество с переключённым участником (исходное не меняется). */
export function toggleLocalMute(set: Set<string>, identity: string): Set<string> {
  const n = new Set(set);
  if (n.has(identity)) n.delete(identity); else n.add(identity);
  return n;
}

/** Одно и то же лицо на разных устройствах: identity u-<id> и u-<id>~<метка устройства>. */
export const sameUser = (a: string, b: string): boolean => !!a && !!b && a.startsWith("u-") && a.split("~")[0] === b.split("~")[0];

/** Применить к звуковым элементам комнаты: у каждого `data-identity` — кто говорит. Свои же другие устройства (`self`) не проигрываются никогда:
 *  телефон рядом с компьютером иначе воспроизводит ваш голос, микрофон его снова ловит — эхо у всех и двойная стенограмма. */
export function applyLocalMute(root: ParentNode | null, muted: Set<string>, self = ""): void {
  root?.querySelectorAll<HTMLMediaElement>("audio[data-identity], video[data-identity]").forEach((el) => { const id = el.dataset.identity ?? ""; el.muted = muted.has(id) || sameUser(id, self); });
}
