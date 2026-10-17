import { describe, expect, it } from "vitest";
import type { ProtocolItem } from "./api";
import { docState, generationLine } from "./components/GenerationInfo";
import { elapsedNow, stageText } from "./components/JobProgress";

const base: ProtocolItem = {
  id: "1", meeting_id: "m", kind: "summary", status: "running", error: null, created_by: "u", created_at: "2026-10-17T10:00:00Z", updated_at: "2026-10-17T10:00:00Z",
  model: "qwen3-1.7b-q4_k_m", location: null, title: null, edited_at: null, edited_by: null,
};

describe("состояние документа во время и после формирования", () => {
  it("в очереди, формируется, остановлено по времени, отменено — разные понятные состояния", () => {
    expect(docState({ ...base, status: "pending" })).toEqual({ label: "в очереди", tone: "pending" });
    expect(docState(base)).toEqual({ label: "формируется", tone: "pending" });
    expect(docState({ ...base, status: "timeout" })).toEqual({ label: "превышено время", tone: "error" });
    expect(docState({ ...base, status: "cancelled" })).toEqual({ label: "отменено", tone: "warn" });
  });

  it("неполное резюме с охватом стенограммы не выглядит готовым", () => {
    const s = docState({ ...base, status: "ready", coverage: { done: 3, total: 8, percent: 38 } });
    expect(s.label).toBe("неполное, 38%");
    expect(s.tone).toBe("warn");
    expect(docState({ ...base, status: "ready", coverage: { done: 8, total: 8, percent: 100 } }).label).toBe("готово");
  });

  it("строка списка называет итог остановленного и отменённого задания", () => {
    const t = { requested_at: "2026-10-17T10:00:00Z", started_at: null, llm_started_at: null, llm_finished_at: null, finished_at: "2026-10-17T10:10:00Z", queue_s: 0, prepare_s: 1, llm_s: 599, total_s: 600 };
    expect(generationLine({ ...base, status: "timeout", timing: t })).toContain("остановлено по времени ");
    expect(generationLine({ ...base, status: "cancelled", timing: t })).toContain("отменено ");
    expect(generationLine({ ...base, status: "running" })).not.toContain("готово");
  });
});

describe("этап и время идущего задания", () => {
  it("ожидание → подготовка → анализ с числом фрагментов → подготовка документа", () => {
    expect(stageText({ ...base, status: "pending" })).toMatch(/^Ожидание: задание в очереди/);
    expect(stageText(base)).toBe("Подготовка материалов");
    expect(stageText({ ...base, live: { elapsed_s: 70, limit_s: 600, target_s: 300, stage: "analysis", done: 3, total: 8 } })).toBe("Анализ материалов: обработано 3 из 8 фрагментов стенограммы");
    expect(stageText({ ...base, live: { elapsed_s: 250, limit_s: 600, target_s: 300, stage: "assemble", done: 8, total: 8 } })).toBe("Подготовка документа");
  });

  it("прошедшее время: значение сервера плюс секунды после ответа, назад не идёт", () => {
    expect(elapsedNow(70, 1_000_000, 1_005_000)).toBe(75);
    expect(elapsedNow(70, 1_000_000, 999_000)).toBe(70);
  });
});
