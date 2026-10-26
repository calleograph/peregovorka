import { describe, expect, it } from "vitest";
import { countsText, defaultSelection, filesText, isActive, jobStatus, selectable, summary, TRUST, type RestoreItem } from "./restoreView";

const item = (over: Partial<RestoreItem> = {}): RestoreItem => ({ id: "a", dir: "Комната/2026-10-24, Суббота/10-30", mode: "manifest", trust: "valid", trust_note: "", status: "new", note: "", meeting_id: null, room: null,
  started_at: null, files: [], problems: [], people: { found: 0, missing: 0 }, how: "", ...over });

describe("выбор для восстановления", () => {
  it("по умолчанию отмечены только достоверные новые и неполные", () => {
    const items = [item({ id: "1" }), item({ id: "2", status: "partial" }), item({ id: "3", status: "present" }), item({ id: "4", status: "problem" }), item({ id: "5", trust: "none" }), item({ id: "6", trust: "foreign" })];
    expect([...defaultSelection(items)].sort()).toEqual(["1", "2"]);
  });
  it("предположительные можно отметить вручную, «уже в приложении» и «проблемы» — нет", () => {
    expect(selectable(item({ trust: "none" }))).toBe(true);
    expect(selectable(item({ status: "present" }))).toBe(false);
    expect(selectable(item({ status: "problem" }))).toBe(false);
  });
  it("сводка отделяет достоверное от предположительного", () => {
    const items = [item({ id: "1" }), item({ id: "2", trust: "none" }), item({ id: "3", trust: "invalid" })];
    expect(summary(items, new Set(["1", "2", "3"]))).toEqual({ certain: 1, supposed: 2 });
    expect(summary(items, new Set())).toEqual({ certain: 0, supposed: 0 });
  });
});

describe("подписи", () => {
  it("только верная подпись считается достоверной", () => {
    expect(Object.entries(TRUST).filter(([, v]) => v.certain).map(([k]) => k)).toEqual(["valid"]);
    for (const [k, v] of Object.entries(TRUST)) if (k !== "valid") expect(v.label).toMatch(/^Предположительно/);
  });
  it("состав файлов словами", () => {
    expect(filesText([{ area: "Audio", name: "a.wav", kind: "audio_participant", state: "ok" }, { area: "Audio", name: "b.wav", kind: "audio_participant", state: "ok" }, { area: "Transcripts", name: "protocol.txt", kind: "transcript", state: "ok" }]))
      .toBe("2 записей участников, стенограмма");
    expect(filesText([])).toBe("файлов нет");
  });
  it("состояние задания", () => {
    expect(jobStatus("stale")).toMatch(/перезапуск/);
    expect(isActive(null)).toBe(false);
  });
});

describe("итог задания", () => {
  it("по-русски и без нулей", () => {
    expect(countsText({ found: 3, verified: 1, supposed: 2, present: 0 })).toBe("папок найдено: 3, достоверно: 1, предположительно: 2");
    expect(countsText(null)).toBe("");
  });
});
