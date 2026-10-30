// Выбор общей сцены для видеозаписи (страница-рекордер, шаблон Egress). Чистая функция: решает по данным сервера и дорожкам комнаты, без DOM и сети.
//
// Приоритет (совпадает с backend/app/services/rec_scene.py):
//   1. явная сцена ведущего («Показать всем»): камеры, экраны, доска — только то, что ещё существует;
//   2. показ экрана любым участником;
//   3. доска, пока над ней работают (сервер сообщает `board.active`);
//   4. обычная конференция: говорящий крупно, остальные — полосой.
// Локальные закрепления и открытые панели отдельных участников сюда не попадают: запись следует за общей сценой.

export interface StageItem { type: "camera" | "screen" | "board"; identity?: string }
export interface SceneInput {
  stage: StageItem[];
  boardActive: boolean;
  boardHasContent: boolean;
  /** Участники с опубликованной камерой / экраном. Порядок — порядок появления. */
  cameras: string[];
  screens: string[];
  speaker: string | null;
}
export type SceneReason = "stage" | "screen" | "board" | "conference" | "empty";
export interface Scene { main: string[]; strip: string[]; reason: SceneReason }

export const keyOf = (i: StageItem): string => (i.type === "board" ? "board" : `${i.type}:${i.identity}`);

export function pickScene(i: SceneInput): Scene {
  const cams = new Set(i.cameras), scr = new Set(i.screens);
  const exists = (it: StageItem) => (it.type === "board" ? true : it.type === "camera" ? cams.has(it.identity ?? "") : scr.has(it.identity ?? ""));
  const camKeys = i.cameras.map((id) => `camera:${id}`);
  const rest = (main: string[]) => camKeys.filter((k) => !main.includes(k));

  const staged = i.stage.filter(exists).map(keyOf);
  if (staged.length) return { main: staged.slice(0, 4), strip: rest(staged), reason: "stage" };
  if (i.screens.length) { const main = [`screen:${i.screens[0]}`]; return { main, strip: rest(main), reason: "screen" }; }
  if (i.boardActive && i.boardHasContent) return { main: ["board"], strip: camKeys, reason: "board" };
  if (!i.cameras.length) return { main: [], strip: [], reason: "empty" };
  const sp = i.speaker && cams.has(i.speaker) ? i.speaker : i.cameras[0];
  const main = [`camera:${sp}`];
  return { main, strip: rest(main), reason: "conference" };
}
