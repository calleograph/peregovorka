import { describe, expect, it } from "vitest";
import { shortCommit, versionLabel } from "./util";

describe("версия в интерфейсе", () => {
  it("версия и короткий commit (7 символов)", () => {
    expect(versionLabel("0.1.4", "6123022abcdef0123")).toBe("0.1.4 · 6123022");
    expect(shortCommit("6123022abcdef")).toBe("6123022");
  });
  it("commit неизвестен — показываем только версию, а не «unknown»", () => {
    expect(versionLabel("0.1.4", "unknown")).toBe("0.1.4");
    expect(versionLabel("0.1.4", "")).toBe("0.1.4");
    expect(versionLabel("0.1.4", null)).toBe("0.1.4");
  });
});
