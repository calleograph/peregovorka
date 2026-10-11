import { describe, expect, it } from "vitest";
import { initialMic, joinMode, micFacts, wantMicOnJoin } from "./prejoinMode";

describe("who enters how", () => {
  it("regular rooms are untouched, whoever enters", () => {
    expect(joinMode({ room_type: "regular", can_manage: false })).toBe("regular");
    expect(joinMode({ room_type: "regular", can_manage: true })).toBe("regular");
    expect(joinMode(null)).toBe("regular");
    expect(joinMode({})).toBe("regular");
  });

  it("presentation: leaders and administrators are speakers, everyone else is a viewer", () => {
    expect(joinMode({ room_type: "presentation", can_manage: true })).toBe("leader");
    expect(joinMode({ room_type: "presentation", can_manage: false })).toBe("viewer");
    expect(joinMode({ room_type: "presentation" })).toBe("viewer");
  });
});

describe("microphone state on the welcome screen", () => {
  it("viewer: off; leader: on unless the room says join muted; regular: as before", () => {
    expect(initialMic("viewer", false)).toBe(false);
    expect(initialMic("leader", false)).toBe(true);
    expect(initialMic("leader", true)).toBe(false);
    expect(initialMic("regular", false)).toBe(true);
    expect(initialMic("regular", true)).toBe(false);
  });

  it("the leader choice decides whether the microphone is switched on at entry", () => {
    const base = { presentation: true, canManage: true, muteOnJoin: false };
    expect(wantMicOnJoin({ ...base, chosen: false })).toBe(false);
    expect(wantMicOnJoin({ ...base, chosen: true })).toBe(true);
    expect(wantMicOnJoin({ ...base, muteOnJoin: true, chosen: true })).toBe(true);
    expect(wantMicOnJoin({ ...base, muteOnJoin: true, chosen: false })).toBe(false);
    expect(wantMicOnJoin({ ...base })).toBe(true);                                     // выбора не было (экран пропущен): прежнее поведение
    expect(wantMicOnJoin({ ...base, muteOnJoin: true })).toBe(false);
  });

  it("choice never changes regular rooms or viewers", () => {
    expect(wantMicOnJoin({ presentation: false, canManage: true, muteOnJoin: false, chosen: false })).toBe(true);
    expect(wantMicOnJoin({ presentation: false, canManage: false, muteOnJoin: true, chosen: true })).toBe(false);
    expect(wantMicOnJoin({ presentation: true, canManage: false, muteOnJoin: false, chosen: true })).toBe(true);        // зритель выбора не имеет; микрофон ему закрывает RoomPage по правам
  });
});

describe("texts", () => {
  it("viewer is told exactly what happens, no automatic microphone", () => {
    const f = micFacts("viewer", true);
    const text = f.map((x) => `${x.title}. ${x.text}`).join(" ");
    expect(text).toContain("Вы входите в режиме зрителя");
    expect(text).toContain("Микрофон и камера выключены");
    expect(text).toContain("Руководитель может предоставить вам слово");
    expect(text).toContain("самостоятельно включить микрофон, камеру");
    expect(text).not.toContain("включится сразу");
  });

  it("leader sees the state he chose", () => {
    expect(micFacts("leader", true)[0].title).toContain("микрофон будет включён");
    expect(micFacts("leader", false)[0].title).toContain("микрофон выключен");
    expect(micFacts("leader", false)[0].text).toContain("не включится сам");
  });

  it("regular rooms keep the old text", () => {
    expect(micFacts("regular", true)).toEqual([{ icon: "mic", title: "Микрофон включится сразу", text: "Выключить его можно кнопкой в комнате." }]);
  });
});
