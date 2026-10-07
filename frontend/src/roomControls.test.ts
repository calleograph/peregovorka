import { describe, expect, it } from "vitest";
import { summarizeDevices } from "./clientInfo";
import { addEquals, describeFilter, isEmptyQuery, opsFor, PRESETS } from "./journalFilters";
import { journalParams } from "./api";
import { describeMediaError, isDeviceBusyError, isIceError, isTransientConnectError } from "./mediaErrors";
import { captureOptions, DEFAULT_MIC_PREFS, loadMicPrefs, saveMicPrefs } from "./micPrefs";

const memStore = (init: Record<string, string> = {}) => {
  const m = { ...init };
  return { getItem: (k: string) => m[k] ?? null, setItem: (k: string, v: string) => { m[k] = v; }, dump: () => m };
};

describe("настройки микрофона", () => {
  it("по умолчанию шумоподавление, эхоподавление и автоусиление включены, микрофон не освобождается при выключении", () => {
    expect(loadMicPrefs(memStore())).toEqual(DEFAULT_MIC_PREFS);
    expect(DEFAULT_MIC_PREFS).toMatchObject({ noiseSuppression: true, echoCancellation: true, autoGainControl: true, releaseOnMute: false });
  });
  it("сохраняются и читаются; повреждённые данные не ломают вход", () => {
    const st = memStore();
    saveMicPrefs({ ...DEFAULT_MIC_PREFS, noiseSuppression: false, releaseOnMute: true }, st);
    expect(loadMicPrefs(st)).toMatchObject({ noiseSuppression: false, releaseOnMute: true, echoCancellation: true });
    expect(loadMicPrefs(memStore({ "room.micPrefs.v1": "{oops" }))).toEqual(DEFAULT_MIC_PREFS);
    expect(loadMicPrefs(memStore({ "room.micPrefs.v1": JSON.stringify({ noiseSuppression: "yes" }) }))).toEqual(DEFAULT_MIC_PREFS);
    expect(loadMicPrefs(null)).toEqual(DEFAULT_MIC_PREFS);
  });
  it("параметры захвата берутся из настроек", () => {
    expect(captureOptions({ ...DEFAULT_MIC_PREFS, noiseSuppression: false })).toEqual({ noiseSuppression: false, echoCancellation: true, autoGainControl: true });
  });
});

describe("ошибки устройств и подключения", () => {
  it("занятый микрофон распознаётся и объясняется (в т. ч. монопольный режим Windows)", () => {
    const e = Object.assign(new Error("Could not start audio source"), { name: "NotReadableError" });
    expect(isDeviceBusyError(e)).toBe(true);
    const m = describeMediaError(e, "mic", { secureContext: true });
    expect(m.message).toMatch(/занят/);
    expect(m.message).toMatch(/монопольн/);
    expect(m.message).toMatch(/без микрофона/);
    expect(isDeviceBusyError(new Error("x"))).toBe(false);
  });
  it("«could not establish pc connection» — это ICE, а не общая ошибка действия", () => {
    const e = Object.assign(new Error("could not establish pc connection"), { name: "ConnectionError" });
    expect(isIceError(e)).toBe(true);
    const m = describeMediaError(e, "connect", { secureContext: true });
    expect(m.reason).toBe("IceFailed");
    expect(m.message).toMatch(/UDP|порт/);
    expect(m.message).not.toMatch(/Не удалось выполнить действие/);
  });
  it("повторять подключение имеет смысл при сетевых сбоях и ICE, но не при ошибках прав", () => {
    expect(isTransientConnectError(new Error("could not establish pc connection"))).toBe(true);
    expect(isTransientConnectError(new Error("Request timed out"))).toBe(true);
    expect(isTransientConnectError(new Error("websocket closed"))).toBe(true);
    expect(isTransientConnectError(Object.assign(new Error("denied"), { name: "NotAllowedError" }))).toBe(false);
    expect(isTransientConnectError(new Error("room is full"))).toBe(false);
  });
});

describe("сведения об устройствах для журнала", () => {
  it("считает устройства по видам и берёт названия, не более нескольких", () => {
    const list = [
      { kind: "audioinput", label: "Микрофон (Realtek)" }, { kind: "audioinput", label: "" }, { kind: "audiooutput", label: "Динамики" },
      { kind: "videoinput", label: "HD Webcam" },
    ] as MediaDeviceInfo[];
    expect(summarizeDevices(list)).toEqual({ mics: 2, speakers: 1, cameras: 1, mic_names: ["Микрофон (Realtek)"], camera_names: ["HD Webcam"] });
    expect(summarizeDevices([])).toMatchObject({ mics: 0, cameras: 0 });
  });
});

describe("фильтры журнала", () => {
  it("щелчок по значению добавляет условие «равно» один раз", () => {
    const a = addEquals([], "user", "ivanov");
    expect(a).toEqual([{ field: "user", op: "eq", value: "ivanov" }]);
    expect(addEquals(a, "user", "ivanov")).toBe(a);
    expect(addEquals(a, "room", "ИТ-1")).toHaveLength(2);
  });
  it("«не ниже» — только для уровня", () => {
    expect(opsFor("level")).toContain("gte");
    expect(opsFor("user")).not.toContain("gte");
    expect(opsFor("user")).toEqual(expect.arrayContaining(["eq", "ne", "contains", "not_contains"]));
  });
  it("параметры запроса: пустые условия не передаются, остальное — JSON", () => {
    const p = new URLSearchParams(journalParams({ filters: [{ field: "user", op: "ne", value: "x" }, { field: "room", op: "eq", value: " " }], q: " сеть ", range: "24h" }, 55, 50));
    expect(JSON.parse(p.get("filters")!)).toEqual([{ field: "user", op: "ne", value: "x" }]);
    expect(p.get("q")).toBe("сеть");
    expect(p.get("range")).toBe("24h");
    expect(p.get("before_id")).toBe("55");
    expect(p.get("limit")).toBe("50");
    expect(new URLSearchParams(journalParams({ filters: [] })).has("filters")).toBe(false);
  });
  it("готовые наборы корректны и описываются по-русски", () => {
    expect(PRESETS.length).toBeGreaterThanOrEqual(6);
    for (const p of PRESETS) for (const f of p.query.filters) expect(describeFilter(f)).toMatch(/«.+»/);
    expect(describeFilter({ field: "level", op: "gte", value: "warn" })).toBe("Уровень не ниже «предупреждение»");
    expect(isEmptyQuery({ filters: [], q: "  " }, "")).toBe(true);
    expect(isEmptyQuery({ filters: [], q: "x" }, "")).toBe(false);
  });
});
