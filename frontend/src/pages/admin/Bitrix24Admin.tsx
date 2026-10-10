import { FormEvent, useState } from "react";
import { api, type ApiError, type BitrixDiagnose, type BitrixLookup, type BitrixSync, type BitrixUserSync } from "../../api";
import { bitrixFields } from "./fields";
import SettingsForm from "./SettingsForm";

const LABELS: Record<string, string> = { display_name: "ФИО", email: "E-mail", title: "Должность", department: "Подразделение", phone: "Телефон" };
const STATUS: Record<string, string> = { ok: "обновлено", fresh: "уже свежие", paused: "пауза после сбоя", not_found: "не найдены на портале", no_key: "нет e-mail", skipped: "пропущено", error: "ошибка", disabled: "выключено" };

/** Bitrix24 как дополнительный источник профиля: настройки, диагностика соединения, проверка на одном человеке, ручная синхронизация. Секрет webhook нигде не показывается. */
export default function Bitrix24Admin() {
  const [email, setEmail] = useState("");
  const [look, setLook] = useState<BitrixLookup | null>(null);
  const [sync, setSync] = useState<BitrixSync | null>(null);
  const [diag, setDiag] = useState<BitrixDiagnose | null>(null);
  const [who, setWho] = useState("");
  const [one, setOne] = useState<BitrixUserSync | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const run = async (f: () => Promise<void>) => {
    setBusy(true); setErr("");
    try { await f(); } catch (x) { setErr((x as ApiError).message); } finally { setBusy(false); }
  };
  const lookup = (e: FormEvent) => { e.preventDefault(); void run(async () => { setLook(null); setLook(await api.admin.bitrixLookup(email.trim())); }); };
  const diagnose = () => void run(async () => { setDiag(null); setDiag(await api.admin.bitrixDiagnose()); });
  const oneUser = (apply: boolean) => void run(async () => { setOne(null); setOne(await api.admin.bitrixSyncUser(who.trim(), apply)); });
  const runSync = () => void run(async () => { setSync(null); setSync(await api.admin.bitrixSync()); });

  return (
    <>
      <SettingsForm key="bitrix24" group="bitrix24" title="Bitrix24: данные профиля" fields={bitrixFields} testable
        intro="Дополнительный источник для карточки сотрудника. Сопоставление — по e-mail из каталога; если на портале несколько совпадений, данные не используются. Запись в Bitrix24 не выполняется. Порядок подключения: сохраните настройки → «Диагностика» → «Проверить на одном человеке» → включите интеграцию." />
      <div className="card form">
        <h2>Диагностика соединения</h2>
        <div className="row">
          <button type="button" className="btn primary" onClick={diagnose} disabled={busy}>Запустить диагностику</button>
          <span className="muted small">Проверяет адрес, webhook, выданные права и чтение сотрудников. Ничего не сохраняет.</span>
        </div>
        {diag && (
          <div className={`alert ${diag.ok ? "ok" : "error"}`} role="status">
            <strong>{diag.ok ? "Интеграция готова к работе" : "Есть проблемы"}</strong> <span className="muted small">({diag.ms} мс)</span>
            <ul className="small">
              {diag.steps.map((s) => <li key={s.name}>{s.ok ? "✓" : "✗"} <strong>{s.name}:</strong> {s.message}</li>)}
            </ul>
            {diag.scopes && <p className="small">Права вебхука: <code>{diag.scopes.join(", ") || "нет"}</code></p>}
            <p className="small">Будет использоваться: сотрудники — {diag.will_use.users ? "да" : "нет"}; названия подразделений — {diag.will_use.department ? "да" : "нет"}.</p>
          </div>
        )}
      </div>
      <div className="card form">
        <h2>Проверка на одном человеке</h2>
        <p className="muted small">Укажите логин или e-mail человека, который уже входил в систему. «Показать» ничего не меняет; «Обновить сейчас» применяет данные портала к его карточке по настроенным приоритетам.</p>
        <div className="row">
          <input value={who} onChange={(e) => setWho(e.target.value)} placeholder="логин или e-mail" aria-label="Логин или e-mail пользователя" maxLength={320} />
          <button type="button" className="btn" onClick={() => oneUser(false)} disabled={busy || !who.trim()}>Показать, что изменится</button>
          <button type="button" className="btn" onClick={() => oneUser(true)} disabled={busy || !who.trim()}>Обновить сейчас</button>
        </div>
        {one && (
          <div className={`alert ${one.ok ? "ok" : "error"}`} role="status">
            {one.ok ? "✓ " : "✗ "}{one.message}
            {one.ok && (
              <ul className="small">
                {Object.entries(one.changed ?? {}).map(([k, v]) => <li key={k}>{LABELS[k] ?? k}: «{v.from ?? "—"}» → «{v.to}»</li>)}
                {!Object.keys(one.changed ?? {}).length && <li>Текстовые поля без изменений</li>}
                {one.avatar && <li>Фото: {one.avatar}</li>}
              </ul>
            )}
          </div>
        )}
      </div>
      <div className="card form">
        <h2>Пробный поиск и массовая синхронизация</h2>
        <form className="row" onSubmit={lookup}>
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="e-mail сотрудника портала" aria-label="E-mail сотрудника" maxLength={320} required />
          <button className="btn" disabled={busy}>Показать, что вернёт портал</button>
        </form>
        {look && (
          <div className={`alert ${look.ok ? "ok" : "error"}`} role="status">
            {look.ok ? "✓ " : "✗ "}{look.message}
            {look.ok && look.fields && (
              <ul className="small">
                {Object.entries(look.fields).map(([k, v]) => <li key={k}>{LABELS[k] ?? k}: {v}</li>)}
                <li>Фото: {look.has_photo ? "есть" : "нет"}</li>
              </ul>
            )}
          </div>
        )}
        <div className="row">
          <button type="button" className="btn" onClick={runSync} disabled={busy}>Обновить профили сейчас</button>
          <span className="muted small">До 200 человек за запуск, самые недавние по входу. Обычно данные обновляются сами при входе.</span>
        </div>
        {sync && <div className="alert ok" role="status">Обработано: {sync.processed}. {Object.entries(sync.result).map(([k, n]) => `${STATUS[k] ?? k}: ${n}`).join("; ")}</div>}
        {err && <div className="alert error" role="alert">{err}</div>}
      </div>
    </>
  );
}
