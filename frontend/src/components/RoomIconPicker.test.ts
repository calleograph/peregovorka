import { beforeEach, describe, expect, it, vi } from "vitest";

const calls: string[] = [];
vi.mock("../api", () => ({ api: { roomIcon: {
  library: async (_r: string, k: string) => { calls.push(`library ${k}`); },
  upload: async () => { calls.push("upload"); },
  color: async (_r: string, h: number | null) => { calls.push(`color ${h}`); },
} } }));
const { applyPendingIcon } = await import("./RoomIconPicker");

describe("значок новой комнаты применяется после её создания", () => {
  beforeEach(() => { calls.length = 0; });
  it("значок из библиотеки и цвет — оба запроса, цвет после значка", async () => {
    await applyPendingIcon("r", { key: "server", hue: 145 });
    expect(calls).toEqual(["library server", "color 145"]);
  });
  it("только цвет — буквенный значок своего цвета", async () => {
    await applyPendingIcon("r", { hue: 0 });
    expect(calls).toEqual(["color 0"]);
  });
  it("картинка без цвета; пустой выбор — ничего", async () => {
    await applyPendingIcon("r", { blob: new Blob(["x"]) });
    await applyPendingIcon("r", null);
    await applyPendingIcon("r", { hue: null });
    expect(calls).toEqual(["upload"]);
  });
});
