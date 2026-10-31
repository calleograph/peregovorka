import { describe, expect, it } from "vitest";
import { adminPayload, managePayload, newRoomForm, toRoomForm } from "./components/RoomEditor";
import type { RoomManage } from "./api";

const room = (over: Partial<RoomManage> = {}): RoomManage => ({
  id: "r1", slug: "it-1", name: "ИТ-1", description: null, is_enabled: true, max_participants: 20, has_password: true, camera_allowed: true, screen_share_allowed: false,
  board_allowed: false, board_access: "auto", room_type: "presentation", auto_record: true, record_audio: false, transcription_enabled: true, mute_on_join: false, welcome_message: null,
  guest_access_enabled: false, guest_token: null, acl: [], moderators: [], active_meeting_id: null, can_edit_system_fields: true, video_mode: "auto", video_quality: "1080p",
  retention: { text_days: 365, audio_days: 60, video_days: 30, custom: { text: true, audio: false, video: false }, history_access: "participants", anonymize_mode: "off", anonymizer_profile_id: "p-1" }, ...over,
});

describe("единый редактор комнаты", () => {
  it("загруженные значения возвращаются в те же поля запроса (повторное открытие показывает сохранённое)", () => {
    const f = toRoomForm(room());
    const m = managePayload(f);
    expect(m).toMatchObject({ room_type: "presentation", video_mode: "auto", video_quality: "1080p", screen_share_allowed: false, auto_record: true, record_audio: true });
    expect(f.board_access).toBe("leaders");                                // старая «доска выключена» = только руководители
    expect(m).not.toHaveProperty("password");                              // пустой пароль — не менять
    // свой срок комнаты уходит с числом; общий срок сервера — только флаг, без числа (комната следует за сервером)
    expect(adminPayload(f)).toEqual({ slug: "it-1", is_enabled: true, history_access: "participants", text_retention_custom: true, text_retention_days: 365, audio_retention_custom: false, video_retention_custom: false, anonymize_mode: "off", anonymizer_profile_id: "p-1" });
  });
  it("пароль: новый задаётся, «убрать» очищает", () => {
    expect(managePayload({ ...newRoomForm(), name: "A", password: "secret1" }).password).toBe("secret1");
    expect(managePayload({ ...newRoomForm(), name: "A", password: "x", clearPassword: true }).password).toBe("");
  });
  it("новая комната — разумные значения по умолчанию; автоотправка выключена", () => {
    const f = newRoomForm();
    const m = managePayload({ ...f, name: " Новая " });
    expect(m.name).toBe("Новая");
    expect(m).toMatchObject({ video_mode: "off", room_type: "regular", guest_access_enabled: false, board_access: "auto", board_allowed: true });
    expect((m.mail_delivery as { enabled: boolean }).enabled).toBe(false);
    expect(adminPayload({ ...f, slug: " IT-2 " }).slug).toBe("it-2");
  });
});
