import { describe, expect, it } from "vitest";
import { CHAT_NOTES, DEFAULT_VOLUME, HAND_NOTES, peakAt, totalDuration } from "./soundSpec";

describe("сигналы уведомлений", () => {
  it("явно слышны: громкость по умолчанию в разы выше прежней (пики были 0,06 и 0,07)", () => {
    for (const n of [...CHAT_NOTES, ...HAND_NOTES].filter((x) => x.peak >= 0.5)) expect(peakAt(n, DEFAULT_VOLUME)).toBeGreaterThan(0.3);
  });
  it("не искажают: итоговая амплитуда ограничена, а при нулевой громкости — практически тишина", () => {
    for (const n of [...CHAT_NOTES, ...HAND_NOTES]) { expect(peakAt(n, 1)).toBeLessThanOrEqual(0.95); expect(peakAt(n, 0)).toBeLessThan(0.001); }
  });
  it("«рука» и «сообщение» различимы: разное число тонов, направление и длительность", () => {
    const chatMain = CHAT_NOTES.filter((n) => n.type === "sine"), hand = HAND_NOTES;
    expect(hand.length).toBe(3); expect(chatMain.length).toBe(2);
    expect(chatMain[1].freq).toBeLessThan(chatMain[0].freq);          // сообщение — вниз
    expect(hand[2].freq).toBeGreaterThan(hand[0].freq);               // рука — вверх
    expect(totalDuration(hand)).toBeGreaterThan(totalDuration(CHAT_NOTES));
    expect(totalDuration(hand)).toBeLessThan(1.2);                    // но не затянуты
  });
});
