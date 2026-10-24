import { describe, expect, it } from "vitest";
import { appLink, desktop, desktopState, offerApp, onDesktopCommand } from "./desktop";

describe("платформенный слой приложения Windows", () => {
  it("в браузере моста нет и вызовы ничего не делают", () => {
    expect(desktop).toBeNull();
    expect(() => desktopState({ unread: 3 })).not.toThrow();
    expect(onDesktopCommand("navigate", () => undefined)).toBeTypeOf("function");
  });
  it("ссылка «открыть в приложении» кодирует адрес встречи целиком", () => {
    expect(appLink("https://meet.example.org/m/AbCd1234?x=1&y=2")).toBe("peregovorka://open?url=https%3A%2F%2Fmeet.example.org%2Fm%2FAbCd1234%3Fx%3D1%26y%3D2");
  });
  it("предлагается только в браузере на Windows 10/11, не на телефонах", () => {
    expect(offerApp("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140.0 Safari/537.36")).toBe(true);
    expect(offerApp("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 Safari/605.1.15")).toBe(false);
    expect(offerApp("Mozilla/5.0 (Linux; Android 14; Pixel 8) Chrome/140.0 Mobile Safari/537.36")).toBe(false);
  });
});
