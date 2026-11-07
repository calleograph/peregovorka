import { describe, expect, it } from "vitest";
import type { StorageMigration } from "./api";
import { duration, enoughSpace, isOpen, remaining } from "./storageMigration";

const job = (o: Partial<StorageMigration>) => ({ state: "copying", bytes_total: 1000, bytes_done: 400, speed_bps: 100, estimate: null, ...o }) as StorageMigration;

describe("перенос между хранилищами", () => {
  it("длительность словами", () => {
    expect(duration(null)).toBe("—");
    expect(duration(30)).toBe("меньше минуты");
    expect(duration(600)).toBe("10 мин");
    expect(duration(3900)).toBe("1 ч 5 мин");
  });
  it("остаток считается по скорости, без замера — неизвестен", () => {
    expect(remaining(job({ bytes_total: 200 << 20, bytes_done: 100 << 20, speed_bps: 1 << 20 }))).toBe(100);
    expect(remaining(job({ bytes_total: 1000, bytes_done: 10, speed_bps: 1, estimate: { categories: [], target_free: null, speed_bps: 495, seconds: null, not_in_profiles: "" } }))).toBe(2);
    expect(remaining(job({ speed_bps: null }))).toBeNull();
  });
  it("нехватка места видна до начала", () => {
    expect(enoughSpace(job({ estimate: { categories: [], target_free: 500, speed_bps: null, seconds: null, not_in_profiles: "" } }))).toBe(false);
    expect(enoughSpace(job({ estimate: null }))).toBe(true);
  });
  it("завершённое задание не занимает очередь", () => {
    expect(isOpen(job({ state: "ready" }))).toBe(true);
    expect(isOpen(job({ state: "switched" }))).toBe(false);
  });
});
