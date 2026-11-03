import { Modal } from "./components/Dialogs";

/** Вложения во всех чатах: изображения (в том числе снимки экрана из буфера обмена) и документы, не больше 3 МБ на файл. Сервер проверяет то же самое
 *  (тип — и по содержимому), а здесь отказ виден сразу, до загрузки. Видео, звук, архивы и прочее — почтой или через файлообменник. */
export const CHAT_MAX_MB = 3;
export const CHAT_TYPES = ["png", "jpg", "jpeg", "gif", "webp", "pdf", "txt", "md", "csv", "docx", "xlsx", "pptx", "odt", "ods", "odp", "drawio"];
export const CHAT_ACCEPT = CHAT_TYPES.map((t) => `.${t}`).join(",");
export const CHAT_REJECT = "Файл превышает допустимый размер 3 МБ или имеет неподдерживаемый формат. Для передачи таких файлов воспользуйтесь электронной почтой или корпоративным файлообменником";
const MIME_EXT: Record<string, string> = { "image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp" };

/** Подходит ли файл чату. maxMb и types — от сервера (администратор может только сузить), но не шире правил чатов. */
export function chatFileOk(f: { name: string; size: number; type: string }, maxMb = CHAT_MAX_MB, types: string[] = CHAT_TYPES): boolean {
  const ext = (f.name.includes(".") ? f.name.split(".").pop()! : MIME_EXT[f.type] ?? "").toLowerCase();       // снимок экрана без имени — по типу
  const allowed = types.filter((t) => CHAT_TYPES.includes(t));
  return f.size > 0 && f.size <= Math.min(maxMb, CHAT_MAX_MB) * 1024 * 1024 && allowed.includes(ext);
}

/** Понятное предупреждение об отказе: закрывается кнопкой «Понятно». */
export function ChatFileRejected({ onClose }: { onClose: () => void }) {
  return (
    <Modal title="Файл не прикреплён" onClose={onClose}>
      <p>{CHAT_REJECT}.</p>
      <div className="row" style={{ justifyContent: "flex-end" }}><button type="button" className="btn primary" onClick={onClose}>Понятно</button></div>
    </Modal>
  );
}
