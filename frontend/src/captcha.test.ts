import { createHash } from "node:crypto";
import { describe, expect, it } from "vitest";
import { sha256Hex, solveCaptcha } from "./captcha";

describe("captcha", () => {
  it("SHA-256 совпадает с эталоном, в том числе для кириллицы и длинных строк", () => {
    for (const s of ["", "abc", "ab?expires=1760000000" + 12345, "Привет, мир", "x".repeat(55), "x".repeat(56), "x".repeat(64), "x".repeat(1000)]) {
      expect(sha256Hex(s)).toBe(createHash("sha256").update(s, "utf8").digest("hex"));
    }
  });

  it("подбирает число и собирает заголовок", async () => {
    const salt = "deadbeef?expires=9999999999";
    const challenge = createHash("sha256").update(`${salt}777`).digest("hex");
    const header = await solveCaptcha({ algorithm: "SHA-256", challenge, salt, signature: "sig", maxnumber: 2000 });
    const p = JSON.parse(atob(header));
    expect(p).toEqual({ algorithm: "SHA-256", challenge, number: 777, salt, signature: "sig" });
  });

  it("если решения нет в пределах maxnumber — ошибка, а не бесконечный цикл", async () => {
    await expect(solveCaptcha({ algorithm: "SHA-256", challenge: "0".repeat(64), salt: "s", signature: "x", maxnumber: 50 })).rejects.toThrow();
  });
});
