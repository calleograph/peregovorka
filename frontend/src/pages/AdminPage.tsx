import { useEffect, useState, type ReactNode } from "react";
import { useLocation } from "react-router-dom";
import { tabFromSearch } from "../navMenu";
import AccessAdmin from "./admin/AccessAdmin";
import LoginAccessAdmin from "./admin/LoginAccessAdmin";
import AsrModelsAdmin from "./admin/AsrModelsAdmin";
import CaAdmin from "./admin/CaAdmin";
import ClientDiagAdmin from "./admin/ClientDiagAdmin";
import { anonFields, audioStorageFields, chatFilesFields, generalFields, journalFields, llmApiFields, llmModeFields, mailPolicyFields, protocolFields, screenFields, storageFields } from "./admin/fields";
import ApiProfilesAdmin from "./admin/ApiProfilesAdmin";
import JournalAdmin from "./admin/JournalAdmin";
import LdapAdmin from "./admin/LdapAdmin";
import MailAdmin, { MailLogAdmin } from "./admin/MailAdmin";
import UpdatesAdmin from "./admin/UpdatesAdmin";
import RoomsAdmin from "./admin/RoomsAdmin";
import SipAdmin from "./admin/SipAdmin";
import SettingsForm from "./admin/SettingsForm";
import StorageSyncAdmin from "./admin/StorageSyncAdmin";
import StoragesAdmin from "./admin/StoragesAdmin";
import SystemAdmin from "./admin/SystemAdmin";
import LocalLlmPanel from "./admin/LocalLlmPanel";
import { LlmEffectivePanel, LlmStatsPanel } from "./admin/LlmOverview";
import { AuditAdmin, MeetingsAdmin, RecordingsAdmin, UsersAdmin } from "./admin/Tables";
import TemplatesAdmin from "./admin/TemplatesAdmin";

interface Page { id: string; label: string; render: (go: (id: string) => void) => ReactNode }
interface Group { title: string; pages: Page[] }

/** Разделы сгруппированы по смыслу: состояние, вход и доступ, хранилища, почта, встречи, интеграции, журналы, система. Пароли нигде не показываются. */
const GROUPS: Group[] = [
  { title: "Состояние", pages: [
    { id: "system", label: "Состояние системы", render: (go) => <SystemAdmin onOpen={go} /> },
    { id: "clients", label: "Диагностика клиентов", render: () => <ClientDiagAdmin /> },
    { id: "updates", label: "Обновления и версии", render: (go) => <UpdatesAdmin onOpen={go} /> },
  ] },
  { title: "LDAP и доступ", pages: [
    { id: "ldap", label: "Подключения LDAP", render: (go) => <LdapAdmin onOpen={go} /> },
    { id: "ca", label: "Сертификаты (CA)", render: () => <CaAdmin /> },
    { id: "login_access", label: "Доступ к системе (кто может входить)", render: (go) => <LoginAccessAdmin onOpen={go} /> },
    { id: "access", label: "Доступ к администрированию", render: () => <AccessAdmin /> },
  ] },
  { title: "Хранилища", pages: [
    { id: "storages", label: "Серверы файлов (SMB, каталог)", render: (go) => <StoragesAdmin onOpen={go} /> },
    { id: "storage", label: "Протоколы и материалы", render: () => (
      <SettingsForm key="storage" group="storage" title="Хранилище протоколов и материалов" fields={storageFields} testable
        intro="Куда складываются стенограммы, протоколы, переписка и схемы доски. Выберите хранилище из раздела «Серверы файлов» — подпапки создаются автоматически." />) },
    { id: "audio_storage", label: "Записи аудио", render: () => (
      <SettingsForm key="audio_storage" group="audio_storage" title="Хранилище аудиозаписей" fields={audioStorageFields} testable
        intro="Отдельное место для звука встреч — так большие файлы не смешиваются с протоколами. Выберите хранилище из раздела «Серверы файлов»." />) },
    { id: "chat_files", label: "Вложения чата", render: () => (
      <SettingsForm key="chat_files" group="chat_files" title="Вложения чата" fields={chatFilesFields} testable
        intro="Файлы и картинки в чате встречи: размер, допустимые типы и место хранения (общее хранилище, в том числе SMB)." />) },
    { id: "storage_sync", label: "Сверка хранилищ", render: () => <StorageSyncAdmin /> },
  ] },
  { title: "Электронная почта", pages: [
    { id: "mail", label: "Исходящая почта (SMTP)", render: (go) => <MailAdmin onOpen={go} /> },
    { id: "mail_policy", label: "Правила рассылки", render: () => (
      <SettingsForm key="mail_policy" group="mail_policy" title="Правила рассылки материалов встреч" fields={mailPolicyFields}
        intro="Глобальные ограничения. Руководитель комнаты выбирает только, какие материалы и кому отправлять (в «Настройки комнаты» → «Материалы после встречи»); адреса сервера и пароль ему не показываются." />) },
    { id: "mail_log", label: "Журнал отправки", render: () => <MailLogAdmin /> },
  ] },
  { title: "Встречи и комнаты", pages: [
    { id: "rooms", label: "Переговорки", render: () => <RoomsAdmin /> },
    { id: "meetings", label: "Встречи", render: () => <MeetingsAdmin /> },
    { id: "recordings", label: "Записи аудио (список)", render: () => <RecordingsAdmin /> },
    { id: "users", label: "Пользователи", render: () => <UsersAdmin /> },
    { id: "protocol", label: "Инструкции и режим протоколов", render: () => (
      <SettingsForm key="protocol" group="protocol" title="Инструкции и режим формирования протоколов" fields={protocolFields}
        intro="Что по умолчанию просит модель и когда документы создаются сами. Пользователь всегда видит инструкцию в окне «Сформировать протокол» и может её изменить." />) },
    { id: "templates", label: "Общие шаблоны", render: () => <TemplatesAdmin /> },
  ] },
  { title: "Интеграции", pages: [
    { id: "asr", label: "Распознавание речи (ASR)", render: () => <AsrModelsAdmin /> },
    { id: "anon", label: "Обезличивание", render: () => (
      <>
        <SettingsForm key="anonymizer" group="anonymizer" title="Обезличивание: основной API" fields={anonFields} testable
          intro="Внутренний сервис обезличивания (DocClean или совместимый JSON API). Пока он ВКЛЮЧЁН, текст проходит через него перед отправкой в языковую модель, а при сбое протокол не создаётся. Если выключить — протоколы и резюме создаются как обычно, но текст уходит в модель без обезличивания. Для отдельной переговорки обезличивание можно выключить или включить принудительно в её настройках." />
        <ApiProfilesAdmin key="anon-profiles" kind="anonymizer" fields={anonFields} />
      </>) },
    { id: "llm", label: "Языковая модель (LLM)", render: () => (
      <>
        <LlmEffectivePanel />
        <SettingsForm key="llm-mode" group="llm" title="Системные значения по умолчанию: какая модель что делает" fields={llmModeFields}
          intro="Эти значения действуют, пока для переговорки, встречи или конкретного формирования документа не выбрана другая модель. Порядок выбора при формировании: модель, выбранная в окне «Сформировать» → настройка встречи → настройка переговорки → эти значения." />
        <LocalLlmPanel />
        <SettingsForm key="llm-api" group="llm" title="Внешний API по умолчанию (прежние общие настройки)" fields={llmApiFields} testable
          intro="Подключение к внешней модели, которое используется, когда выше выбрано «Внешняя LLM». Для внешней модели действует обезличивание по правилам переговорки. Несколько подключений можно завести в разделе ниже." />
        <ApiProfilesAdmin key="llm-profiles" kind="llm" fields={llmApiFields} />
        <LlmStatsPanel />
      </>) },
    { id: "sip", label: "SIP-телефония", render: () => <SipAdmin /> },
  ] },
  { title: "Журналы", pages: [
    { id: "journal", label: "Журнал событий", render: (go) => <JournalAdmin onOpenSettings={() => go("journal_settings")} /> },
    { id: "journal_settings", label: "Хранение журнала", render: () => (
      <SettingsForm key="journal" group="journal" title="Хранение журнала событий" fields={journalFields} testable
        intro="Сколько хранить записи, нужно ли держать их в базе сервера и куда дублировать во внешнее хранилище (диск или сетевой ресурс), чтобы не занимать место на сервере." />) },
    { id: "audit", label: "Журнал аудита", render: () => <AuditAdmin /> },
  ] },
  { title: "Система", pages: [
    { id: "screen", label: "Показ экрана", render: () => <SettingsForm key="screen" group="screen" title="Показ экрана" fields={screenFields} intro="Качество и поведение показа экрана для всех комнат, где он разрешён." /> },
    { id: "general", label: "Общие настройки", render: () => <SettingsForm key="general" group="general" title="Общие настройки" fields={generalFields} /> },
  ] },
];

export default function AdminPage({ version }: { version: string }) {
  const ids = GROUPS.flatMap((g) => g.pages.map((p) => p.id));
  const location = useLocation();
  const [tab, setTab] = useState(() => tabFromSearch(location.search, ids) ?? (() => { const t = sessionStorage.getItem("adminTab"); return t && ids.includes(t) ? t : "system"; })());
  const pick = (t: string) => { setTab(t); try { sessionStorage.setItem("adminTab", t); } catch { /* ignore */ } };
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
          {GROUPS.map((g) => (
            <div key={g.title}>
              <h4>{g.title}</h4>
              {g.pages.map((p) => <button key={p.id} className={tab === p.id ? "active" : ""} aria-current={tab === p.id ? "page" : undefined} onClick={() => pick(p.id)}>{p.label}</button>)}
            </div>
          ))}
        </nav>
        <div style={{ minWidth: 0 }}>{page?.render(pick)}</div>
      </div>
    </section>
  );
}
