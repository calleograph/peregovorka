/** Описание сигналов уведомлений комнаты. Сигналы должны быть ЯВНО слышны поверх голосов в комнате, но не пугать:
 *  «рука» — три восходящих тона (≈0,75 с), «сообщение» — два нисходящих «дин-дон» (≈0,45 с). Это разные мелодии, их не спутать.
 *  `peak` — амплитуда при громкости 1; итоговая = peak × громкость (по умолчанию 0,8). Прежние пики (0,10 и 0,12) в шуме комнаты не слышались. */
export interface Note { freq: number; at: number; dur: number; peak: number; type: OscillatorType }

export const CHAT_NOTES: Note[] = [
  { freq: 988, at: 0, dur: 0.26, peak: 0.55, type: "sine" },
  { freq: 740, at: 0.17, dur: 0.34, peak: 0.55, type: "sine" },
  { freq: 1976, at: 0, dur: 0.18, peak: 0.12, type: "triangle" },      // обертон: ярче и заметнее на динамиках ноутбуков
];

export const HAND_NOTES: Note[] = [
  { freq: 659, at: 0, dur: 0.22, peak: 0.65, type: "triangle" },
  { freq: 880, at: 0.17, dur: 0.22, peak: 0.65, type: "triangle" },
  { freq: 1175, at: 0.34, dur: 0.42, peak: 0.7, type: "triangle" },
];

export const DEFAULT_VOLUME = 0.8;

export const totalDuration = (notes: Note[]): number => Math.max(...notes.map((n) => n.at + n.dur));
export const peakAt = (n: Note, volume: number): number => Math.max(0.0002, Math.min(0.95, n.peak * volume));
