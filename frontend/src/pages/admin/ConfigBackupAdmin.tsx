import { useEffect, useRef, useState } from "react";
import { api, type ApiError, type ConfigHeader, type ConfigInfo, type ConfigPreview, type ConfigReport } from "../../api";
import { archivePasswordComplete, backupFileName, canApply, groupPassword, groupWarnings, statusTitle, statusTone } from "../../configBackupMath";
import { copyText } from "../../util";

/** «Сервер → Резервная копия конфигурации»: шифрованный экспорт и импорт настроек. Архив нигде не сохраняется на сервере; пароль архива показывается один раз. */
export default function ConfigBackupAdmin() {
  return (
    <section aria-label="Резервная копия конфигурации">
      <h2>Резервная копия конфигурации</h2>
      <p className="muted">Все настройки, подключения, сертификаты и секреты — одним зашифрованным файлом, чтобы восстановить работающую систему на новом сервере. Данные встреч (записи, стенограммы, протоколы, история, чат), журналы, сессии и фото не входят.</p>
      <ExportPanel />
      <ImportPanel />
    </section>
  );
}

function ExportPanel() {
  const [info, setInfo] = useState<ConfigInfo | null>(null);
  const [open, setOpen] = useState(false);
  const [pw, setPw] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [secret, setSecret] = useState<{ password: string; filename: string } | null>(null);
  const [saved, setSaved] = useState(false);
  const [copied, setCopied] = useState(false);
  useEffect(() => { api.admin.configInfo().then(setInfo).catch((e) => setErr((e as ApiError).message)); }, []);

  const run = async () => {
    setBusy(true); setErr("");
    try {
      const r = await api.admin.configExport(pw);
      const url = URL.createObjectURL(r.blob);
      const a = document.createElement("a");
      a.href = url; a.download = r.filename || backupFileName(); document.body.appendChild(a); a.click(); a.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 10000);
      setSecret({ password: r.password, filename: r.filename }); setSaved(false); setCopied(false); setPw(""); setOpen(false);
    } catch (e) { setErr((e as ApiError).message); }
    setBusy(false);
  };
  const done = () => { setSecret(null); setSaved(false); };

  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <h3 style={{ marginTop: 0 }}>Скачать конфигурацию</h3>
      {info && (
        <details>
          <summary>Что войдёт в архив и что нет</summary>
          <p className="small"><b>Войдёт:</b> {info.included.filter((i) => i.count > 0).map((i) => `${i.title} (${i.count})`).join(", ") || "—"}; настройки: {info.settings_groups.map((g) => g.title).join(", ")}; оформление (логотипы).</p>
          {info.hashed.length > 0 && <div className="alert warn small"><b>Хранятся только в виде хэша:</b><ul>{info.hashed.map((h) => <li key={h.what}>{h.what} ({h.count}) — {h.how}.</li>)}</ul></div>}
          <p className="small"><b>Не войдёт:</b> {info.excluded.map((e) => e.reason).filter((v, i, a) => a.indexOf(v) === i).join("; ")}.</p>
          <p className="small muted">{info.environment}</p>
        </details>
      )}
      {err && <div className="alert error" role="alert">{err}</div>}
      {!open && !secret && <button className="btn primary" onClick={() => { setOpen(true); setErr(""); }}>Скачать конфигурацию</button>}
      {open && (
        <form className="form" onSubmit={(e) => { e.preventDefault(); if (pw && !busy) void run(); }}>
          <p className="small">Архив содержит секреты, поэтому нужен ваш пароль. Файл будет зашифрован случайным паролем из 20 символов — он покажется один раз после скачивания; на сервере ни файл, ни пароль не сохраняются.</p>
          <label>Ваш пароль администратора<input type="password" value={pw} onChange={(e) => setPw(e.target.value)} autoComplete="current-password" autoFocus /></label>
          <div className="row"><button className="btn primary" disabled={!pw || busy}>{busy ? "Готовим архив…" : "Скачать"}</button><button type="button" className="btn" onClick={() => { setOpen(false); setPw(""); }}>Отмена</button></div>
        </form>
      )}
      {secret && (
        <div className="alert warn" role="alert" aria-live="assertive">
          <b>Пароль архива — запишите его сейчас.</b> Он показывается один раз и нигде не сохраняется; без него архив не открыть, восстановить пароль нельзя. Храните отдельно от файла.
          <div className="row" style={{ marginTop: 8, alignItems: "center" }}>
            <code style={{ fontSize: 20, letterSpacing: 1, userSelect: "all" }} data-testid="archive-password">{groupPassword(secret.password)}</code>
            <button className="btn" onClick={async () => { setCopied(await copyText(secret.password)); }}>{copied ? "Скопировано ✓" : "Копировать"}</button>
          </div>
          <div className="muted small" style={{ marginTop: 4 }}>Файл «{secret.filename}» сохранён в папку загрузок браузера.</div>
          <label className="check" style={{ marginTop: 8 }}><input type="checkbox" checked={saved} onChange={(e) => setSaved(e.target.checked)} /> Я сохранил пароль</label>
          <button className="btn" disabled={!saved} onClick={done}>Закрыть и забыть пароль</button>
        </div>
      )}
    </div>
  );
}

function ImportPanel() {
  const file = useRef<File | null>(null);
  const [name, setName] = useState("");
  const [head, setHead] = useState<ConfigHeader | null>(null);
  const [apw, setApw] = useState("");
  const [prev, setPrev] = useState<ConfigPreview | null>(null);
  const [ack, setAck] = useState(false);
  const [adminPw, setAdminPw] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [report, setReport] = useState<ConfigReport | null>(null);

  const reset = () => { file.current = null; setName(""); setHead(null); setApw(""); setPrev(null); setAck(false); setAdminPw(""); setErr(""); setReport(null); };
  const pick = async (f: File | undefined) => {
    reset();
    if (!f) return;
    file.current = f; setName(f.name);
    try { setHead(await api.admin.configInspect(f)); } catch (e) { setErr((e as ApiError).message); }
  };
  const check = async () => {
    if (!file.current) return;
    setBusy(true); setErr("");
    try { setPrev(await api.admin.configPreview(file.current, apw)); setAck(false); } catch (e) { setErr((e as ApiError).message); setPrev(null); }
    setBusy(false);
  };
  const apply = async () => {
    if (!file.current || !prev) return;
    if (!window.confirm("Применить конфигурацию из архива? Настройки на этом сервере, совпадающие с архивными, будут заменены. Изменения применяются одной операцией: при ошибке всё откатывается.")) return;
    setBusy(true); setErr("");
    try { setReport(await api.admin.configApply(file.current, apw, adminPw)); setPrev(null); setAdminPw(""); setApw(""); } catch (e) { setErr((e as ApiError).message); }
    setBusy(false);
  };

  return (
    <div className="card">
      <h3 style={{ marginTop: 0 }}>Восстановить конфигурацию</h3>
      <p className="small muted">Обычно — на новом чистом сервере под локальным администратором: пользователи и ваш локальный вход не меняются. Сначала архив проверяется и показывается, что изменится; ничего не применяется без вашего подтверждения.</p>
      {!report && (
        <>
          <label>Файл архива (.pgcfg)<input type="file" accept=".pgcfg,application/octet-stream" onChange={(e) => void pick(e.target.files?.[0])} /></label>
          {head && !err && <div className="small muted">Файл «{name}»: создан {head.created_at ? new Date(head.created_at).toLocaleString("ru-RU") : "—"} на версии {head.app_version ?? "—"}, {Math.ceil(head.size_bytes / 1024)} КБ.</div>}
          {head && (
            <form className="form" onSubmit={(e) => { e.preventDefault(); if (archivePasswordComplete(apw) && !busy) void check(); }}>
              <label>Пароль архива (20 символов, был показан при скачивании)<input value={apw} onChange={(e) => setApw(e.target.value)} autoComplete="off" spellCheck={false} placeholder="xxxx xxxx xxxx xxxx xxxx" style={{ fontFamily: "var(--mono)" }} /></label>
              <button className="btn" disabled={!archivePasswordComplete(apw) || busy}>{busy && !prev ? "Проверяем…" : "Проверить архив"}</button>
            </form>
          )}
        </>
      )}
      {err && <div className="alert error" role="alert" style={{ whiteSpace: "pre-line" }}>{err}</div>}

      {prev && (
        <div style={{ marginTop: 12 }}>
          <h4>Что будет восстановлено</h4>
          <p className="small">Архив от {prev.manifest.created_at ? new Date(prev.manifest.created_at).toLocaleString("ru-RU") : "—"}, версия {prev.manifest.app_version ?? "—"}{prev.manifest.source_url ? `, сервер ${prev.manifest.source_url}` : ""}.</p>
          {prev.compat.notes.map((n) => <div key={n} className="alert info small">{n}</div>)}
          <ul className="small">
            {prev.components.tables.filter((t) => t.count > 0).map((t) => <li key={t.name}>{t.title}: {t.count}</li>)}
            {prev.components.settings.length > 0 && <li>Настройки: {prev.components.settings.map((s) => s.title).join(", ")}</li>}
            {prev.components.files.length > 0 && <li>Оформление: {prev.components.files.map((f) => f.title).join(", ")}</li>}
          </ul>
          {prev.hashed.length > 0 && <div className="alert warn small"><b>Хранятся только как хэш (сам секрет в системе отсутствует):</b><ul>{prev.hashed.map((h) => <li key={h.what}>{h.what} ({h.count}) — {h.how}.</li>)}</ul></div>}
          {prev.notes.map((n) => <div key={n} className="alert info small">{n}</div>)}
          {prev.manifest_warnings.map((n) => <div key={n} className="alert warn small">{n}</div>)}
          {prev.conflicts.length > 0 && <div className="alert error small"><b>Конфликты:</b><ul>{prev.conflicts.map((c) => <li key={c}>{c}</li>)}</ul></div>}
          {Object.keys(prev.replace).length > 0 && <div className="alert warn small"><b>На этом сервере уже есть и будет заменено:</b> {Object.entries(prev.replace).map(([k, v]) => `${k} (${v})`).join(", ")}.</div>}
          {prev.warnings.length > 0 && (
            <div className="alert warn small">
              <b>Параметры, привязанные к старому серверу — проверьте после восстановления:</b>
              {groupWarnings(prev.warnings).map((g) => (
                <details key={g.kind}><summary>{g.title} ({g.items.length})</summary>
                  <ul>{g.items.map((w, i) => <li key={i}>{w.where}{w.value ? <> — <code>{w.value}</code></> : null}{w.note ? <span className="muted"> {w.note}</span> : null}</li>)}</ul></details>
              ))}
            </div>
          )}
          <details><summary className="small">Что не переносится</summary><ul className="small">{[...new Set(Object.values(prev.excluded))].map((r) => <li key={r}>{r}</li>)}</ul></details>
          <div className="form" style={{ marginTop: 10 }}>
            {prev.needs_ack && <label className="check"><input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} /> Я прочитал предупреждения и понимаю, что будет заменено</label>}
            <label>Ваш пароль администратора<input type="password" value={adminPw} onChange={(e) => setAdminPw(e.target.value)} autoComplete="current-password" /></label>
            <div className="row"><button className="btn primary" disabled={busy || !canApply(prev, ack, adminPw) || prev.conflicts.some((c) => c.includes("невозможен"))} onClick={() => void apply()}>{busy ? "Применяем…" : "Применить конфигурацию"}</button>
              <button className="btn" onClick={reset}>Отмена</button></div>
          </div>
        </div>
      )}

      {report && (
        <div style={{ marginTop: 12 }} role="status">
          <div className={`alert ${report.summary.failed || report.summary.needs_attention ? "warn" : "ok"}`}>
            <b>Конфигурация применена.</b> Работает: {report.summary.restored}, требует внимания: {report.summary.needs_attention}, ошибок проверки: {report.summary.failed}.
          </div>
          {[...report.files_problems, ...report.refresh_problems].map((p) => <div key={p} className="alert warn small">{p}</div>)}
          {report.applied.replaced.length > 0 && <div className="alert info small"><b>Заменено:</b><ul>{report.applied.replaced.map((r) => <li key={r}>{r}</li>)}</ul></div>}
          <table className="table compact"><thead><tr><th>Раздел</th><th>Объект</th><th>Состояние</th><th>Что делать</th></tr></thead><tbody>
            {report.checks.map((c, i) => <tr key={i}><td>{c.component}</td><td>{c.item}</td><td><span className={`badge ${statusTone(c.status)}`}>{statusTitle(c.status)}</span></td><td className="small">{c.message}</td></tr>)}
            {!report.checks.length && <tr><td colSpan={4} className="muted">Подключений для проверки в архиве не было.</td></tr>}
          </tbody></table>
          <p className="small muted">Подключения, помеченные «требует внимания», перенесены, но не отвечают с нового сервера — обычно меняется адрес или сеть: откройте раздел и поправьте. Идентификаторы пользователей каталога сопоставятся при первом входе.</p>
          <button className="btn" onClick={reset}>Готово</button>
        </div>
      )}
    </div>
  );
}
