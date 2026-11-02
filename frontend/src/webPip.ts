/** Плавающее окно встречи в браузере (картинка в картинке): кого показывать — те же правила, что в приложении Android (PipModel.focus). */
export interface PipCand { identity: string; local: boolean; screen: boolean; cam: boolean; speaking: boolean }
export interface PipFocus { identity: string; source: "screen" | "camera" | "none" }

/**
 * Чужая демонстрация экрана; иначе говорящий собеседник (с камерой — в первую очередь); иначе последний говоривший, пока он во встрече (окно не перескакивает
 * в паузах речи); иначе собеседник с камерой, любой собеседник, сам участник. [lastSpeaker] — только собеседник: сам участник «залипнуть» не может.
 */
export function pipFocus(list: PipCand[], lastSpeaker: string | null): PipFocus | null {
  const others = list.filter((p) => !p.local);
  const p = others.find((x) => x.screen) ?? others.find((x) => x.speaking && x.cam) ?? others.find((x) => x.speaking)
    ?? others.find((x) => x.identity === lastSpeaker) ?? others.find((x) => x.cam) ?? others[0] ?? list[0];
  if (!p) return null;
  return { identity: p.identity, source: !p.local && p.screen ? "screen" : p.cam ? "camera" : "none" };
}

/** Видеоэлемент страницы, который сейчас показывает эту дорожку (у LiveKit одна дорожка может быть прикреплена к нескольким элементам — берём видимый). */
export function videoShowing(doc: Document, trackId: string | undefined): HTMLVideoElement | null {
  if (!trackId) return null;
  const all = [...doc.querySelectorAll("video")].filter((v) => (v.srcObject as MediaStream | null)?.getVideoTracks?.().some((t) => t.id === trackId));
  return all.find((v) => v.offsetWidth > 0) ?? all[0] ?? null;
}

/** Последний говоривший собеседник: кто говорит сейчас, иначе прежний. */
export const lastSpeakerOf = (list: PipCand[], prev: string | null): string | null => list.find((p) => !p.local && p.speaking)?.identity ?? prev;
