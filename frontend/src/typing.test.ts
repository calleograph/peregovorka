import { describe, expect, it } from "vitest";
import { TYPING_EXPIRE_MS, TYPING_HEARTBEAT_MS, TYPING_STOP_MS, TypingSender, TypingTracker, typingText } from "./typing";

describe("«печатает…»", () => {
  it("отправитель шлёт «начал», редкий пульс и «закончил» — но не событие на каждую клавишу", () => {
    let now = 0;
    const timers: { at: number; fn: () => void }[] = [];
    const sent: boolean[] = [];
    const s = new TypingSender((t) => sent.push(t), () => now, (fn, ms) => { const t = { at: now + ms, fn }; timers.push(t); return t as never; }, (t) => { const i = timers.indexOf(t as never); if (i >= 0) timers.splice(i, 1); });
    for (let i = 0; i < 20; i++) { now += 100; s.input(); }                       // 2 с непрерывного ввода: одно событие
    expect(sent).toEqual([true]);
    now += TYPING_HEARTBEAT_MS; s.input();                                         // прошло 3+ с — пульс
    expect(sent).toEqual([true, true]);
    now += TYPING_STOP_MS; for (const t of timers.splice(0)) if (t.at <= now) t.fn();      // ввод прекратился — автоматический «закончил»
    expect(sent).toEqual([true, true, false]);
    s.stop(); expect(sent).toHaveLength(3);                                         // повторный stop ничего не шлёт
    s.input(); s.stop();                                                           // отправка сообщения: немедленный «закончил»
    expect(sent.slice(3)).toEqual([true, false]);
  });

  it("получатель гасит индикатор сам, не показывает себя и сортирует по давности", () => {
    const t = new TypingTracker();
    t.event("a", "Иван Петров", true, 0);
    t.event("b", "Анна", true, 1000);
    t.event("me", "Я", true, 1000);
    expect(t.names(1500, "Я")).toEqual(["Иван Петров", "Анна"]);
    expect(t.names(TYPING_EXPIRE_MS + 500, "Я")).toEqual(["Анна"]);                  // у Ивана пульс не пришёл — исчез
    t.event("b", "Анна", false, 2000);
    expect(t.names(2100, "Я")).toEqual([]);
  });

  it("текст индикатора", () => {
    expect(typingText([])).toBe("");
    expect(typingText(["Иван Петров"])).toBe("Иван Петров печатает…");
    expect(typingText(["Иван", "Анна"])).toBe("Иван и Анна печатают…");
    expect(typingText(["Иван Петров", "А", "Б", "В"])).toBe("Иван Петров и ещё 3 печатают…");
  });
});
