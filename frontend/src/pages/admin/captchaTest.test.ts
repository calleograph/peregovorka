import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";

// Окно проверки CAPTCHA обязано использовать ТОТ ЖЕ компонент картинки/поля, что окно входа, а не собственную копию вёрстки.
describe("проверка CAPTCHA в администрировании", () => {
  const dialog = readFileSync(new URL("../../components/CaptchaDialog.tsx", import.meta.url), "utf8");
  const admin = readFileSync(new URL("./CaptchaAdmin.tsx", import.meta.url), "utf8");
  it("делит с окном входа один компонент картинки и поля ввода", () => {
    expect(dialog).toContain("<CaptchaImageField");
    expect(admin).toContain("CaptchaImageField");
    expect(admin).not.toContain("captcha-img-row");
  });
  it("ходит только на отдельные тестовые адреса и не трогает вход", () => {
    expect(admin).not.toMatch(/auth\/login|api\.login/);
  });
});
