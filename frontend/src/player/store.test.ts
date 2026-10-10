import { beforeEach, describe, expect, it } from "vitest";
import { closePlayer, getPlayerState, openPlayer, resetPlayer, setPlayerMode, type PlayerItem } from "./store";

const a: PlayerItem = { id: "a", meetingId: "m", title: "Общая запись", kind: "mix_audio", hasVideo: false, durationS: 60, canDownload: false };
const b: PlayerItem = { ...a, id: "b", title: "Запись участника", kind: "participant" };

describe("глобальное состояние плеера", () => {
  beforeEach(resetPlayer);
  it("открытие другой записи заменяет текущую: одновременно играет только одна", () => {
    openPlayer(a);
    expect(getPlayerState().item?.id).toBe("a");
    openPlayer(b);
    expect(getPlayerState().item?.id).toBe("b");
  });
  it("повторное открытие той же записи не пересоздаёт её, а возвращает окно в полный режим", () => {
    openPlayer(a);
    const first = getPlayerState().item;
    setPlayerMode("mini");
    openPlayer(a);
    expect(getPlayerState().item).toBe(first);
    expect(getPlayerState().mode).toBe("full");
  });
  it("закрытие убирает запись; режим переключается без смены записи", () => {
    openPlayer(a);
    setPlayerMode("mini");
    expect(getPlayerState()).toMatchObject({ item: { id: "a" }, mode: "mini" });
    closePlayer();
    expect(getPlayerState().item).toBeNull();
  });
});
