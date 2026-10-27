import { useEffect, useState, type ReactNode } from "react";
import { useLocation } from "react-router-dom";
import { tabFromSearch } from "../navMenu";
import { collapseAll, expandAll, initialCollapsed, saveCollapsed, toggleGroup } from "../adminNav";
import { Icon } from "../components/Icons";
import AccessAdmin from "./admin/AccessAdmin";
import LoginAccessAdmin from "./admin/LoginAccessAdmin";
import AsrModelsAdmin from "./admin/AsrModelsAdmin";
import Bitrix24Admin from "./admin/Bitrix24Admin";
import PublicApiAdmin from "./admin/PublicApiAdmin";
import CaAdmin from "./admin/CaAdmin";
import SiteAdmin from "./admin/SiteAdmin";
import ClientDiagAdmin from "./admin/ClientDiagAdmin";
import { anonFields, audioStorageFields, authSecurityFields, captchaFields, loginProtectionFields, securityLogFields, chatFilesFields, chatsFields, sipPolicyFields, profilePolicyFields, generalFields, journalFields, mailPolicyFields, privacyFields, protocolFields, screenFields, storageFields } from "./admin/fields";
import ApiProfilesAdmin from "./admin/ApiProfilesAdmin";
import JournalAdmin from "./admin/JournalAdmin";
import LdapAdmin from "./admin/LdapAdmin";
import MailAdmin, { MailLogAdmin } from "./admin/MailAdmin";
import MailTemplatesAdmin from "./admin/MailTemplatesAdmin";
import LlmAdmin from "./admin/LlmAdmin";
import UpdatesAdmin from "./admin/UpdatesAdmin";
import RoomsAdmin from "./admin/RoomsAdmin";
import OutlookAdmin from "./admin/OutlookAdmin";
import { confirmLeave } from "../dirtyGuard";
import SipAdmin from "./admin/SipAdmin";
import IpAccessAdmin from "./admin/IpAccessAdmin";
import SecurityEventsAdmin from "./admin/SecurityEventsAdmin";
import WafAdmin from "./admin/WafAdmin";
import NetTrustAdmin from "./admin/NetTrustAdmin";
import SettingsForm from "./admin/SettingsForm";
import StorageSyncAdmin from "./admin/StorageSyncAdmin";
import RestoreAdmin from "./admin/RestoreAdmin";
import StoragesAdmin from "./admin/StoragesAdmin";
import ConfigBackupAdmin from "./admin/ConfigBackupAdmin";
import CryptoAdmin from "./admin/CryptoAdmin";
import SigningAdmin from "./admin/SigningAdmin";
import SystemAdmin from "./admin/SystemAdmin";
import PerformanceAdmin from "./admin/PerformanceAdmin";
import NetworkAdmin from "./admin/NetworkAdmin";
import { AuditAdmin, MeetingsAdmin, RecordingsAdmin, UsersAdmin } from "./admin/Tables";
import TemplatesAdmin from "./admin/TemplatesAdmin";

interface Page { id: string; label: string; render: (go: (id: string) => void) => ReactNode }
interface Group { title: string; pages: Page[] }

/** Разделы сгруппированы по назначению: обзор, встречи, комнаты и пользователи, интеграции, безопасность, хранение и журналы, система. Идентификаторы разделов (адрес `/admin?tab=…`) не менять: на них ссылаются мастер настройки, меню и документация. */
const GROUPS: Group[] = [
  { title: "Обзор", pages: [
    { id: "system", label: "Обзор", render: (go) => <SystemAdmin onOpen={go} /> },
    { id: "system_tech", label: "Технические показатели", render: (go) => <SystemAdmin onOpen={go} view="tech" /> },
    { id: "performance", label: "Производительность", render: () => <PerformanceAdmin /> },
    { id: "clients", label: "Диагностика клиентов", render: () => <ClientDiagAdmin /> },
  ] },
  { title: "Встречи", pages: [
    { id: "meetings", label: "Встречи", render: () => <MeetingsAdmin /> },
    { id: "recordings", label: "Аудиозаписи", render: () => <RecordingsAdmin /> },
    { id: "protocol", label: "Протоколы: инструкции и режим", render: () => (
      <SettingsForm key="protocol" group="protocol" title="Инструкции и режим формирования протоколов" fields={protocolFields}
        intro="Что по умолчанию просит модель и когда документы создаются сами. Пользователь всегда видит инструкцию в окне «Сформировать протокол» и может её изменить." />) },
    { id: "templates", label: "Протоколы: общие шаблоны", render: () => <TemplatesAdmin /> },
  ] },
  { title: "Комнаты и пользователи", pages: [
    { id: "rooms", label: "Комнаты и доступ", render: () => <RoomsAdmin /> },
    { id: "users", label: "Пользователи", render: () => <UsersAdmin /> },
    { id: "login_access", label: "Кто может входить", render: (go) => <LoginAccessAdmin onOpen={go} /> },
    { id: "access", label: "Администраторы сервера", render: () => <AccessAdmin /> },
    { id: "profile_policy", label: "Правка профиля", render: () => (
      <SettingsForm key="profile_policy" group="profile_policy" title="Что пользователь может изменить в личном кабинете" fields={profilePolicyFields}
        intro="Правка пользователя сильнее данных каталога: вход и «обновить из AD» её не перезаписывают, а кнопка «Восстановить из LDAP» возвращает значение каталога. Все правки записываются в журнал аудита." />) },
  ] },
  { title: "Интеграции", pages: [
    { id: "ldap", label: "Каталог: подключения LDAP/AD", render: (go) => <LdapAdmin onOpen={go} /> },
    { id: "ca", label: "Каталог: сертификаты (CA)", render: () => <CaAdmin /> },
    { id: "mail", label: "Почта: сервер SMTP", render: (go) => <MailAdmin onOpen={go} /> },
    { id: "asr", label: "Распознавание речи (ASR)", render: () => <AsrModelsAdmin /> },
    { id: "llm", label: "Языковая модель (LLM)", render: () => <LlmAdmin /> },
    { id: "anon", label: "Обезличивание", render: () => (
      <>
        <SettingsForm key="anonymizer" group="anonymizer" title="Обезличивание: основной API" fields={anonFields} testable
          intro="Внутренний сервис обезличивания (DocClean или совместимый JSON API). Пока он ВКЛЮЧЁН, текст проходит через него перед отправкой в языковую модель, а при сбое протокол не создаётся. Если выключить — протоколы и резюме создаются как обычно, но текст уходит в модель без обезличивания. Для отдельной переговорки обезличивание можно выключить или включить принудительно в её настройках." />
        <ApiProfilesAdmin key="anon-profiles" kind="anonymizer" fields={anonFields} />
      </>) },
    { id: "storages", label: "Файловые хранилища", render: (go) => <StoragesAdmin onOpen={go} /> },
    { id: "outlook", label: "Microsoft Outlook", render: () => <OutlookAdmin /> },
    { id: "sip", label: "SIP-телефония", render: () => <SipAdmin /> },
    { id: "sip_policy", label: "Политика телефонии", render: () => (
      <SettingsForm key="sip_policy" group="sip_policy" title="Политика телефонии: ограничения и журнал вызовов" fields={sipPolicyFields}
        intro="Ограничения действуют на звонки из комнат и на входящие вызовы. Профили, порты и проверка АТС — в разделе «SIP-телефония»." />) },
    { id: "bitrix24", label: "Bitrix24 (профили)", render: () => <Bitrix24Admin /> },
    { id: "public_api", label: "Публичный API", render: () => <PublicApiAdmin /> },
  ] },
  { title: "Windows-клиент", pages: [
    { id: "desktop_signing", label: "Подпись и корпоративные сборки", render: () => <SigningAdmin /> },
  ] },
  { title: "Безопасность", pages: [
    { id: "auth_security", label: "Вход и сеансы", render: () => (
      <SettingsForm key="auth_security" group="auth_security" title="Вход: сеансы, приложение Windows, двухфакторная аутентификация (TOTP)" fields={authSecurityFields}
        intro="Одноразовые коды из приложения-аутентификатора. Если защита обязательна, пользователь при первом входе настраивает её сам (QR-код и резервные коды). Потерянное устройство — «Пользователи → сбросить» или консольная команда admin-reset для локального администратора." />) },
    { id: "security_events", label: "События и блокировки", render: () => <SecurityEventsAdmin /> },
    { id: "security_blocks", label: "Заблокированные адреса", render: () => <SecurityEventsAdmin initialTab="blocks" key="blocks" /> },
    { id: "login_protection", label: "Защита входа от перебора", render: () => (
      <SettingsForm key="login_protection" group="login_protection" title="Защита входа от перебора паролей" fields={loginProtectionFields}
        intro="Серверные счётчики по учётной записи, по IP-адресу, по их паре и по общей интенсивности запросов; состояние хранится в Redis, поэтому закрытие браузера, режим инкогнито, очистка cookie, смена User-Agent и перезапуск не сбрасывают защиту. Реакции нарастают: CAPTCHA → замедление → защита каталога → временная блокировка адреса. Учётная запись целиком не блокируется. Пороги CAPTCHA — в разделе «CAPTCHA»." />) },
    { id: "captcha", label: "CAPTCHA", render: () => (
      <SettingsForm key="captcha" group="captcha" title="CAPTCHA (проверка «я не робот»)" fields={captchaFields}
        intro="Локальная проверка без внешних сервисов. По умолчанию — картинка с символами: после заданного числа неудачных попыток сервер не принимает вход без правильно введённых символов. Защищает вход, вход гостя по ссылке и создание временной переговорки. CAPTCHA — не единственная защита: перебор ограничивается и без неё." />) },
    { id: "waf", label: "Защита веб-приложения (WAF)", render: () => <WafAdmin /> },
    { id: "security_log", label: "Хранение журнала безопасности", render: () => (
      <SettingsForm key="security_log" group="security_log" title="Хранение событий безопасности" fields={securityLogFields}
        intro="Сколько хранить события раздела «События и блокировки» и сколько строк оставлять максимум. Старые записи удаляются автоматически раз в час. Пароли, токены и cookie в журнал не попадают." />) },
    { id: "ip_access", label: "Доверенные подсети", render: () => <IpAccessAdmin /> },
    { id: "net_trust", label: "Сеть и IP-адреса (прокси, WAF)", render: () => <NetTrustAdmin /> },
    { id: "crypto", label: "Шифрование хранилища", render: () => <CryptoAdmin /> },
    { id: "audit", label: "Журнал аудита", render: () => <AuditAdmin /> },
  ] },
  { title: "Хранение и журналы", pages: [
    { id: "storage", label: "Хранилище документов встреч", render: () => (
      <SettingsForm key="storage" group="storage" title="Хранилище протоколов и материалов" fields={storageFields} testable
        intro="Куда складываются стенограммы, протоколы, переписка и схемы доски. Выберите хранилище из раздела «Файловые хранилища» — подпапки создаются автоматически." />) },
    { id: "audio_storage", label: "Хранилище аудиозаписей", render: () => (
      <SettingsForm key="audio_storage" group="audio_storage" title="Хранилище аудиозаписей" fields={audioStorageFields} testable
        intro="Отдельное место для звука встреч — так большие файлы не смешиваются с протоколами. Выберите хранилище из раздела «Файловые хранилища»." />) },
    { id: "chats", label: "Корпоративные чаты", render: () => (
      <SettingsForm key="chats" group="chats" title="Корпоративные чаты" fields={chatsFields}
        intro="Личные и групповые переписки сотрудников. Вложения подчиняются правилам раздела «Хранилище вложений чата» (размер, типы, хранилище). Тексты переписки в технические журналы не пишутся." />) },
    { id: "chat_files", label: "Хранилище вложений чата", render: () => (
      <SettingsForm key="chat_files" group="chat_files" title="Вложения чата" fields={chatFilesFields} testable
        intro="Файлы и картинки в чате встречи и в корпоративных чатах: размер, допустимые типы и место хранения (общее хранилище, в том числе SMB)." />) },
    { id: "storage_sync", label: "Целостность хранилищ", render: () => <StorageSyncAdmin /> },
    { id: "restore", label: "Восстановление материалов", render: () => <RestoreAdmin /> },
    { id: "journal", label: "Журнал событий", render: (go) => <JournalAdmin onOpenSettings={() => go("journal_settings")} /> },
    { id: "journal_settings", label: "Хранение журнала", render: () => (
      <SettingsForm key="journal" group="journal" title="Хранение журнала событий" fields={journalFields} testable
        intro="Сколько хранить записи, нужно ли держать их в базе сервера и куда дублировать во внешнее хранилище (диск или сетевой ресурс), чтобы не занимать место на сервере." />) },
    { id: "mail_log", label: "Почта: журнал отправки", render: () => <MailLogAdmin /> },
  ] },
  { title: "Система", pages: [
    { id: "network", label: "Сетевая доступность", render: () => <NetworkAdmin /> },
    { id: "updates", label: "Обновления и версии", render: (go) => <UpdatesAdmin onOpen={go} /> },
    { id: "config_backup", label: "Резервная копия конфигурации", render: () => <ConfigBackupAdmin /> },
    { id: "general", label: "Общие настройки", render: () => <SettingsForm key="general" group="general" title="Общие настройки" fields={generalFields} /> },
    { id: "site", label: "Настройки сайта", render: () => <SiteAdmin /> },
    { id: "privacy", label: "Конфиденциальность и cookie", render: () => (
      <SettingsForm key="privacy" group="privacy" title="Конфиденциальность и cookie" fields={privacyFields}
        intro="Тексты для страницы входа и страницы «Обработка данных» (открывается без входа и по ссылке «Подробнее»). Юридические формулировки в приложении не зашиты — заполните их по правилам вашей организации. Используются только технические cookie." /> ) },
    { id: "screen", label: "Демонстрация экрана", render: () => <SettingsForm key="screen" group="screen" title="Демонстрация экрана" fields={screenFields} intro="Качество и поведение показа экрана для всех комнат, где он разрешён." /> },
    { id: "mail_policy", label: "Почта: правила рассылки", render: () => (
      <SettingsForm key="mail_policy" group="mail_policy" title="Правила рассылки материалов встреч" fields={mailPolicyFields}
        intro="Глобальные ограничения. Руководитель комнаты выбирает только, какие материалы и кому отправлять (в «Настройки комнаты» → «Материалы после встречи»); адреса сервера и пароль ему не показываются." />) },
    { id: "mail_templates", label: "Почта: шаблоны писем", render: () => <MailTemplatesAdmin /> },
  ] },
];

/** Дополнительные слова для поиска по меню: технические названия остаются в подсказках, а не в названиях разделов. */
const KEYWORDS: Record<string, string> = {
  restore: "восстановление найти материалы перенос хранилища записи стенограммы протоколы паспорт manifest сканирование диск потерялись не отображаются",
  network: "порты межсетевой экран firewall nat udp tcp livekit публикация интернет reverse proxy turn stun доступность сеть",
  config_backup: "экспорт импорт настройки перенос восстановление архив бэкап пароль секреты", system: "состояние здоровье сервисы проблемы", system_tech: "cpu память ядро asr rtf тайминги обслуживание очистка", ldap: "active directory ad ldaps домен каталог", ca: "сертификат ssl tls корневой",
  crypto: "шифрование ключи aes хранилище чат стенограммы мастер-ключ", desktop_signing: "windows приложение клиент подпись сертификат code signing authenticode msi exe установщик gpo ad cs обновления", outlook: "outlook календарь надстройка office exchange приглашение расписание манифест", profile_policy: "профиль правка редактирование поля ldap должность телефон", captcha: "капча captcha перебор бот защита altcha картинка", login_protection: "перебор пароль блокировка ip защита входа брутфорс замедление nat", security_events: "события безопасности журнал блокировки неудачные входы отчёт заблокированные ip", security_blocks: "заблокированные адреса снять блокировку разблокировать вручную ip", waf: "waf межсетевой экран правила наблюдение защита сканирование sql xss инъекции owasp", security_log: "хранение событий безопасности срок размер", ip_access: "доверенные подсети разрешённые ip адреса сети cidr ограничить доступ firewall allowlist whitelist", net_trust: "ip адрес клиента прокси waf x-forwarded-for reverse proxy доверенные заголовок cloudflare", auth_security: "2fa totp двухфакторная аутентификация код приложение qr резервные коды", login_access: "группы вход доступ ldap", access: "администратор права роли", storages: "smb cifs сетевая папка каталог nas", storage_sync: "сверка целостность хранилище",
  mail: "smtp почта письма сервер", mail_policy: "рассылка вложения получатели", llm: "модель языковая qwen api ключ openai", asr: "whisper распознавание речи транскрибация", anon: "docclean персональные данные",
  public_api: "ключи токены интеграции rest webhook scopes права swagger openapi", sip: "телефония asterisk звонок атс инструкция порты rtp журнал вызовов", sip_policy: "телефония политика лимит звонков запрещённые направления перебор", bitrix24: "битрикс портал должность подразделение фото аватарка сотрудники профиль webhook", journal: "события ошибки лог", audit: "аудит действия", privacy: "cookie данные политика", site: "название логотип favicon цвет тема оформление брендинг организация контакты поддержка документы политика согласие соглашение персональные данные", screen: "показ экрана трансляция", users: "учётные записи сотрудники", rooms: "переговорки комнаты ссылки",
};

export default function AdminPage({ version }: { version: string }) {
  const ids = GROUPS.flatMap((g) => g.pages.map((p) => p.id));
  const location = useLocation();
  const [tab, setTab] = useState(() => tabFromSearch(location.search, ids) ?? (() => { const t = sessionStorage.getItem("adminTab"); return t && ids.includes(t) ? t : "system"; })());
  const [collapsed, setCollapsed] = useState<Set<string>>(() => initialCollapsed(GROUPS.map((g) => g.title), GROUPS.find((g) => g.pages.some((p) => p.id === tab))?.title));
  const [q, setQ] = useState("");
  const needle = q.trim().toLowerCase();
  const found = needle ? GROUPS.flatMap((g) => g.pages.filter((p) => `${p.label} ${g.title} ${KEYWORDS[p.id] ?? ""}`.toLowerCase().includes(needle)).map((p) => ({ id: p.id, label: p.label, group: g.title }))) : null;
  const setGroups = (s: Set<string>) => { setCollapsed(s); saveCollapsed(s); };
  // переход к странице (из мастера настройки, верхнего меню) раскрывает её раздел, чтобы пункт не оказался скрытым
  const pick = (t: string) => {
    if (t !== tab && !confirmLeave()) return;
    setTab(t);
    try { sessionStorage.setItem("adminTab", t); } catch { /* ignore */ }
    const g = GROUPS.find((x) => x.pages.some((p) => p.id === t));
    if (g) setCollapsed((c) => { if (!c.has(g.title)) return c; const n = new Set(c); n.delete(g.title); saveCollapsed(n); return n; });
  };
  // переход из мастера первоначальной настройки (страница могла быть уже открыта)
  useEffect(() => {
    const on = (e: Event) => { const t = (e as CustomEvent<string>).detail; if (ids.includes(t)) pick(t); };
    window.addEventListener("admin:goto", on);
    return () => window.removeEventListener("admin:goto", on);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  // переход из верхнего меню (/admin?tab=…) при уже открытой странице
  useEffect(() => { const t = tabFromSearch(location.search, ids); if (t) pick(t); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [location.search]);
  const page = GROUPS.flatMap((g) => g.pages).find((p) => p.id === tab);
  return (
    <section>
      <div className="row"><h1>Администрирование</h1><div className="spacer" /><span className="muted small">Версия: {version || "—"}</span></div>
      <div className="admin-layout">
        <nav className="admin-nav" aria-label="Разделы администрирования">
          <input type="search" className="nav-search" placeholder="Найти раздел…" aria-label="Поиск по разделам администрирования" value={q} onChange={(e) => setQ(e.target.value)}
                 onKeyDown={(e) => { if (e.key === "Enter" && found?.length) { pick(found[0].id); setQ(""); } if (e.key === "Escape") setQ(""); }} />
          {found && (
            <div className="nav-found" role="list">
              {found.length === 0 && <div className="muted small">Ничего не найдено</div>}
              {found.map((x) => <button key={x.id} role="listitem" className={tab === x.id ? "active" : ""} onClick={() => { pick(x.id); setQ(""); }}>{x.label}<span className="muted small"> · {x.group}</span></button>)}
            </div>
          )}
          <div className="nav-tools" role="group" aria-label="Все разделы меню" hidden={!!found}>
            <button type="button" className="icon-btn" onClick={() => setGroups(expandAll())} title="Развернуть все разделы" aria-label="Развернуть все разделы"><Icon name="expandAll" size={16} /></button>
            <button type="button" className="icon-btn" onClick={() => setGroups(collapseAll(GROUPS.map((g) => g.title)))} title="Свернуть все разделы" aria-label="Свернуть все разделы"><Icon name="collapseAll" size={16} /></button>
          </div>
          {!found && GROUPS.map((g, gi) => {
            const open = !collapsed.has(g.title);
            return (
              <div key={g.title} className={`nav-group ${open ? "open" : ""}`}>
                <h4><button type="button" className="nav-head" aria-expanded={open} aria-controls={`navg-${gi}`} onClick={() => setGroups(toggleGroup(collapsed, g.title))}
                            title={open ? "Свернуть раздел" : "Развернуть раздел"}><Icon name="chevronD" size={14} className="chev" /><span>{g.title}</span></button></h4>
                <div id={`navg-${gi}`} className="nav-items" hidden={!open}>
                  {g.pages.map((p) => <button key={p.id} className={tab === p.id ? "active" : ""} aria-current={tab === p.id ? "page" : undefined} onClick={() => pick(p.id)}>{p.label}</button>)}
                </div>
              </div>
            );
          })}
        </nav>
        <div style={{ minWidth: 0 }}>{page?.render(pick)}</div>
      </div>
    </section>
  );
}
