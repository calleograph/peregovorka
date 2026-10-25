import { describe, expect, it } from "vitest";
import { IMAGE_RETRIES, retryUrl } from "./useImageFallback";

describe("повторная загрузка картинки", () => {
  it("первая попытка — адрес как есть, повтор — с новой меткой, версия сохраняется", () => {
    expect(retryUrl("/api/v1/rooms/1/icon?v=abc", 0)).toBe("/api/v1/rooms/1/icon?v=abc");
    expect(retryUrl("/api/v1/rooms/1/icon?v=abc", 1)).toBe("/api/v1/rooms/1/icon?v=abc&r=1");
    expect(retryUrl("/api/v1/users/2/avatar", 1)).toBe("/api/v1/users/2/avatar?r=1");
  });
  it("повторов немного: дальше показывается стандартный значок", () => { expect(IMAGE_RETRIES).toBe(1); });
});
