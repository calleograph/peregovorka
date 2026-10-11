/**
 * Экран перед входом: кем пользователь войдёт и что на этом экране показывать. Чистые функции — проверяются тестами.
 *  - regular — обычная комната (всё как раньше: проверка оборудования, «Микрофон включится сразу»);
 *  - leader  — руководитель (или администратор) презентации: выступает, поэтому проверка микрофона и камеры доступна до входа, а состояние микрофона выбирает сам;
 *  - viewer  — рядовой участник и гость презентации: входит зрителем, микрофон и камера выключены и не запрашиваются, проверки оборудования нет.
 * Роль берётся из данных сервера (`can_manage` считает backend по ролям комнаты), а не из интерфейса.
 */

export type JoinMode = "regular" | "leader" | "viewer";

export interface RoomLike { room_type?: string; can_manage?: boolean; mute_on_join?: boolean }

export function joinMode(room: RoomLike | null | undefined): JoinMode {
  if (!room || room.room_type !== "presentation") return "regular";
  return room.can_manage ? "leader" : "viewer";
}

/** Состояние микрофона на экране входа по умолчанию: руководитель презентации — включён, если комната не задаёт «входят с выключенным микрофоном»; зритель — выключен. */
export const initialMic = (mode: JoinMode, muteOnJoin?: boolean): boolean => (mode === "viewer" ? false : !muteOnJoin);

export type FactIcon = "mic" | "micOff" | "eye" | "hand";
export interface Fact { icon: FactIcon; title: string; text: string }

/** Сообщения о микрофоне на экране входа: по роли и выбору пользователя (а не одно «включится сразу» для всех). */
export function micFacts(mode: JoinMode, micOn: boolean): Fact[] {
  if (mode === "viewer") {
    return [
      { icon: "eye", title: "Вы входите в режиме зрителя", text: "Микрофон и камера выключены. Руководитель может предоставить вам слово." },
      { icon: "hand", title: "После предоставления слова", text: "вы сможете самостоятельно включить микрофон, камеру и другие разрешённые функции." },
    ];
  }
  if (mode === "leader") {
    return micOn
      ? [{ icon: "mic", title: "Вы входите как руководитель: микрофон будет включён", text: "Он включится сразу после входа — так вы выбрали. Выключить его можно кнопкой в комнате." }]
      : [{ icon: "micOff", title: "Вы входите как руководитель: микрофон выключен", text: "Он не включится сам. Когда понадобится, нажмите «Микрофон» в комнате." }];
  }
  return [{ icon: "mic", title: "Микрофон включится сразу", text: "Выключить его можно кнопкой в комнате." }];
}

/** Нужно ли включать микрофон при входе. Руководитель презентации — по выбору на экране входа; везде остальное — как раньше (по настройке комнаты). */
export function wantMicOnJoin(o: { presentation: boolean; canManage: boolean; muteOnJoin: boolean; chosen?: boolean }): boolean {
  if (o.presentation && o.canManage && o.chosen !== undefined) return o.chosen;
  return !o.muteOnJoin;
}
