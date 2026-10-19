import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import ProtectionBadge from "./ProtectionBadge";

const html = (input: { e2ee: boolean; storage?: boolean }, expected = false) =>
  renderToStaticMarkup(createElement(ProtectionBadge, { input: { https: true, storage: !!input.storage, e2ee: input.e2ee }, e2eeExpected: expected }));

describe("индикатор E2EE", () => {
  it("при выключенном E2EE элемента нет вообще (даже с шифрованием хранилища)", () => {
    expect(html({ e2ee: false })).toBe("");
    expect(html({ e2ee: false, storage: true })).toBe("");
  });
  it("при активном E2EE — компактный индикатор «🔒 Шифрование» (подробности во всплывающей подсказке)", () => {
    expect(html({ e2ee: true })).toContain("🔒 Шифрование");
    expect(html({ e2ee: true })).toContain("Сквозное шифрование звука и видео включено");
  });
  it("если комната требует E2EE, а оно не активно, показано фактическое состояние", () => {
    const h = html({ e2ee: false }, true);
    expect(h).toContain("Шифрование не активно");
    expect(h).not.toContain("Шифрование включено");
  });
});
