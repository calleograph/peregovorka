import { describe, expect, it } from "vitest";
import { deniedText, detectBrowser, planFor } from "./permissionHint";

const UA = {
  chrome: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
  edge: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36 Edg/130.0",
  yandex: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 YaBrowser/24.10 Yowser/2.5 Safari/537.36",
  firefox: "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0",
  safari: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15",
  iphone: "Mozilla/5.0 (iPhone; CPU iPhone OS 17_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Mobile/15E148 Safari/604.1",
  android: "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Mobile Safari/537.36",
};

describe("подсказка о запросе доступа", () => {
  it("определяет семейство браузера, включая Edge и Яндекс.Браузер (Chromium)", () => {
    expect(detectBrowser(UA.chrome)).toBe("chromium");
    expect(detectBrowser(UA.edge)).toBe("chromium");
    expect(detectBrowser(UA.yandex)).toBe("chromium");
    expect(detectBrowser(UA.firefox)).toBe("firefox");
    expect(detectBrowser(UA.safari)).toBe("safari");
    expect(detectBrowser(UA.iphone)).toBe("mobile");
    expect(detectBrowser(UA.android)).toBe("mobile");
    expect(detectBrowser("curl/8.0")).toBe("unknown");
  });
  it("стрелка: слева сверху для Chromium/Firefox, к центру для Safari, без стрелки на телефонах и когда браузер неизвестен", () => {
    expect(planFor(UA.chrome).arrow).toBe("top-left");
    expect(planFor(UA.firefox).arrow).toBe("top-left");
    expect(planFor(UA.safari).arrow).toBe("top-center");
    expect(planFor(UA.iphone).arrow).toBe("none");
    const unknown = planFor("curl/8.0");
    expect(unknown.arrow).toBe("none");
    expect(unknown.title).toBe("Посмотрите на запрос браузера на доступ к камере и микрофону");
  });
  it("тексты — как согласовано", () => {
    const p = planFor(UA.chrome);
    expect(p.title).toBe("Разрешите доступ к микрофону и камере в окне браузера");
    expect(p.text).toBe("Без этого вы не сможете использовать микрофон и камеру во встрече");
  });
  it("инструкция при блокировке называет, что именно заблокировано, и говорит про «Проверить снова»", () => {
    expect(deniedText(true, false)).toContain("микрофону заблокирован");
    expect(deniedText(false, true)).toContain("камере заблокирован");
    expect(deniedText(true, true)).toContain("микрофону и камере");
    expect(deniedText(true, true)).toContain("«Проверить снова»");
  });
});
