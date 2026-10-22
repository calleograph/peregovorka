import { describe, expect, it } from "vitest";
import { transcriptionStoppedText } from "./recordingNotice";

describe("уведомление об остановке транскрибации", () => {
  it("соответствует фактическим записям, без ложного «запись продолжается»", () => {
    expect(transcriptionStoppedText(false, false)).toBe("Транскрибация остановлена");
    expect(transcriptionStoppedText(true, false)).toBe("Транскрибация остановлена. Аудиозапись продолжается");
    expect(transcriptionStoppedText(false, true)).toBe("Транскрибация остановлена. Видеозапись продолжается");
    expect(transcriptionStoppedText(true, true)).toBe("Транскрибация остановлена. Аудио- и видеозапись продолжаются");
  });
});
