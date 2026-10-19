import { useMemo, useRef, useState } from "react";
import { api, type ApiError, type RoomIconInfo } from "../api";
import { ROOM_ICON_CATEGORIES, ROOM_ICONS } from "../roomIcons/icons";
import { findIcons } from "../roomIcons/search";
import { Modal } from "./Dialogs";
import ImageCropper from "./ImageCropper";
import RoomIcon from "./RoomIcon";

/** Выбор, который ещё не применён: комната создаётся позже, значок присваивается ей сразу после создания. */
export type PendingIcon = { kind: "library"; key: string } | { kind: "image"; blob: Blob } | { kind: "reset" } | null;

export async function applyPendingIcon(roomId: string, p: PendingIcon): Promise<void> {
  if (!p || p.kind === "reset") return;
  if (p.kind === "library") await api.roomIcon.library(roomId, p.key);
  else await api.roomIcon.upload(roomId, p.blob);
}

const ACCEPT = "image/png,image/jpeg,image/webp";
const MAX_FILE = 12 * 1024 * 1024;       // выбранный файл до кадрирования; на сервер уходит только результат (≤ 400 КБ)

/**
 * Блок «Значок комнаты»: текущий значок, «Выбрать из библиотеки», «Загрузить свой» (с квадратным кадрированием), «Сбросить на стандартный».
 * У существующей комнаты изменения применяются сразу (права проверяет сервер: администратор и руководитель комнаты). У создаваемой (`roomId` не задан) выбор сохраняется в `pending`
 * и применяется после создания. Исходный большой файл остаётся в браузере: на сервер отправляется только результат кадрирования.
 */
export default function RoomIconPicker({ roomId, name, icon, onChange, pending, onPending }: {
  roomId?: string; name: string; icon?: RoomIconInfo | null; onChange?: (i: RoomIconInfo | null) => void;
  pending?: PendingIcon; onPending?: (p: PendingIcon) => void;
}) {
  const [lib, setLib] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const [localUrl, setLocalUrl] = useState<string | null>(null);

  const shown: RoomIconInfo | null | undefined = !roomId && pending
    ? (pending.kind === "library" ? { kind: "library", key: pending.key, url: null } : pending.kind === "image" && localUrl ? { kind: "image", key: null, url: localUrl } : null)
    : icon;

  const run = async (fn: () => Promise<{ icon: RoomIconInfo | null }>) => {
    setBusy(true); setErr("");
    try { const r = await fn(); onChange?.(r.icon); } catch (e) { setErr((e as ApiError).message || "Не удалось сохранить значок"); } finally { setBusy(false); }
  };
  const pickLib = async (key: string) => {
    setLib(false);
    if (roomId) await run(() => api.roomIcon.library(roomId, key)); else { onPending?.({ kind: "library", key }); }
  };
  const onFile = (f: File | undefined) => {
    setErr("");
    if (!f) return;
    if (!/^image\/(png|jpeg|webp)$/.test(f.type)) { setErr("Допустимы только изображения PNG, JPG и WEBP."); return; }
    if (f.size > MAX_FILE) { setErr("Файл больше 12 МБ: выберите картинку поменьше."); return; }
    setFile(f);
  };
  const saveCropped = async (blob: Blob) => {
    if (roomId) { await api.roomIcon.upload(roomId, blob).then((r) => onChange?.(r.icon)); setFile(null); return; }
    if (localUrl) URL.revokeObjectURL(localUrl);
    setLocalUrl(URL.createObjectURL(blob)); onPending?.({ kind: "image", blob }); setFile(null);
  };
  const reset = async () => {
    if (roomId) await run(() => api.roomIcon.reset(roomId));
    else { if (localUrl) URL.revokeObjectURL(localUrl); setLocalUrl(null); onPending?.(null); }
  };

  return (
    <fieldset className="group ricon-pick">
      <legend>Значок комнаты</legend>
      <div className="ricon-now">
        <RoomIcon name={name || "Комната"} icon={shown} size={64} />
        <div className="muted small">{shown ? (shown.kind === "library" ? "Значок из библиотеки" : "Своё изображение") : "Стандартный значок: цветной квадрат с буквами названия"}
          {!roomId && " · будет применён после создания комнаты"}</div>
      </div>
      <div className="row">
        <button type="button" className="btn" disabled={busy} onClick={() => setLib(true)}>Выбрать из библиотеки</button>
        <button type="button" className="btn" disabled={busy} onClick={() => input.current?.click()}>Загрузить свой</button>
        <button type="button" className="btn ghost" disabled={busy || !shown} onClick={() => void reset()}>Сбросить на стандартный</button>
        <input ref={input} type="file" accept={ACCEPT} hidden aria-label="Файл значка" onChange={(e) => { onFile(e.target.files?.[0]); e.target.value = ""; }} />
      </div>
      <span className="help">PNG, JPG или WEBP. После выбора откроется окно кадрирования: квадрат, перемещение и масштаб. Сохраняется небольшое изображение 256×256, исходный файл на сервер не отправляется.</span>
      {err && <div className="alert error" role="alert">{err}</div>}
      {lib && <LibraryDialog name={name} current={shown?.kind === "library" ? shown.key : null} onPick={(k) => void pickLib(k)} onClose={() => setLib(false)} />}
      {file && <ImageCropper file={file} onCancel={() => setFile(null)} onDone={saveCropped} title="Значок комнаты" out={512} maxBytes={400 * 1024} shape="rounded"
                              hint="Перетащите картинку и подберите масштаб. Значок показывается квадратом со скруглёнными углами." />}
    </fieldset>
  );
}

function LibraryDialog({ name, current, onPick, onClose }: { name: string; current: string | null; onPick: (key: string) => void; onClose: () => void }) {
  const [q, setQ] = useState("");
  const [cat, setCat] = useState("");
  const [sel, setSel] = useState<string | null>(current);
  const items = useMemo(() => findIcons(q, cat), [q, cat]);
  return (
    <Modal title="Библиотека значков" onClose={onClose} wide>
      <div className="stack">
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Поиск по названию: сервер, щит, календарь…" aria-label="Поиск значка" autoFocus />
        <div className="ricon-cats" role="group" aria-label="Категории">
          <button type="button" className={`btn mini ${cat === "" ? "primary" : ""}`} aria-pressed={cat === ""} onClick={() => setCat("")}>Все ({ROOM_ICONS.length})</button>
          {ROOM_ICON_CATEGORIES.map((c) => <button key={c.id} type="button" className={`btn mini ${cat === c.id ? "primary" : ""}`} aria-pressed={cat === c.id} onClick={() => setCat(c.id)}>{c.name}</button>)}
        </div>
        <div className="ricon-grid" role="list" aria-label="Значки">
          {items.map((i) => (
            <button key={i.key} type="button" role="listitem" className="ricon-tile" aria-pressed={sel === i.key} onClick={() => setSel(i.key)} onDoubleClick={() => onPick(i.key)} title={i.name}>
              <RoomIcon name={name || "Комната"} icon={{ kind: "library", key: i.key, url: null }} size={44} /><span className="n">{i.name}</span>
            </button>))}
          {!items.length && <div className="muted">Ничего не найдено.</div>}
        </div>
        <div className="row">
          {sel && <><RoomIcon name={name || "Комната"} icon={{ kind: "library", key: sel, url: null }} size={56} /><span className="muted small">Предпросмотр</span></>}
          <div className="spacer" />
          <button type="button" className="btn primary" disabled={!sel} onClick={() => sel && onPick(sel)}>Применить</button>
          <button type="button" className="btn ghost" onClick={onClose}>Отмена</button>
        </div>
        <span className="muted ricon-lic">Значки: Lucide (лицензия ISC). Набор можно расширять — см. документацию «Значки комнат».</span>
      </div>
    </Modal>
  );
}
