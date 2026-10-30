import { describe, expect, it, vi } from "vitest";
import { ApiError } from "./api";
import { confirmUpload, readUpload, sha256Hex, withRetry } from "./uploadVerify";

const PNG = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 1, 2, 3]);

describe("загрузка вложений с подтверждением", () => {
  it("пустой файл отклоняется сразу, с понятным текстом (а не отправляется «как будто успешно»)", async () => {
    await expect(readUpload(new Blob([]))).rejects.toMatchObject({ code: "empty" });
  });

  it("байты читаются в память, SHA-256 совпадает с эталоном", async () => {
    const up = await readUpload(new Blob([PNG]));
    expect(up.bytes.byteLength).toBe(PNG.length);
    expect(up.sha256).toBe(await sha256Hex(PNG.buffer.slice(0) as ArrayBuffer));
    expect(up.sha256).toMatch(/^[0-9a-f]{64}$/);
  });

  it("вложение считается отправленным, только если сервер принял столько же байт и ту же контрольную сумму", async () => {
    const up = await readUpload(new Blob([PNG]));
    expect(() => confirmUpload({ size: PNG.length, sha256: up.sha256! }, up)).not.toThrow();
    expect(() => confirmUpload({ size: PNG.length }, up)).not.toThrow();                         // старый сервер: нет контрольной суммы — размера достаточно
    expect(() => confirmUpload({ size: PNG.length - 3, sha256: up.sha256! }, up)).toThrow(/не целиком/);
    expect(() => confirmUpload({ size: PNG.length, sha256: "0".repeat(64) }, up)).toThrow(/изменённым/);
  });

  it("повтор только при сетевых сбоях и 502/503/504; отказы сервера (тип, размер) не повторяются", async () => {
    const sleep = vi.fn(async () => undefined);
    let n = 0;
    const flaky = async () => { if (++n < 3) throw new ApiError(0, "network", "Нет связи с сервером"); return "ok"; };
    await expect(withRetry(flaky, [1, 2], sleep)).resolves.toBe("ok");
    expect(n).toBe(3);
    expect(sleep).toHaveBeenCalledTimes(2);
    let m = 0;
    await expect(withRetry(async () => { m++; throw new ApiError(422, "error", "Такой тип файла не разрешён"); }, [1, 2], sleep)).rejects.toMatchObject({ status: 422 });
    expect(m).toBe(1);
    await expect(withRetry(async () => { throw new ApiError(503, "error", "хранилище недоступно"); }, [1], sleep)).rejects.toMatchObject({ status: 503 });
  });
});
