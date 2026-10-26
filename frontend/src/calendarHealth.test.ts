import { describe, expect, it } from "vitest";
import { healthOf } from "./calendarHealth";
import type { CalendarEvents } from "./calendarApi";

const data = (o: Partial<CalendarEvents> = {}): CalendarEvents => ({ events: [], from: "", to: "", server_time: "", stale: false, error: null, mode: "graph", ...o });
const base = { loading: false, err: "", code: "", data: data(), lastOk: new Date(2026, 9, 26, 14, 5) };

describe("состояние подключения словами", () => {
  it("подключён, идёт синхронизация, нет соединения с устаревшими данными, нет прав, нужна повторная авторизация", () => {
    expect(healthOf(base)).toMatchObject({ kind: "ok" });
    expect(healthOf(base).text).toContain("14:05");
    expect(healthOf({ ...base, loading: true }).kind).toBe("sync");
    const stale = healthOf({ ...base, data: data({ stale: true, error: "Нет связи с Exchange" }) });
    expect(stale.kind).toBe("stale"); expect(stale.text).toContain("могли устареть"); expect(stale.text).toContain("14:05");
    expect(healthOf({ ...base, err: "Graph отказал в доступе к календарю: выдайте приложению право Calendars.ReadBasic.All" }).kind).toBe("denied");
    expect(healthOf({ ...base, code: "forbidden", err: "Нет" }).kind).toBe("denied");
    expect(healthOf({ ...base, code: "not_connected", err: "x" }).kind).toBe("relogin");
    expect(healthOf({ ...base, err: "Нет связи с Microsoft Graph (ConnectError)." }).kind).toBe("down");
  });
  it("устаревшие данные никогда не выдаются за актуальные: при ошибке состояние не «ok»", () => {
    for (const d of [data({ stale: true }), data({ error: "Exchange не ответил вовремя." })]) expect(healthOf({ ...base, data: d }).kind).not.toBe("ok");
    expect(healthOf({ ...base, loading: true, data: data({ stale: true }) }).kind).toBe("stale");
  });
});
