import { useSyncExternalStore } from "react";

/** Какую запись играет плеер. Хранилище лежит на уровне приложения, а не страницы истории: плеер переживает переходы между разделами. */
export interface PlayerItem {
  id: string; meetingId: string; title: string; subtitle?: string; kind: "mix_audio" | "mix_video" | "participant";
  hasVideo: boolean; durationS: number; canDownload: boolean;
}
export interface PlayerState { item: PlayerItem | null; mode: "full" | "mini"; focusTick: number }

let state: PlayerState = { item: null, mode: "full", focusTick: 0 };
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());
const set = (s: Partial<PlayerState>) => { state = { ...state, ...s }; emit(); };

/** Открыть запись. Если играет другая — она останавливается (элемент пересоздаётся по `key`), одновременно звучит только одна. Та же запись — просто разворачивается. */
export function openPlayer(item: PlayerItem): void {
  if (state.item && state.item.id === item.id) { set({ mode: "full", focusTick: state.focusTick + 1 }); return; }
  set({ item, mode: "full", focusTick: state.focusTick + 1 });
}
export const closePlayer = () => set({ item: null });
export const setPlayerMode = (mode: "full" | "mini") => set({ mode });
export const getPlayerState = () => state;

export function usePlayer(): PlayerState {
  return useSyncExternalStore((cb) => { listeners.add(cb); return () => listeners.delete(cb); }, () => state, () => state);
}

/** Для тестов. */
export function resetPlayer(): void { state = { item: null, mode: "full", focusTick: 0 }; emit(); }
