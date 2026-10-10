import { describe, expect, it } from "vitest";
import { nextStep, progressPercent, stateTitle, volumeLabel } from "./storageMath";

describe("хранилище записей и перенос: чистая логика", () => {
  it("состояние тома: недоступное — красным, с пометкой «данные устарели»", () => {
    expect(volumeLabel({ state: "ok", used_percent: 40, stale: false })).toEqual({ text: "в порядке", tone: "ok" });
    expect(volumeLabel({ state: "ok", used_percent: 80, stale: false }).tone).toBe("warn");
    expect(volumeLabel({ state: "ok", used_percent: 95, stale: false }).tone).toBe("bad");
    expect(volumeLabel({ state: "unavailable", used_percent: 10, stale: true }).text).toContain("устарели");
    expect(volumeLabel({ state: "not_configured", used_percent: null, stale: false }).text).toBe("не настроено");
  });
  it("прогресс считает и пропущенные, и ошибочные файлы; пустое задание — 100 %", () => {
    expect(progressPercent({ total: 10, done: 4, skipped: 1, failed: 1 })).toBe(60);
    expect(progressPercent({ total: 0, done: 0, skipped: 0, failed: 0 })).toBe(100);
    expect(progressPercent({ total: 3, done: 3, skipped: 0, failed: 0 })).toBe(100);
  });
  it("подсказка по итогам: остановка — как продолжить; ошибки — файлы на прежнем месте", () => {
    expect(nextStep({ state: "failed", skipped: 0, failed: 0, error: "SMB недоступен" })).toContain("«Продолжить»");
    expect(nextStep({ state: "done", skipped: 0, failed: 2, error: null })).toContain("прежнем месте");
    expect(nextStep({ state: "done", skipped: 3, failed: 0, error: null })).toContain("Пропущено");
    expect(nextStep({ state: "running", skipped: 0, failed: 0, error: null })).toBe("");
    expect(stateTitle("failed")).toBe("остановлен");
  });
});
