import { describe, expect, it } from "vitest";
import type { Participant } from "./api";
import { moreLabel, orderParticipants, summarizeParticipants } from "./meetingPeople";

const P = (name: string, extra: Partial<Participant> = {}): Participant => ({ user_id: name, display_name: name, joined_at: "2026-10-01T10:00:00Z", left_at: null, online: false, ...extra });

describe("участники в истории", () => {
  it("первыми — организатор и руководители, дальше по порядку входа; гости и телефоны — после сотрудников", () => {
    const list = [P("Гость А (гость)", { participant_type: "guest", user_id: null }), P("Рядовой 1"), P("Руководитель", { role: "leader" }), P("Рядовой 2"), P("Организатор", { role: "organizer" })];
    expect(orderParticipants(list).map((p) => p.display_name)).toEqual(["Организатор", "Руководитель", "Рядовой 1", "Рядовой 2", "Гость А (гость)"]);
  });
  it("показываются первые N, остальные — счётчиком; исходный список не меняется", () => {
    const list = Array.from({ length: 30 }, (_, i) => P(`Сотрудник ${i}`));
    const s = summarizeParticipants(list, 4);
    expect(s.shown).toHaveLength(4);
    expect(s.more).toBe(26);
    expect(s.total).toBe(30);
    expect(list[0].display_name).toBe("Сотрудник 0");
  });
  it("если скрывался бы ровно один человек — показываем его (строка «+ 1» ничего не экономит)", () => {
    const s = summarizeParticipants(Array.from({ length: 5 }, (_, i) => P(`Сотрудник ${i}`)), 4);
    expect(s.shown).toHaveLength(5);
    expect(s.more).toBe(0);
  });
  it("пустой список и одиночный участник", () => {
    expect(summarizeParticipants([])).toEqual({ shown: [], more: 0, total: 0 });
    expect(summarizeParticipants([P("Один")]).shown).toHaveLength(1);
  });
  it("склонение: участник / участника / участников", () => {
    expect(moreLabel(1)).toBe("+ 1 участник");
    expect(moreLabel(2)).toBe("+ 2 участника");
    expect(moreLabel(5)).toBe("+ 5 участников");
    expect(moreLabel(11)).toBe("+ 11 участников");
    expect(moreLabel(21)).toBe("+ 21 участник");
    expect(moreLabel(27)).toBe("+ 27 участников");
    expect(moreLabel(112)).toBe("+ 112 участников");
  });
});
