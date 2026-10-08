import { describe, expect, it } from "vitest";
import type { LlmOptions } from "./api";
import { choiceLabel, effectiveText, isPhoneIdentity, normalizeNumber, numberAllowed, tileName } from "./phone";

describe("телефонные участники", () => {
  it("узнаёт identity звонков и подписывает плитку", () => {
    expect(isPhoneIdentity("p-0123")).toBe(true);
    expect(isPhoneIdentity("sip_+70001112233_ab")).toBe(true);
    expect(isPhoneIdentity("u-0123")).toBe(false);
    expect(tileName("p-0123", "Телефон: +70001112233")).toBe("Телефон: +70001112233");
    expect(tileName("sip_+70001112233_ab", "", { "sip.phoneNumber": "+70001112233" })).toBe("Телефон: +70001112233");
    expect(tileName("sip_+70001112233_ab", "+70001112233")).toBe("Телефон: +70001112233");
    expect(tileName("u-1", "Анна")).toBe("Анна");
  });
  it("нормализует номер как сервер и проверяет префиксы", () => {
    expect(normalizeNumber("+7 (000) 111-22-33")).toBe("+70001112233");
    expect(normalizeNumber("*98")).toBe("*98");
    expect(normalizeNumber("abc")).toBeNull();
    expect(normalizeNumber("")).toBeNull();
    expect(numberAllowed([], "123")).toBe(true);
    expect(numberAllowed(["+7", "8*"], "8123")).toBe(true);
    expect(numberAllowed(["+7"], "9000")).toBe(false);
  });
});

const opts: LlmOptions = { system: { name: "Основной", provider: "external", model: "gpt-x" }, local: [{ id: "q", title: "Qwen3 0.6B", light: true, installed: true }],
  profiles: [{ id: "p1", name: "Сильная", model: "big", is_default: false }], on_missing: "system" };

describe("выбор языковой модели", () => {
  it("подписи вариантов", () => {
    expect(choiceLabel({ mode: "inherit", profile_id: null, local_model: null }, opts)).toBe("Как системная (gpt-x)");
    expect(choiceLabel({ mode: "local", profile_id: null, local_model: "q" }, opts)).toBe("Qwen3 0.6B");
    expect(choiceLabel({ mode: "profile", profile_id: "p1", local_model: null }, opts)).toBe("Сильная");
    expect(choiceLabel({ mode: "off", profile_id: null, local_model: null }, opts)).toBe("Отключена");
  });
  it("объясняет, какая модель будет использована и почему подменена", () => {
    expect(effectiveText({ name: "Qwen3", source: "room", available: true, reason: null, note: null })?.text).toContain("для этой комнаты");
    expect(effectiveText({ name: "Основной", source: "system", available: true, reason: null, note: "Выбранный профиль удалён. Использована системная модель." })?.tone).toBe("warn");
    expect(effectiveText({ name: "x", source: "room", available: false, reason: "отключена", note: null })?.tone).toBe("error");
    expect(effectiveText(null)).toBeNull();
  });
});
