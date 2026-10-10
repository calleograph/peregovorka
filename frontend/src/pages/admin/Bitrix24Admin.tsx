import { FormEvent, useState } from "react";
import { api, type ApiError, type BitrixLookup, type BitrixSync } from "../../api";
import { bitrixFields } from "./fields";
import SettingsForm from "./SettingsForm";

const LABELS: Record<string, string> = { display_name: "ФИО", email: "E-mail", title: "Должность", department: "Подразделение", phone: "Телефон" };
const STATUS: Record<string, string> = { ok: "обновлено", fresh: "уже свежие", paused: "пауза после сбоя", not_found: "не найдены на портале", no_key: "нет e-mail", skipped: "пропущено", error: "ошибка", disabled: "выключено" };

/** Bitrix24 как дополнительный источник профиля: настройки, пробный поиск по e-mail и ручная синхронизация. Секрет webhook нигде не показывается. */
export default function Bitrix24Admin() {
  const [email, setEmail] = useState("");
  const [look, setLook] = useState<BitrixLookup | null>(null);
  const [sync, setSync] = useState<BitrixSync | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const lookup = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true); setErr(""); setLook(null);
    try { setLook(await api.admin.bitrixLookup(email.trim())); } catch (x) { setErr((x as ApiError).message); } finally { setBusy(false); }
  };
  const runSync = async () => {
    setBusy(true); setErr(""); setSync(null);
    try { setSync(await api.admin.bitrixSync()); } catch (x) { setErr((x as ApiError).message); } finally { setBusy(false); }
  };

  return (
    <>
      <SettingsForm key="bitrix24" group="bitrix24" title="Bitrix24: данные профиля" fields={bitrixFields} testable
        intro="Дополнительный источник для карточки сотрудника. Сопоставление — по e-mail из каталога; если на портале несколько совпадений, данные не используются. Запись в Bitrix24 не выполняется. Сохраните настройки, затем нажмите «Проверить подключение»." />
      <div className="card form">
        <h2>Проверка и синхронизация</h2>
        <form className="row" onSubmit={lookup}>
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="e-mail сотрудника" aria-label="E-mail сотрудника" maxLength={320} required />
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
