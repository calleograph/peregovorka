import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type ApiError, type OutlookCenterInfo } from "../api";
import { Brand } from "../components/Brand";
import { runClientChecks, type ClientCheck } from "../outlookCenter";

const mark = (ok: boolean | null) => (ok === true ? "✓" : ok === false ? "✗" : "•");
const yn = (v: boolean | null | undefined) => (v === true ? "Да" : v === false ? "Нет" : "Зависит от клиента");
const when = (iso?: string | null) => (iso ? new Date(iso).toLocaleDateString("ru-RU") : "—");

/**
 * Публичный центр установки надстройки Outlook (`/outlook`): без входа. Для пользователя — манифест и инструкции по клиентам; для администратора Exchange — пакет, сценарии PowerShell,
 * инструкции; сведения о публичном TLS и сертификатах; проверка доступности из браузера этого компьютера. Секретов и внутренних адресов здесь нет.
 */
export default function OutlookCenter() {
  const [info, setInfo] = useState<OutlookCenterInfo | null>(null);
  const [err, setErr] = useState("");
  const [checks, setChecks] = useState<ClientCheck[] | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { api.outlookCenter().then(setInfo).catch((e) => setErr((e as ApiError).message)); }, []);
  const run = async () => {
    if (!info?.enabled) return;
    setBusy(true);
    try { setChecks(await runClientChecks(window.location.origin, info.addin_id)); } finally { setBusy(false); }
  };
  const tls = info?.tls;
  return (
    <div className="shell guest-shell">
      <header className="topbar"><span className="brand"><Brand /></span></header>
      <main>
        <section className="card oc" style={{ maxWidth: 960, margin: "0 auto" }}>
          <h1>Надстройка Peregovorka для Outlook</h1>
          {err && <div className="alert error" role="alert">{err}</div>}
          {!info && !err && <p className="muted">Загрузка…</p>}
          {info && !info.enabled && <div className="alert" role="status">{info.reason} Обратитесь к администратору Peregovorka.</div>}
          {info?.enabled && (
            <>
              <p className="muted">Кнопка «Добавить конференцию» в окне встречи Outlook создаёт комнату Peregovorka и вставляет ссылку в приглашение. Страница доступна без входа; пароли и ключи здесь не показываются.</p>

              <h2>Для пользователя</h2>
              <div className="row"><a className="btn primary" href={info.manifest_url} download>Скачать манифест (XML)</a></div>
              <div className="alert info small">Установка надстройки управляется администратором Exchange. Если пункта «Добавить из файла» нет или установка запрещена, обратитесь к администратору или воспользуйтесь корпоративным развёртыванием: вам ничего скачивать не потребуется.</div>
              <details className="group"><summary>Классический Outlook для Windows</summary><div className="help"><ol>
                <li>Скачайте манифест (кнопка выше).</li><li>«Главная» → «Получить надстройки» → «Мои надстройки» → «Пользовательские надстройки» → «Добавить из файла…».</li>
                <li>Выберите скачанный файл, подтвердите предупреждение. Локальных прав администратора Windows не требуется.</li><li>В окне нового собрания появится группа «Peregovorka».</li></ol>
                <p className="muted small">Надстройка, добавленная пользователем, показывает кнопку на ленте, но не заменяет переключатель Teams/Skype — это умеют только надстройки, установленные администратором.</p></div></details>
              <details className="group"><summary>Новый Outlook для Windows</summary><div className="help"><ol>
                <li>Скачайте манифест.</li><li>В левой панели «Приложения» (или «Получить надстройки») → «Мои надстройки» → «Пользовательские надстройки» → «Добавить из файла».</li><li>Выберите файл манифеста.</li></ol>
                <p className="muted small">Доступность пункта зависит от версии клиента и политик организации.</p></div></details>
              <details className="group"><summary>Outlook в браузере</summary><div className="help"><ol>
                <li>Скачайте манифест.</li><li>Откройте письмо или собрание → «⋯» → «Получить надстройки» → «Мои надстройки» → «Пользовательские надстройки» → «Добавить из файла».</li></ol></div></details>
              <details className="group"><summary>Обновление и удаление</summary><div className="help">
                <p>Логика надстройки обновляется вместе с сервером. Заново добавлять манифест нужно, только если изменился адрес сервера (идентификатор зависит от адреса и не меняется при повторном скачивании).</p>
                <p>Удаление: «Пользовательские надстройки» → «⋯» рядом с Peregovorka → «Удалить». Ссылки в уже отправленных приглашениях продолжают работать.</p></div></details>

              <h2>Проверка доступности с этого компьютера</h2>
              <p className="muted small">Проверяет, что именно <b>этот</b> компьютер открывает адрес по HTTPS, принимает сертификат и получает манифест и ресурсы надстройки.</p>
              <div className="row"><button className="btn" onClick={run} disabled={busy}>{busy ? "Проверка…" : "Проверить с этого компьютера"}</button></div>
              {checks && <ul className="stage-list" aria-label="Результаты проверки">{checks.map((c) => <li key={c.key} className={c.ok === false ? "bad" : c.ok === null ? "skip" : "ok"}><span className="stage-mark" aria-hidden>{mark(c.ok)}</span><span><b>{c.title}.</b> {c.message}</span></li>)}</ul>}

              <h2>Совместимость</h2>
              <p className="small">Базовые функции: {info.compat.basic_functions.join("; ")}.</p>
              <p className="small">Дополнительные (зависят от версии Mailbox API): {info.compat.advanced_functions.join("; ")}.</p>
              <div className="table-scroll"><table className="table compact"><thead><tr><th>Среда</th><th>Mailbox API</th><th>Базовые функции</th><th>Подписка на отправку</th><th>Серверная сверка</th><th>Заметки</th></tr></thead><tbody>
                {info.compat.environments.map((e) => (
                  <tr key={e.id}><td>{e.title}</td><td>{e.mailbox_api}</td><td>{yn(e.basic)}</td><td>{yn(e.send_tracking)}</td><td>{e.server_sync.join(", ")}</td><td>{e.note}{!e.verified && " Не проверено в этой установке."}</td></tr>))}
              </tbody></table></div>
              {info.compat.notes.map((n) => <div key={n} className="alert info small">{n}</div>)}

              <h2>Для администратора Exchange</h2>
              <div className="row">
                <a className="btn primary" href={info.package_url} download>Скачать полный пакет установки (ZIP)</a>
                <a className="btn" href={info.manifest_url} download>Только манифест (XML)</a>
                <a className="btn" href="/api/v1/outlook/scripts/Install-OutlookAddin.ps1" download>Install-OutlookAddin.ps1</a>
                <a className="btn" href="/api/v1/outlook/scripts/Test-OutlookAddin.ps1" download>Test-OutlookAddin.ps1</a>
                <a className="btn" href="/api/v1/outlook/scripts/Uninstall-OutlookAddin.ps1" download>Uninstall-OutlookAddin.ps1</a></div>
              <p className="muted small">Прямая ссылка на манифест без перенаправлений (для Exchange 2019 и «Добавить из URL»): <code>{info.manifest_url}</code></p>
              <details className="group" open><summary>Локальный Exchange 2016/2019 и Subscription Edition (Exchange Management Shell)</summary><div className="help"><ol>
                <li>Нужна роль «Org Marketplace Apps» или «Org Management». Распакуйте пакет.</li>
                <li>Проверка условий (ничего не меняет): <code>.\Test-OutlookAddin.ps1</code></li>
                <li>Пробный запуск: <code>.\Install-OutlookAddin.ps1 -WhatIf</code>, затем <code>.\Install-OutlookAddin.ps1</code>. Выбранным пользователям: <code>-ProvidedTo SpecificUsers -UserList 'user@example.org'</code>.</li>
                <li>Либо прямой ссылкой: <code>.\Install-OutlookAddin.ps1 -ManifestUrl '{info.manifest_url}'</code></li>
                <li>Повторный запуск безопасен: дубликат не создаётся. Обновление: <code>-Replace</code>. Удаление: <code>.\Uninstall-OutlookAddin.ps1</code>. Журнал — в файле <code>Peregovorka-Outlook-install-*.log</code>.</li>
                <li>Проверка результата: <code>Get-App -OrganizationApp</code> или снова <code>Test-OutlookAddin.ps1</code>. Клиенты получают надстройку до нескольких часов; перезапустите Outlook.</li></ol>
                <p className="muted small">Exchange Subscription Edition: используйте те же команды; предел Mailbox API вашей сборки уточните в документации Microsoft — подписка на отправку не включается без подтверждения.</p></div></details>
              <details className="group"><summary>Microsoft 365 (Exchange Online)</summary><div className="help"><ol>
                <li>Центр администрирования Microsoft 365 → «Параметры» → «Интегрированные приложения» → «Загрузить пользовательские приложения» → тип «Office Add-in» → файл манифеста.</li>
                <li>Назначьте пользователей или группы; распространение занимает до 24 часов. Либо сценарии выше после <code>Connect-ExchangeOnline</code>.</li></ol></div></details>

              <h2>Сертификаты HTTPS</h2>
              {tls ? (
                <>
                  <p className="small"><span className={`badge ${tls.ok === true ? "ok" : tls.ok === false ? "bad" : "warn"}`}>{tls.ok === true ? "Проверка пройдена" : tls.ok === false ? "Ошибка" : "Требует внимания"}</span> {tls.message}</p>
                  {tls.issuer && <p className="small">Адрес: <b>{tls.host}</b> · издатель: <b>{tls.issuer}</b> · действует до <b>{when(tls.not_after)}</b>{tls.days_left != null ? ` (осталось дней: ${tls.days_left})` : ""}</p>}
                  {tls.chain && tls.chain.length > 0 && <ol className="small">{tls.chain.map((c, i) => <li key={c.sha256}>{c.role === "leaf" ? "Сертификат сайта" : c.role === "root" ? "Корневой центр" : "Промежуточный центр"}: {c.subject} (выдан: {c.issuer}, до {when(c.not_after)}) {" "}
                    <a href={`/api/v1/outlook/tls/cert-${i}.pem`} download>PEM</a> · <a href={`/api/v1/outlook/tls/cert-${i}.cer`} download>CER (DER)</a></li>)}</ol>}
                  {tls.fix && <div className="alert info small"><b>Где исправлять:</b> {tls.fix.text}</div>}
                  <div className="row">{info.downloads.chain && <a className="btn" href="/api/v1/outlook/tls/chain.pem" download>Скачать цепочку (PEM)</a>}{info.downloads.fullchain && <a className="btn" href="/api/v1/outlook/tls/fullchain.pem" download>Скачать fullchain.pem (сайт + промежуточные)</a>}</div>
                </>) : <p className="muted small">Сведения о сертификате сервер получить не смог (нет маршрута к собственному адресу). Откройте адрес в браузере и посмотрите сертификат у значка замка, либо запустите Test-OutlookAddin.ps1 на рабочей станции.</p>}
              <div className="alert info small"><b>Когда импорт сертификатов нужен.</b> Только если организация использует внутренний центр сертификации, которому ещё не доверяют рабочие станции или Exchange: корневой сертификат разворачивается штатными средствами организации (групповая политика, MDM). Для публичного центра сертификации импорт не нужен. Импортировать сертификат самого сайта в доверенные не требуется и не помогает; если прокси отдаёт неполную цепочку, исправляется прокси (fullchain).</div>

              <h2>Документация API</h2>
              <p className="small"><a href="/api/docs">Открыть документацию API</a> — публичный API интеграций и ресурсы установки; методы по-прежнему требуют ключа или входа.</p>
            </>
          )}
          <p><Link className="btn" to="/">На главную</Link></p>
        </section>
      </main>
    </div>
  );
}
