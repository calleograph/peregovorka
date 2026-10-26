import { describe, expect, it } from "vitest";
import { originMismatch } from "./networkView";

describe("originMismatch", () => {
  it("совпадающий адрес — без предупреждения, регистр не важен", () => {
    expect(originMismatch("Meet.Example.org", "meet.example.org")).toBeNull();
  });
  it("другой адрес — предупреждение с обоими адресами", () => {
    const m = originMismatch("192.0.2.10", "meet.example.org");
    expect(m).toContain("192.0.2.10");
    expect(m).toContain("meet.example.org");
  });
  it("публичный адрес неизвестен — молчим", () => {
    expect(originMismatch("meet.example.org", "")).toBeNull();
  });
});
