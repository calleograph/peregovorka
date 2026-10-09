import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { EVENT_TYPES } from "./liveSocket";

/** Охрана: событие, описанное в типе LiveEvent, но не внесённое в EVENT_TYPES, молча отбрасывается сокетом — «рука» и «печатает» не доходили до остальных участников. */
describe("события живого сокета", () => {
  it("каждый тип из LiveEvent принимается сокетом", () => {
    const src = readFileSync(resolve(__dirname, "liveSocket.ts"), "utf8");
    const union = src.slice(src.indexOf("export type LiveEvent"), src.indexOf("const EVENT_TYPES") > 0 ? src.indexOf("export const EVENT_TYPES") : undefined);
    const declared = [...union.matchAll(/type: "([a-z_]+)"/g)].map((m) => m[1]);
    expect(declared.length).toBeGreaterThan(8);
    expect(declared.filter((t) => !EVENT_TYPES.has(t))).toEqual([]);
  });
  it("на подписанные типы сервер действительно отправляет события «рука» и «печатает»", () => {
    expect(EVENT_TYPES.has("hand_changed")).toBe(true);
    expect(EVENT_TYPES.has("chat_typing")).toBe(true);
  });
});
