import { useCallback, useEffect, useState } from "react";
import { api, type ApiError, type JournalRow, type OutlookCenterInfo, type OutlookCheck, type OutlookConference } from "../../api";
import SettingsForm from "./SettingsForm";
import { outlookFields } from "./fields";
import { fmtDate } from "./common";
import InfoTip from "../../components/InfoTip";

const mark = (ok: boolean | null) => (ok === true ? "✓" : ok === false ? "✗" : "•");
const STATE: Record<string, string> = { waiting: "ждёт начала", open: "вход открыт", live: "идёт", ended: "завершена", cancelled: "отменена" };

/**
 * Интеграции → Microsoft Outlook: состояние и проверки, пакет надстройки для этого сервера, настройки, серверная сверка календаря, инструкции администратору Exchange,
 * история созданных конференций и журнал ошибок надстройки.
 */
const EVENT_LABEL: Record<string, string> = {
  addin_ok: "конференция добавлена", addin_error: "ошибка надстройки", addin_legacy: "кнопка старого манифеста (обновите манифест)", addin_login_failed: "вход в панели не выполнен",
  addin_boot: "панель открыта (среда клиента)", addin_ready: "панель запущена Office", addin_init_timeout: "Office не запустил панель (причина в сообщении)", addin_init_failed: "панель не запустилась",
};

export default function OutlookAdmin() {
  const [checks, setChecks] = useState<OutlookCheck[] | null>(null);
  const [compat, setCompat] = useState<OutlookCenterInfo["compat"] | null>(null);
  const [checking, setChecking] = useState(false);
  const [err, setErr] = useState("");
  const [rows, setRows] = useState<OutlookConference[] | null>(null);
  const [log, setLog] = useState<JournalRow[] | null>(null);
  const [mailbox, setMailbox] = useState("");
  const [test, setTest] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState("");
  const [note, setNote] = useState("");
  const [clientNote, setClientNote] = useState("");

  const check = useCallback(async () => {
    setChecking(true); setErr("");
    try { setChecks((await api.admin.outlookStatus()).checks); } catch (e) { setErr((e as ApiError).message); }
    setChecking(false);
  }, []);
  /** Проверка «с клиентской стороны»: браузер сам проверяет цепочку и имя сервера по системному доверию; сертификат при этом никуда не загружается и ничем не подменяется. */
  const clientCheck = async (url: string) => {
    setClientNote("");
    try { const r = await fetch(url, { cache: "no-store" }); setClientNote(r.ok ? "С этого компьютера публичный HTTPS-адрес открывается: сертификат принят браузером, API надстройки отвечает." : `С этого компьютера адрес открылся, но API ответил HTTP ${r.status}.`); }
    catch { setClientNote("С этого компьютера публичный HTTPS-адрес не открылся (сертификат не принят, адрес недоступен или блокируется). Подробнее — скрипт Test-OutlookAddin.ps1 из пакета на рабочей станции Outlook."); }
  };
  const loadLists = useCallback(async () => {
    try { setRows((await api.admin.outlookMeetings()).items); } catch (e) { setErr((e as ApiError).message); }
    try { setLog((await api.admin.journal({ filters: [{ field: "category", op: "eq", value: "outlook" }] }, undefined, 30)).items); } catch { setLog([]); }
  }, []);
  useEffect(() => { void check(); void loadLists(); api.outlookCenter().then((c) => setCompat(c.compat)).catch(() => undefined); }, [check, loadLists]);

  const [formKey, setFormKey] = useState(0);
  const [det, setDet] = useState<Awaited<ReturnType<typeof api.admin.outlookEwsDetect>> | null>(null);
  const detect = async () => {
    setBusy("detect"); setDet(null);
    try { setDet(await api.admin.outlookEwsDetect()); } catch (e) { setTest({ ok: false, text: (e as ApiError).message }); }
    setBusy("");
  };
  const applyAuth = async (m: string) => {
    try { await api.admin.saveSettings("outlook", { ews_auth: m } as never); setFormKey((k) => k + 1); setDet(null); void check(); } catch (e) { setTest({ ok: false, text: (e as ApiError).message }); }
  };
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
      <p className="muted small">Кнопка «Добавить конференцию» в окне встречи Outlook создаёт связанную комнату Peregovorka и вставляет ссылку в приглашение; вход открывается за заданное время до начала.<InfoTip>Надстройка использует веб-компоненты и API этого сервера: администратор один раз устанавливает готовый манифест, дальше всё обновляется вместе с Peregovorka.</InfoTip></p>
      {err && <div className="alert error" role="alert">{err}</div>}

      <div className="card" aria-label="Состояние интеграции">
        <div className="row"><h3 style={{ margin: 0 }}>Состояние и проверки</h3><div className="spacer" />
          <a className="btn mini" href="/outlook" target="_blank" rel="noreferrer">Публичная страница установки</a>
          <a className="btn mini" href="/api/docs" target="_blank" rel="noreferrer">Открыть документацию API</a>
          <button className="btn mini" disabled={checking} onClick={() => void check()}>{checking ? "Проверка…" : "Проверить заново"}</button></div>
        {!checks ? <p className="muted">Проверяем…</p> : (
          <ul className="stage-list" aria-label="Результаты проверок">
            {checks.map((c) => (
              <li key={c.key} className={c.ok === false ? "bad" : c.ok === null ? "skip" : "ok"}>
                <span className="stage-mark" aria-hidden>{mark(c.ok)}</span>
                <span><b>{c.title}.</b> {c.message}
                  {c.tls && (
                    <div className="muted small">Адрес: {c.tls.host}:{c.tls.port}{c.tls.issuer ? ` · издатель: ${c.tls.issuer}` : ""}{c.tls.not_after ? ` · действует до ${fmtDate(c.tls.not_after)}` : ""}{c.tls.days_left != null ? ` (осталось дней: ${c.tls.days_left})` : ""}
                      {c.tls.chain_complete === false && <> · цепочка, отдаваемая устройством, неполная</>}
                      {c.tls.fix && <div><b>Где исправлять:</b> {c.tls.fix.text}</div>}
                      {c.tls.has_fullchain && <div><a href="/api/v1/outlook/tls/fullchain.pem" download>Скачать fullchain.pem</a> (сертификат сайта + найденные промежуточные, без корня) — установите его на устройстве, где завершается HTTPS</div>}
                      {(c.ok !== true) && <> · <button className="btn mini" onClick={() => void clientCheck(c.tls!.client_check_url)}>Проверить с этого компьютера</button></>}</div>)}</span></li>))}
          </ul>)}
        {clientNote && <div className="alert info" role="status">{clientNote}</div>}
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
          {compat && (
            <>
              <p className="small">Базовые функции (Mailbox 1.3): {compat.basic_functions.join("; ")}.</p>
              <p className="small">Дополнительные (зависят от версии Mailbox API): {compat.advanced_functions.join("; ")}.</p>
              <div className="table-wrap"><table className="table compact">
                <thead><tr><th>Среда</th><th>Mailbox API</th><th>Базовые функции</th><th>Подписка на отправку</th><th>Серверная сверка</th><th>Примечание</th></tr></thead>
                <tbody>{compat.environments.map((e) => <tr key={e.id}><td>{e.title}</td><td>{e.mailbox_api}</td><td>{e.basic === null ? "Зависит от клиента" : e.basic ? "Да" : "Нет"}</td><td>{e.send_tracking ? "Да (1.12)" : "Нет"}</td><td>{e.server_sync.join(", ")}</td><td>{e.note}{!e.verified && " Не проверено в этой установке."}</td></tr>)}</tbody></table></div>
              {compat.notes.map((n) => <div key={n} className="alert info small">{n}</div>)}
            </>)}
        </details>
      </div>

      <SettingsForm key={`outlook-${formKey}`} group="outlook" title="Настройки интеграции" fields={outlookFields} onSaved={() => void check()}
        intro="Включите интеграцию, задайте публичный HTTPS-адрес и при необходимости группы, которым разрешено создавать конференции. Все настройки хранятся в базе и входят в резервную копию конфигурации." />

      <div className="card" aria-label="Проверка сверки календаря">
        <h3 style={{ margin: 0 }}>Проверка доступа к календарю</h3>
        <p className="muted">Проверяет вход с сохранёнными настройками сверки и чтение календаря указанного ящика. Сначала сохраните настройки выше.</p>
        <div className="row">
          <input value={mailbox} onChange={(e) => setMailbox(e.target.value)} placeholder="организатор@example.org" aria-label="Почтовый ящик для проверки" style={{ maxWidth: 320 }} inputMode="email" />
          <button className="btn" disabled={busy !== "" || !mailbox.includes("@")} onClick={() => void syncTest()}>{busy === "test" ? "Проверяем…" : "Проверить доступ"}</button>
          <button className="btn" disabled={busy !== ""} onClick={() => void detect()} title="Без учётных данных: что предлагают сохранённые адреса EWS">{busy === "detect" ? "Определяем…" : "Определить способы входа"}</button>
          <button className="btn" disabled={busy !== ""} onClick={() => void syncRun()} title="Не дожидаясь интервала">{busy === "run" ? "Сверяем…" : "Сверить сейчас"}</button></div>
        {det && (
          <div className="alert info" role="status">
            <b>{det.message}</b>
            <ul className="stage-list">{det.servers.map((x) => <li key={x.url} className={x.reachable ? "ok" : "bad"}><span className="stage-mark" aria-hidden>{x.reachable ? "✓" : "✗"}</span><span>{x.message}</span></li>)}</ul>
            {det.recommended && <button className="btn mini" onClick={() => void applyAuth(det.recommended!)}>Применить: {det.label}</button>}
          </div>)}
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
        <p className="muted small">Ошибки, которые пользователь видел в панели или уведомлении Outlook (этап и код в квадратных скобках, в скобках — клиент Outlook и поддерживаемые наборы API), и итоги серверной сверки.<InfoTip>Полный журнал — «Хранение и журналы → Журнал событий», категория outlook.</InfoTip></p>
        {log && !log.length && <p className="muted">Ошибок нет.</p>}
        {log && log.length > 0 && (
          <ul className="stage-list">{log.map((l) => (
            <li key={l.id} className={l.level === "warn" || l.level === "error" ? "bad" : "ok"}><span className="stage-mark" aria-hidden>{l.level === "info" ? "•" : "!"}</span>
              <span>{fmtDate(l.at)} · {EVENT_LABEL[l.event] ?? l.event}{l.user ? ` · ${l.user}` : ""}{l.room ? ` · ${l.room}` : ""}{l.message ? ` — ${l.message}` : ""}{l.client ? <span className="muted small"> ({l.client})</span> : null}</span></li>))}</ul>)}
      </div>
    </section>
  );
}
