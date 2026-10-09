import { FormEvent, useCallback, useEffect, useState } from "react";
import { api, type ApiError, type StorageProfile, type TestResult } from "../../api";

interface Form {
  id?: string; name: string; kind: "local" | "smb"; local_path: string; smb_server: string; smb_share: string; smb_base_path: string;
  smb_domain: string; smb_username: string; secret: string; secret_set: boolean;
}

const empty: Form = { name: "", kind: "smb", local_path: "/data/exports", smb_server: "", smb_share: "", smb_base_path: "", smb_domain: "", smb_username: "", secret: "", secret_set: false };

const toForm = (p: StorageProfile): Form => ({
  id: p.id, name: p.name, kind: p.kind, local_path: p.config.local_path ?? "/data/exports", smb_server: p.config.smb_server ?? "", smb_share: p.config.smb_share ?? "",
  smb_base_path: p.config.smb_base_path ?? "", smb_domain: p.config.smb_domain ?? "", smb_username: p.config.smb_username ?? "", secret: "", secret_set: p.secret_set,
});

const FOLDER_HELP: [string, string][] = [
  ["Audio", "записи аудио"], ["Transcripts", "стенограммы"], ["Protocols", "протоколы и резюме"], ["Chat", "переписка и вложения чата"], ["Boards", "схемы доски"], ["Logs", "журнал событий"],
];

/**
 * Хранилища создаются один раз: «Файловый сервер №1 → SMB → \\сервер\ресурс → учётная запись». Функции (записи, протоколы, вложения чата, журнал)
 * потом лишь выбирают хранилище в своих разделах — адрес и пароль второй раз вводить не нужно. Подпапки внутри создаются автоматически.
 */
export default function StoragesAdmin({ onOpen }: { onOpen?: (id: string) => void }) {
  const [items, setItems] = useState<StorageProfile[]>([]);
  const [form, setForm] = useState<Form | null>(null);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [tests, setTests] = useState<Record<string, TestResult | "run">>({});

  const load = useCallback(() => api.admin.storages().then((r) => setItems(r.items)).catch((e) => setError((e as ApiError).message)), []);
  useEffect(() => { void load(); }, [load]);

  const set = <K extends keyof Form>(k: K, v: Form[K]) => setForm((f) => (f ? { ...f, [k]: v } : f));

  const save = async (e: FormEvent) => {
    e.preventDefault();
    if (!form) return;
    setError(""); setNote("");
    if (!form.name.trim()) { setError("Укажите название хранилища, например «Файловый сервер №1»."); return; }
    const config: Record<string, string> = form.kind === "local" ? { local_path: form.local_path } : {
      smb_server: form.smb_server.trim(), smb_share: form.smb_share.trim(), smb_base_path: form.smb_base_path.trim(), smb_domain: form.smb_domain.trim(), smb_username: form.smb_username.trim() };
    setBusy(true);
    try {
      if (form.id) await api.admin.updateStorage(form.id, { name: form.name.trim(), config, ...(form.secret !== "" ? { secret: form.secret } : {}) });
      else await api.admin.createStorage({ name: form.name.trim(), kind: form.kind, config, ...(form.secret ? { secret: form.secret } : {}) });
      setForm(null); setNote("Сохранено. Нажмите «Проверить» — приложение попробует записать файл и создать подпапки."); await load();
    } catch (err) { setError((err as ApiError).message); }
    setBusy(false);
  };

  const test = async (p: StorageProfile) => {
    setTests((t) => ({ ...t, [p.id]: "run" }));
    try { const r = await api.admin.testStorage(p.id); setTests((t) => ({ ...t, [p.id]: r })); }
    catch (e) { setTests((t) => ({ ...t, [p.id]: { ok: false, message: (e as ApiError).message, ms: 0 } })); }
  };

  const remove = async (p: StorageProfile) => {
    if (!window.confirm(`Удалить хранилище «${p.name}»? Файлы на самом ресурсе не удаляются.`)) return;
    setError(""); setNote("");
    try { await api.admin.deleteStorage(p.id); await load(); } catch (e) { setError((e as ApiError).message); }
  };

  return (
    <section>
      <div className="row"><h2>Файловые хранилища</h2><div className="spacer" />
        {!form && <button className="btn primary" onClick={() => { setForm({ ...empty }); setError(""); setNote(""); }}>＋ Добавить хранилище</button>}</div>
      <p className="muted">Адрес и учётная запись вводятся здесь один раз. Записи, протоколы, вложения чата и журнал только выбирают хранилище в своих разделах. Внутри автоматически создаются подпапки:{" "}
        {FOLDER_HELP.map(([f, d], i) => <span key={f}>{i ? ", " : ""}<code>{f}/</code> — {d}</span>)}.</p>
      {note && <div className="alert ok" role="status">{note}</div>}
      {error && <div className="alert error" role="alert">{error}</div>}

      {form && (
        <form className="card form" onSubmit={save} noValidate>
          <h3 style={{ margin: 0 }}>{form.id ? `Изменение хранилища «${form.name}»` : "Новое хранилище"}</h3>
          <label>Название<input value={form.name} onChange={(e) => set("name", e.target.value)} placeholder="Файловый сервер №1" maxLength={120} />
            <span className="example">Пример: <code>Файловый сервер №1</code></span></label>
          <label>Тип
            <select value={form.kind} disabled={!!form.id} onChange={(e) => set("kind", e.target.value as "local" | "smb")}>
              <option value="smb">Сетевой ресурс SMB (общая папка Windows / NAS)</option>
              <option value="local">Локальный каталог на сервере приложения</option>
            </select>
            <span className="help">Приложение подключается к SMB само — монтировать ресурс в систему не нужно. Тип после создания не меняется.</span></label>
          {form.kind === "local" ? (
            <label>Каталог внутри контейнера<input value={form.local_path} onChange={(e) => set("local_path", e.target.value)} placeholder="/data/exports" />
              <span className="help">Абсолютный путь внутри контейнера приложения (лежит в разделе DATA_ROOT на сервере). Не путь Windows.</span></label>
          ) : (
            <fieldset className="group"><legend>Сетевой ресурс SMB</legend>
              <div className="cols">
                <label>Сервер (имя или IP)<input value={form.smb_server} onChange={(e) => set("smb_server", e.target.value)} placeholder="files.corp.local" />
                  <span className="help">Без схемы (smb://) и слэшей.</span></label>
                <label>Общий ресурс (имя шары)<input value={form.smb_share} onChange={(e) => set("smb_share", e.target.value)} placeholder="meetings" />
                  <span className="help">Итоговый адрес: <code>\\{form.smb_server || "сервер"}\{form.smb_share || "ресурс"}{form.smb_base_path ? `\\${form.smb_base_path.replace(/[\\/]+/g, "\\")}` : ""}</code></span></label>
              </div>
              <label>Подкаталог на ресурсе <span className="muted small">(необязательно)</span><input value={form.smb_base_path} onChange={(e) => set("smb_base_path", e.target.value)} placeholder="peregovorka" />
                <span className="help">Подпапки Audio, Protocols и другие создаются внутри него.</span></label>
              <div className="cols">
                <label>Домен <span className="muted small">(необязательно)</span><input value={form.smb_domain} onChange={(e) => set("smb_domain", e.target.value)} placeholder="CORP" /></label>
                <label>Учётная запись с правом записи<input value={form.smb_username} onChange={(e) => set("smb_username", e.target.value)} placeholder="svc-peregovorka" autoComplete="off" /></label>
              </div>
              <label>Пароль учётной записи {form.id && <span className="muted small">— {form.secret_set ? "задан (пусто — не менять)" : "не задан"}</span>}
                <input type="password" value={form.secret} onChange={(e) => set("secret", e.target.value)} autoComplete="new-password" placeholder={form.secret_set ? "••••••••" : ""} />
                <span className="help">Хранится в зашифрованном виде и после сохранения не показывается.</span></label>
            </fieldset>
          )}
          <div className="row form-actions"><button className="btn primary" disabled={busy}>{busy ? "Сохранение…" : "Сохранить"}</button><button type="button" className="btn ghost" onClick={() => setForm(null)}>Отмена</button></div>
        </form>
      )}

      {!items.length && !form && <div className="card"><p className="muted">Хранилищ пока нет. Добавьте файловый сервер — после этого его можно выбрать для записей, протоколов и вложений чата.</p></div>}
      <div className="table-scroll">{items.length > 0 && (
        <table className="table">
          <thead><tr><th>Название</th><th>Адрес</th><th>Используется для</th><th /></tr></thead>
          <tbody>
            {items.map((p) => {
              const t = tests[p.id];
              return (
                <tr key={p.id}>
                  <td>{p.name}<div className="muted small">{p.kind === "smb" ? `SMB${p.config.smb_username ? ` · ${p.config.smb_domain ? `${p.config.smb_domain}\\` : ""}${p.config.smb_username}` : ""}` : "локальный каталог"}</div></td>
                  <td className="small"><code>{p.address}</code></td>
                  <td className="small">{p.used_by.length ? p.used_by.join(", ") : <span className="muted">пока никем</span>}
                    {t && t !== "run" && <div className={t.ok ? "ok-text" : "err"} role="status">{t.ok ? "✓ " : "✗ "}{t.message}</div>}</td>
                  <td className="actions">
                    <button className="btn ghost" disabled={t === "run"} onClick={() => void test(p)} title="Пробная запись и создание подпапок">{t === "run" ? "Проверка…" : "Проверить"}</button>
                    <button className="btn ghost" onClick={() => { setForm(toForm(p)); setError(""); setNote(""); }}>Изменить</button>
                    <button className="btn ghost danger" onClick={() => void remove(p)}>Удалить</button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}</div>
      {onOpen && items.length > 0 && <p className="muted small">Выбрать хранилище: «Хранилище протоколов», «Хранилище записей», «Вложения чата», «Хранение журнала».</p>}
    </section>
  );
}
