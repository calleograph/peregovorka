import { useCallback, useEffect, useState } from "react";
import { api, type ApiError, type JournalRow, type OutlookCheck, type OutlookConference } from "../../api";
import SettingsForm from "./SettingsForm";
import { outlookFields } from "./fields";
import { fmtDate } from "./common";

const mark = (ok: boolean | null) => (ok === true ? "✓" : ok === false ? "✗" : "•");
const STATE: Record<string, string> = { waiting: "ждёт начала", open: "вход открыт", live: "идёт", ended: "завершена", cancelled: "отменена" };

/**
 * Интеграции → Microsoft Outlook: состояние и проверки, пакет надстройки для этого сервера, настройки, серверная сверка календаря, инструкции администратору Exchange,
 * история созданных конференций и журнал ошибок надстройки.
 */
export default function OutlookAdmin() {
  const [checks, setChecks] = useState<OutlookCheck[] | null>(null);
  const [checking, setChecking] = useState(false);
  const [err, setErr] = useState("");
  const [rows, setRows] = useState<OutlookConference[] | null>(null);
  const [log, setLog] = useState<JournalRow[] | null>(null);
  const [mailbox, setMailbox] = useState("");
  const [test, setTest] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState("");
  const [note, setNote] = useState("");

  const check = useCallback(async () => {
    setChecking(true); setErr("");
    try { setChecks((await api.admin.outlookStatus()).checks); } catch (e) { setErr((e as ApiError).message); }
    setChecking(false);
  }, []);
  const loadLists = useCallback(async () => {
    try { setRows((await api.admin.outlookMeetings()).items); } catch (e) { setErr((e as ApiError).message); }
    try { setLog((await api.admin.journal({ filters: [{ field: "category", op: "eq", value: "outlook" }] }, undefined, 30)).items); } catch { setLog([]); }
  }, []);
  useEffect(() => { void check(); void loadLists(); }, [check, loadLists]);

  const syncTest = async () => {
    setBusy("test"); setTest(null);
    try { const r = await api.admin.outlookSyncTest(mailbox.trim()); setTest({ ok: r.ok, text: `${r.message} (${r.ms} мс)` }); } catch (e) { setTest({ ok: false, text: (e as ApiError).message }); }
    setBusy("");
  };
  const syncRun = async () => {
    setBusy("run"); setNote("");
    try { const r = await api.admin.outlookSyncRun(); setNote(`Проверено: ${r.checked}; отменено: ${r.cancelled}; перенесено/обновлено: ${r.rescheduled}; ошибок: ${r.error}.`); void loadLists(); void check(); }
    catch (e) { setNote((e as ApiError).message); }
    setBusy("");
  };

  return (
    <section>
      <h2>Microsoft Outlook</h2>
      <p className="muted">Кнопка «Добавить конференцию» в окне встречи Outlook создаёт связанную комнату Peregovorka и вставляет ссылку в приглашение; вход открывается за заданное время до начала.
        Надстройка использует веб-компоненты и API этого сервера: администратор один раз устанавливает готовый манифест, дальше всё обновляется вместе с Peregovorka.</p>
      {err && <div className="alert error" role="alert">{err}</div>}

      <div className="card" aria-label="Состояние интеграции">
        <div className="row"><h3 style={{ margin: 0 }}>Состояние и проверки</h3><div className="spacer" />
          <button className="btn mini" disabled={checking} onClick={() => void check()}>{checking ? "Проверка…" : "Проверить заново"}</button></div>
        {!checks ? <p className="muted">Проверяем…</p> : (
          <ul className="stage-list" aria-label="Результаты проверок">
            {checks.map((c) => (
              <li key={c.key} className={c.ok === false ? "bad" : c.ok === null ? "skip" : "ok"}>
                <span className="stage-mark" aria-hidden>{mark(c.ok)}</span><span><b>{c.title}.</b> {c.message}</span></li>))}
          </ul>)}
      </div>

      <div className="card" aria-label="Пакет надстройки">
        <h3 style={{ margin: 0 }}>Надстройка для этого сервера</h3>
        <p className="muted">Пакет содержит манифест с адресами вашего сервера и постоянным идентификатором надстройки (он не меняется при повторном скачивании), сценарии <code>Install-OutlookAddin.ps1</code> и
          <code> Test-OutlookAddin.ps1</code> и инструкцию по установке, обновлению и удалению. Паролей и ключей в пакете нет.</p>
        <div className="row">
          <a className="btn primary" href="/api/v1/admin/outlook/package" download>Скачать надстройку для этого сервера</a>
          <a className="btn" href="/api/v1/outlook/manifest.xml" download>Только манифест (XML)</a></div>
        <details className="group"><summary>Как установить: Exchange 2019, Microsoft 365, проверка у одного пользователя</summary>
          <div className="help">
            <p><b>Локальный Exchange 2019 (основной сценарий).</b> Распакуйте пакет, в Exchange Management Shell выполните <code>.\Test-OutlookAddin.ps1</code>, затем
              <code> .\Install-OutlookAddin.ps1 -WhatIf</code> и <code>.\Install-OutlookAddin.ps1</code>. Либо Exchange admin center → Organization → Add-ins → «+» → Add from file. Клиенты получают надстройку не сразу
              (до нескольких часов); перезапустите Outlook. Для обновления: <code>-Replace</code>.</p>
            <p><b>Microsoft 365.</b> Центр администрирования → Settings → Integrated apps → Upload custom apps → Office Add-in → манифест → пользователи.</p>
            <p><b>Один пользователь.</b> Outlook в браузере → Get Add-ins → My add-ins → Custom add-ins → Add from file. Так кнопка появится на ленте, но не заменит переключатель Teams/Skype — это умеют только надстройки, установленные администратором.</p>
            <p>Клиентам Outlook нужен доступ к <code>{checks ? "адресу сервера" : "серверу"}</code> по HTTPS с доверенным сертификатом и (для классической загрузки Office.js) к <code>appsforoffice.microsoft.com</code>.</p>
          </div>
        </details>
        <details className="group"><summary>Матрица совместимости (по документации Microsoft; в настоящем Outlook и Exchange не проверялась)</summary>
          <div className="table-wrap"><table className="table compact">
            <thead><tr><th>Среда</th><th>Кнопка и вставка ссылки</th><th>Передача при отправке</th><th>Примечание</th></tr></thead>
            <tbody>
              <tr><td>Exchange 2019 + классический Outlook для Windows (Microsoft 365 Apps)</td><td>Да (Mailbox 1.3)</td><td>Exchange 2019 CU12+, Outlook 2206+</td><td>Первый целевой сценарий</td></tr>
              <tr><td>Exchange 2019 + Outlook 2019/2021 (бессрочный)</td><td>Не гарантируется</td><td>Нет</td><td>Функция «поставщик онлайн-встреч» заявлена для подписки Microsoft 365</td></tr>
              <tr><td>Exchange 2019 + Outlook в браузере / новый Outlook</td><td>Проверить на вашем тенанте</td><td>Для локального Exchange не заявлено</td><td>Нужен доступ клиентов к серверу</td></tr>
              <tr><td>Exchange Online + все клиенты (Windows, Web, Mac)</td><td>Да</td><td>Да (1.12)</td><td>Мобильные клиенты не поддержаны этим манифестом</td></tr>
              <tr><td>Exchange 2016</td><td>Mailbox 1.3 доступен; функция поставщика — по клиенту</td><td>Нет</td><td>Не проверялось</td></tr>
            </tbody></table></div>
        </details>
      </div>

      <SettingsForm key="outlook" group="outlook" title="Настройки интеграции" fields={outlookFields} onSaved={() => void check()}
        intro="Включите интеграцию, задайте публичный HTTPS-адрес и при необходимости группы, которым разрешено создавать конференции. Все настройки хранятся в базе и входят в резервную копию конфигурации." />

      <div className="card" aria-label="Проверка сверки календаря">
        <h3 style={{ margin: 0 }}>Проверка доступа к календарю</h3>
        <p className="muted">Проверяет вход с сохранёнными настройками сверки и чтение календаря указанного ящика. Сначала сохраните настройки выше.</p>
        <div className="row">
          <input value={mailbox} onChange={(e) => setMailbox(e.target.value)} placeholder="организатор@example.org" aria-label="Почтовый ящик для проверки" style={{ maxWidth: 320 }} inputMode="email" />
          <button className="btn" disabled={busy !== "" || !mailbox.includes("@")} onClick={() => void syncTest()}>{busy === "test" ? "Проверяем…" : "Проверить доступ"}</button>
          <button className="btn" disabled={busy !== ""} onClick={() => void syncRun()} title="Не дожидаясь интервала">{busy === "run" ? "Сверяем…" : "Сверить сейчас"}</button></div>
        {test && <div className={`alert ${test.ok ? "ok" : "error"}`} role="status">{test.ok ? "✓ " : "✗ "}{test.text}</div>}
        {note && <div className="alert info" role="status">{note}</div>}
      </div>

      <div className="card" aria-label="История конференций">
        <div className="row"><h3 style={{ margin: 0 }}>Созданные конференции</h3><div className="spacer" /><button className="btn mini" onClick={() => void loadLists()}>Обновить</button></div>
        {rows && !rows.length && <p className="muted">Пока нет ни одной конференции, созданной из Outlook.</p>}
        {rows && rows.length > 0 && (
          <div className="table-wrap"><table className="table compact">
            <thead><tr><th>Встреча</th><th>Организатор</th><th>Время</th><th>Состояние</th><th>Сверка</th></tr></thead>
            <tbody>{rows.map((r) => (
              <tr key={r.id}><td><a href={r.url} target="_blank" rel="noreferrer">{r.name}</a>{r.recurring ? <span className="badge"> серия</span> : null}</td>
                <td>{r.organizer ?? "—"}{r.organizer_email ? <div className="muted small">{r.organizer_email}</div> : null}</td>
                <td>{fmtDate(r.start)} — {fmtDate(r.end)}</td><td>{STATE[r.state] ?? r.state}</td>
                <td>{!r.tracked ? <span className="muted small">не отслеживается</span> : r.sync_state === "error" ? <span className="bad-text small">{r.sync_error}</span> : r.synced_at ? <span className="small">{fmtDate(r.synced_at)}</span> : <span className="muted small">ещё не сверялась</span>}</td></tr>))}</tbody></table></div>)}
      </div>

      <div className="card" aria-label="Журнал ошибок надстройки">
        <div className="row"><h3 style={{ margin: 0 }}>Журнал надстройки и сверки</h3><div className="spacer" /><button className="btn mini" onClick={() => void loadLists()}>Обновить</button></div>
        <p className="muted small">Сообщения, которые пользователь видел в уведомлении Outlook, и итоги серверной сверки. Полный журнал — «Журналы → Журнал событий», категория outlook.</p>
        {log && !log.length && <p className="muted">Ошибок нет.</p>}
        {log && log.length > 0 && (
          <ul className="stage-list">{log.map((l) => (
            <li key={l.id} className={l.level === "warn" || l.level === "error" ? "bad" : "ok"}><span className="stage-mark" aria-hidden>{l.level === "info" ? "•" : "!"}</span>
              <span>{fmtDate(l.at)} · {l.event}{l.user ? ` · ${l.user}` : ""}{l.room ? ` · ${l.room}` : ""}{l.message ? ` — ${l.message}` : ""}</span></li>))}</ul>)}
      </div>
    </section>
  );
}
