import { describe, expect, it } from "vitest";
import { pickScene, type SceneInput } from "./scene";

const base: SceneInput = { stage: [], boardActive: false, boardHasContent: true, cameras: ["a", "b", "c"], screens: [], speaker: null };

describe("общая сцена видеозаписи", () => {
  it("обычная конференция: говорящий крупно, остальные полосой", () => {
    expect(pickScene({ ...base, speaker: "b" })).toEqual({ main: ["camera:b"], strip: ["camera:a", "camera:c"], reason: "conference" });
  });
  it("нет говорящего — первый участник; говорящий без камеры не выбирается", () => {
    expect(pickScene(base).main).toEqual(["camera:a"]);
    expect(pickScene({ ...base, speaker: "zz" }).main).toEqual(["camera:a"]);
  });
  it("показ экрана главнее обычной конференции", () => {
    expect(pickScene({ ...base, screens: ["b"], speaker: "a" })).toEqual({ main: ["screen:b"], strip: ["camera:a", "camera:b", "camera:c"], reason: "screen" });
  });
  it("работа над доской: доска крупно, пока она активна, затем возврат к участникам", () => {
    expect(pickScene({ ...base, boardActive: true })).toEqual({ main: ["board"], strip: ["camera:a", "camera:b", "camera:c"], reason: "board" });
    expect(pickScene({ ...base, boardActive: false, speaker: "a" }).reason).toBe("conference");
  });
  it("пустая доска не показывается, даже если «активна»", () => {
    expect(pickScene({ ...base, boardActive: true, boardHasContent: false }).reason).toBe("conference");
  });
  it("явная сцена ведущего главнее показа экрана и доски", () => {
    const s = pickScene({ ...base, stage: [{ type: "camera", identity: "c" }], screens: ["a"], boardActive: true });
    expect(s).toEqual({ main: ["camera:c"], strip: ["camera:a", "camera:b"], reason: "stage" });
    expect(pickScene({ ...base, stage: [{ type: "board" }, { type: "screen", identity: "a" }], screens: ["a"] }).main).toEqual(["board", "screen:a"]);
  });
  it("ведущий убрал участника со сцены — исчезнувшие элементы пропускаются, запись возвращается к правилам по умолчанию", () => {
    expect(pickScene({ ...base, stage: [{ type: "camera", identity: "gone" }], speaker: "b" }).reason).toBe("conference");
    expect(pickScene({ ...base, stage: [{ type: "screen", identity: "gone" }], screens: [] }).reason).toBe("conference");
  });
  it("не больше четырёх крупных элементов", () => {
    const st = (["a", "b", "c"] as const).map((id) => ({ type: "camera" as const, identity: id })).concat([{ type: "camera", identity: "d" }, { type: "camera", identity: "e" }]);
    expect(pickScene({ ...base, cameras: ["a", "b", "c", "d", "e"], stage: st }).main).toHaveLength(4);
  });
  it("в комнате никого нет — пустая сцена", () => {
    expect(pickScene({ ...base, cameras: [] })).toEqual({ main: [], strip: [], reason: "empty" });
  });
});
