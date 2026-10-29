import { describe, expect, it, vi } from "vitest";
import { guardGetUserMedia, micErrorKind, type CaptureReport } from "./micCapture";

const err = (name: string, message: string) => Object.assign(new Error(message), { name });

describe("причина отказа микрофона по ошибке Chromium", () => {
  it("различает занятость, отказ запуска, запрет системы, удаление и отказ пользователя", () => {
    expect(micErrorKind(err("NotReadableError", "Device in use"))).toBe("in_use");
    expect(micErrorKind(err("NotReadableError", "Could not start audio source"))).toBe("start_failed");
    expect(micErrorKind(err("NotReadableError", "Device was removed"))).toBe("removed");
    expect(micErrorKind(err("NotAllowedError", "Permission denied by system"))).toBe("system_denied");
    expect(micErrorKind(err("NotAllowedError", "Permission denied"))).toBe("user_denied");
    expect(micErrorKind(err("NotFoundError", "Requested device not found"))).toBe("not_found");
    expect(micErrorKind(err("OverconstrainedError", ""))).toBe("overconstrained");
  });
});

describe("защита захвата микрофона", () => {
  const stream = {} as MediaStream;

  it("временный отказ «Device in use» повторяется и сообщает о восстановлении", async () => {
    let n = 0;
    const orig = vi.fn(async () => { if (++n < 3) throw err("NotReadableError", "Device in use"); return stream; });
    const reports: CaptureReport[] = [];
    const gum = guardGetUserMedia(orig, (r) => reports.push(r), [0, 0, 0]);
    await expect(gum({ audio: true })).resolves.toBe(stream);
    expect(orig).toHaveBeenCalledTimes(3);
    expect(reports).toEqual([{ kind: "in_use", attempts: 3, recovered: true, message: "Device in use" }]);
  });

  it("настоящая блокировка: после последней попытки ошибка возвращается как есть, бесконечных повторов нет", async () => {
    const e = err("NotReadableError", "Device in use");
    const orig = vi.fn(async () => { throw e; });
    const reports: CaptureReport[] = [];
    await expect(guardGetUserMedia(orig, (r) => reports.push(r), [0, 0, 0])({ audio: true })).rejects.toBe(e);
    expect(orig).toHaveBeenCalledTimes(4);
    expect(reports[0]).toMatchObject({ kind: "in_use", attempts: 4, recovered: false });
  });

  it("запрет доступа не повторяется; видео без звука проходит мимо очереди", async () => {
    const denied = vi.fn(async () => { throw err("NotAllowedError", "Permission denied"); });
    await expect(guardGetUserMedia(denied, () => undefined, [0, 0, 0])({ audio: true })).rejects.toThrow();
    expect(denied).toHaveBeenCalledTimes(1);
    const video = vi.fn(async () => stream);
    await guardGetUserMedia(video, () => undefined)({ video: true });
    expect(video).toHaveBeenCalledWith({ video: true });
  });

  it("запросы микрофона выполняются по одному: второй ждёт окончания первого", async () => {
    let active = 0, maxActive = 0;
    const orig = vi.fn(async () => { active++; maxActive = Math.max(maxActive, active); await new Promise((r) => setTimeout(r, 5)); active--; return stream; });
    const gum = guardGetUserMedia(orig, () => undefined);
    await Promise.all([gum({ audio: true }), gum({ audio: { deviceId: "x" } }), gum({ audio: true, video: true })]);
    expect(maxActive).toBe(1);
  });

  it("отказ одного запроса не блокирует следующий", async () => {
    let n = 0;
    const orig = vi.fn(async () => { if (n++ === 0) throw err("NotFoundError", "x"); return stream; });
    const gum = guardGetUserMedia(orig, () => undefined, []);
    await expect(gum({ audio: true })).rejects.toThrow();
    await expect(gum({ audio: true })).resolves.toBe(stream);
  });
});
