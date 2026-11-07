import { describe, expect, it } from "vitest";
import roomPage from "../pages/RoomPage.tsx?raw";
import { showLiveQuestions } from "./LiveControl";

describe("эфир в комнате", () => {
  it("вопросы зрителей видны ведущему обычной и презентационной комнаты, пока идёт эфир и после него", () => {
    expect(showLiveQuestions({ broadcastRoom: true, canControl: false, wasLive: false })).toBe(true);
    expect(showLiveQuestions({ broadcastRoom: false, canControl: true, wasLive: true })).toBe(true);
    expect(showLiveQuestions({ broadcastRoom: false, canControl: true, wasLive: false })).toBe(false);
    expect(showLiveQuestions({ broadcastRoom: false, canControl: false, wasLive: true })).toBe(false);
  });

  it("красная кнопка запускает эфир тем же путём, что окно «Трансляция»", () => {
    expect(roomPage).toContain("onStart={lv.start}");
    expect(roomPage).toContain("lv.start().catch(");
    expect(roomPage).not.toContain("api.setLive(join.meeting_id, true)");
  });
});
