import { useEffect, useState } from "react";
import { api, type ApiError, type AutoBackupRun, type AutoBackupStatus, type StorageProfile } from "../../api";
import { bytes, copyText, fmt } from "../../util";

const STATUS: Record<string, string> = { ok: "успешно", failed: "ошибка", running: "идёт" };

/** «Автоматическая копия настроек»: расписание, выбор сетевого хранилища, ключ шифрования (показывается один раз), проверка и скачивание копий. */
export default function AutoBackupPanel() {
  const [st, setSt] = useState<AutoBackupStatus | null>(null);
  const [profiles, setProfiles] = useState<StorageProfile[]>([]);
  const [form, setForm] = useState<AutoBackupStatus["settings"] | null>(null);
  const [files, setFiles] = useState<{ name: string; size_bytes: number | null }[] | null>(null);
  const [err, setErr] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState("");
  const [pw, setPw] = useState("");
  const [ask, setAsk] = useState<{ what: "key" } | { what: "download"; name: string } | null>(null);
  const [key, setKey] = useState("");

  const load = async () => {
    try { const s = await api.admin.autoBackup(); setSt(s); setForm(s.settings); } catch (e) { setErr((e as ApiError).message); }
  };
  useEffect(() => { void load(); api.admin.storages().then((r) => setProfiles(r.items)).catch(() => undefined); }, []);

  const act = async (id: string, fn: () => Promise<unknown>, ok?: string) => {
    setBusy(id); setErr(""); setNote("");
    try { await fn(); if (ok) setNote(ok); } catch (e) { setErr((e as ApiError).message); }
    await load(); setBusy("");
  };
  const save = () => act("save", async () => {
    const { archive_key_set: _k, ...body } = form!;
    setSt(await api.admin.autoBackupSave(body));
  }, "Сохранено");
  const confirm = () => act("ask", async () => {
    if (ask?.what === "key") { setKey((await api.admin.autoBackupKey(pw)).key); }
    else if (ask) {
      const blob = await api.admin.autoBackupDownload(ask.name, pw);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a"); a.href = url; a.download = ask.name; document.body.appendChild(a); a.click(); a.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 10000);
    }
    setAsk(null); setPw("");
  });
  if (!form || !st) return <div className="card" style={{ marginBottom: 16 }}>{err ? <div className="alert error">{err}</div> : "Загрузка…"}</div>;
  const set = <K extends keyof typeof form>(k: K, v: (typeof form)[K]) => setForm({ ...form, [k]: v });
  const row = (r: AutoBackupRun) => (
    <tr key={r.id}><td>{fmt(r.started_at)}</td><td>{r.kind === "verify" ? "проверка" : "копия"} · {r.trigger === "auto" ? "по расписанию" : r.actor ?? "вручную"}</td>
      <td><span className={`badge ${r.status === "failed" ? "warn" : ""}`}>{STATUS[r.status] ?? r.status}</span></td>
      <td className="small">{r.file_name ?? ""}{r.size_bytes ? ` · ${bytes(r.size_bytes)}` : ""}</td><td className="small">{r.message ?? ""}</td></tr>);

  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <h3 style={{ marginTop: 0 }}>Автоматическая копия на сетевое хранилище</h3>
      <p className="small muted">Копия настроек шифруется на сервере до отправки: на сетевой папке лежит только шифрованный файл. Пароль шифрования создаётся здесь, показывается один раз и хранится на сервере в зашифрованном виде — запишите его отдельно: без него копию не открыть. Записи и документы не копируются, только настройки.</p>
      {st.stale && <div className="alert warn" role="alert">Давно нет успешной копии{st.last_ok ? ` (последняя: ${fmt(st.last_ok.finished_at ?? st.last_ok.started_at)})` : ""}. Проверьте хранилище и историю ниже.</div>}
      {err && <div className="alert error" role="alert">{err}</div>}
      {note && <div className="alert ok">{note}</div>}
      <form className="form" onSubmit={(e) => { e.preventDefault(); void save(); }}>
        <label className="row"><input type="checkbox" checked={form.enabled} onChange={(e) => set("enabled", e.target.checked)} /> Включить копирование по расписанию</label>
        <label>Хранилище (SMB или папка, подпапка ConfigBackups)
          <select value={form.profile_id} onChange={(e) => set("profile_id", e.target.value)}><option value="">— не выбрано —</option>{profiles.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
        <div className="row" style={{ gap: 12, flexWrap: "wrap" }}>
          <label>Время<input type="time" value={form.time} onChange={(e) => set("time", e.target.value)} /></label>
          <label>Раз в, дней<input type="number" min={1} max={30} value={form.every_days} onChange={(e) => set("every_days", Number(e.target.value))} /></label>
          <label>Хранить копий<input type="number" min={1} max={365} value={form.keep} onChange={(e) => set("keep", Number(e.target.value))} /></label>
          <label>Предупредить через, ч без копии<input type="number" min={1} max={720} value={form.stale_hours} onChange={(e) => set("stale_hours", Number(e.target.value))} /></label>
        </div>
        <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
          <button className="btn primary" disabled={!!busy}>Сохранить</button>
          <button type="button" className="btn" disabled={!!busy || !st.settings.profile_id} onClick={() => act("test", async () => setNote((await api.admin.autoBackupTest()).message))}>Проверить подключение</button>
          <button type="button" className="btn" disabled={!!busy} onClick={() => { setAsk({ what: "key" }); setPw(""); }}>{st.settings.archive_key_set ? "Создать новый ключ" : "Создать ключ шифрования"}</button>
          <button type="button" className="btn" disabled={!!busy || !st.settings.archive_key_set || !st.settings.profile_id} onClick={() => act("run", () => api.admin.autoBackupRun(), "Задание выполнено, результат — в истории")}>{busy === "run" ? "Копируем…" : "Создать копию сейчас"}</button>
          <button type="button" className="btn" disabled={!!busy || !st.last_ok} onClick={() => act("verify", () => api.admin.autoBackupVerify(), "Проверка выполнена, результат — в истории")}>Проверить последнюю копию</button>
          <button type="button" className="btn" disabled={!!busy || !st.settings.profile_id} onClick={() => act("files", async () => setFiles(await api.admin.autoBackupFiles()))}>Список копий</button>
        </div>
      </form>
      {key && (
        <div className="alert warn" role="alert" aria-live="assertive" style={{ marginTop: 12 }}>
          <b>Ключ шифрования копий — запишите его сейчас.</b> Он показывается один раз. Предыдущие копии открываются прежним ключом.
          <div className="row" style={{ marginTop: 8, alignItems: "center" }}><code style={{ fontSize: 18, userSelect: "all" }}>{key}</code>
            <button type="button" className="btn" onClick={() => void copyText(key)}>Копировать</button><button type="button" className="btn" onClick={() => setKey("")}>Я записал(а)</button></div>
        </div>)}
      {ask && (
        <form className="form" style={{ marginTop: 12 }} onSubmit={(e) => { e.preventDefault(); if (pw && !busy) void confirm(); }}>
          <label>Ваш пароль администратора<input type="password" value={pw} onChange={(e) => setPw(e.target.value)} autoComplete="current-password" autoFocus /></label>
          <div className="row"><button className="btn primary" disabled={!pw || !!busy}>{ask.what === "key" ? "Создать ключ" : "Скачать"}</button><button type="button" className="btn" onClick={() => setAsk(null)}>Отмена</button></div>
        </form>)}
      {files && (
        <div className="table-scroll" style={{ marginTop: 12 }}><table className="table"><thead><tr><th>Файл</th><th>Размер</th><th /></tr></thead>
          <tbody>{files.length === 0 ? <tr><td colSpan={3} className="muted">Копий в хранилище пока нет.</td></tr> : files.map((f) => (
            <tr key={f.name}><td>{f.name}</td><td>{bytes(f.size_bytes)}</td><td><button className="btn" onClick={() => { setAsk({ what: "download", name: f.name }); setPw(""); }}>Скачать</button></td></tr>))}</tbody></table>
          <p className="small muted">Чтобы восстановить настройки: скачайте копию и загрузите её в блоке «Импорт» ниже, указав ключ шифрования в поле пароля архива.</p></div>)}
      <h4>История заданий</h4>
      <div className="table-scroll"><table className="table"><thead><tr><th>Начато</th><th>Что</th><th>Итог</th><th>Файл</th><th>Сообщение</th></tr></thead>
        <tbody>{st.runs.length ? st.runs.map(row) : <tr><td colSpan={5} className="muted">Заданий пока не было.</td></tr>}</tbody></table></div>
    </div>
  );
}
