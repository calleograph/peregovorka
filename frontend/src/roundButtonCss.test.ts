import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

/** Охрана: модификаторы круглой кнопки панели (тон и «говорит») не должны совпадать с общими однословными классами стилей.
 *  Так было с `.pulse` — точкой 8×8 из design.css: пока участник говорил, кнопка «Микрофон» сжималась до точки (найдено живым сценарием панели управления). */
describe("RoundButton: классы-модификаторы не пересекаются с общими стилями", () => {
  const css = ["styles.css", "design.css"].map((f) => readFileSync(resolve(__dirname, f), "utf8")).join("\n");
  const src = readFileSync(resolve(__dirname, "components/room/RoundButton.tsx"), "utf8");
  const tones = [...(src.match(/export type Tone = ([^;]+);/)?.[1] ?? "").matchAll(/"([a-z]+)"/g)].map((m) => m[1]);
  const extra = [...src.matchAll(/\? "([a-z-]+)" : ""/g)].map((m) => m[1]);
  it("список модификаторов найден", () => { expect(tones.length).toBeGreaterThan(4); expect(extra).toContain("speak"); });
  it("ни один модификатор не задан в CSS отдельным селектором верхнего уровня", () => {
    const bare = [...tones, ...extra].filter((c) => new RegExp(`(^|[},\\s])\\.${c}\\s*[{,:]`, "m").test(css));
    expect(bare).toEqual([]);
  });
});
