// Результат проверки оборудования перед входом (гость): выбранные устройства передаются комнате. Только в памяти вкладки.

export interface PreJoin { micId?: string; speakerId?: string; camId?: string; camOn: boolean }

let current: PreJoin | null = null;
export const setPreJoin = (p: PreJoin | null): void => { current = p; };
/** Выбор устройств забирается один раз при входе. */
export const takePreJoin = (): PreJoin | null => { const p = current; current = null; return p; };

/** Короткий тон (синус) в формате WAV (16 бит, моно) — проверка динамиков без внешних файлов. */
export function toneWav(freq = 440, seconds = 0.9, rate = 22050, volume = 0.35): Uint8Array {
  const n = Math.floor(rate * seconds);
  const buf = new ArrayBuffer(44 + n * 2);
  const v = new DataView(buf);
  const str = (o: number, s: string) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
  str(0, "RIFF"); v.setUint32(4, 36 + n * 2, true); str(8, "WAVE"); str(12, "fmt "); v.setUint32(16, 16, true); v.setUint16(20, 1, true);
  v.setUint16(22, 1, true); v.setUint32(24, rate, true); v.setUint32(28, rate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  str(36, "data"); v.setUint32(40, n * 2, true);
  const fade = Math.floor(rate * 0.04);
  for (let i = 0; i < n; i++) {
    const env = Math.min(1, i / fade, (n - i) / fade);              // плавное начало и конец — без щелчков
    v.setInt16(44 + i * 2, Math.round(Math.sin((2 * Math.PI * freq * i) / rate) * volume * env * 32767), true);
  }
  return new Uint8Array(buf);
}

/** Уровень сигнала 0…1 по данным анализатора (RMS по времени; 128 — тишина у 8-битного представления). */
export function levelFromTimeDomain(data: Uint8Array): number {
  if (data.length === 0) return 0;
  let sum = 0;
  for (let i = 0; i < data.length; i++) { const x = (data[i] - 128) / 128; sum += x * x; }
  return Math.min(1, Math.sqrt(sum / data.length) * 3.2);
}
