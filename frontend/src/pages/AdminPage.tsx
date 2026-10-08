import { useState, type ReactNode } from "react";
import AsrModelsAdmin from "./admin/AsrModelsAdmin";
import ClientDiagAdmin from "./admin/ClientDiagAdmin";
import { anonFields, audioStorageFields, chatFilesFields, generalFields, journalFields, llmFields, protocolFields, screenFields, storageFields } from "./admin/fields";
import ApiProfilesAdmin from "./admin/ApiProfilesAdmin";
import JournalAdmin from "./admin/JournalAdmin";
import UpdatesAdmin from "./admin/UpdatesAdmin";
import RoomsAdmin from "./admin/RoomsAdmin";
import SettingsForm from "./admin/SettingsForm";
import StoragesAdmin from "./admin/StoragesAdmin";
import SystemAdmin from "./admin/SystemAdmin";
import { AuditAdmin, MeetingsAdmin, RecordingsAdmin, UsersAdmin } from "./admin/Tables";
import TemplatesAdmin from "./admin/TemplatesAdmin";

interface Page { id: string; label: string; render: (go: (id: string) => void) => ReactNode }
interface Group { title: string; pages: Page[] }

/** Пункты сгруппированы по задачам администратора; названия, пояснения и примеры заполнения — внутри форм. */
const GROUPS: Group[] = [
  { title: "Обзор", pages: [
    { id: "system", label: "Состояние системы", render: (go) => <SystemAdmin onOpen={go} /> },
    { id: "clients", label: "Диагностика клиентов", render: () => <ClientDiagAdmin /> },
    { id: "updates", label: "Обновления и версии", render: () => <UpdatesAdmin /> },
  ] },
  { title: "Журналы", pages: [
    { id: "journal", label: "Журнал событий", render: (go) => <JournalAdmin onOpenSettings={() => go("journal_settings")} /> },
    { id: "journal_settings", label: "Хранение журнала", render: () => (
      <SettingsForm key="journal" group="journal" title="Хранение журнала событий" fields={journalFields} testable
        intro="Сколько хранить записи, нужно ли держать их в базе сервера и куда дублировать во внешнее хранилище (диск или сетевой ресурс), чтобы не занимать место на сервере." />) },
    { id: "audit", label: "Журнал аудита", render: () => <AuditAdmin /> },
  ] },
  { title: "Встречи", pages: [
    { id: "meetings", label: "Встречи", render: () => <MeetingsAdmin /> },
    { id: "recordings", label: "Записи аудио", render: () => <RecordingsAdmin /> },
  ] },
  { title: "Комнаты и люди", pages: [
    { id: "rooms", label: "Переговорки", render: () => <RoomsAdmin /> },
    { id: "users", label: "Пользователи", render: () => <UsersAdmin /> },
  ] },
  { title: "Протоколы", pages: [
    { id: "protocol", label: "Инструкции и режим", render: () => (
      <SettingsForm key="protocol" group="protocol" title="Инструкции и режим формирования протоколов" fields={protocolFields}
        intro="Что по умолчанию просит модель и когда документы создаются сами. Пользователь всегда видит инструкцию в окне «Сформировать протокол» и может её изменить." />) },
    { id: "templates", label: "Общие шаблоны", render: () => <TemplatesAdmin /> },
  ] },
  { title: "Интеграции", pages: [
    { id: "storages", label: "Хранилища (серверы файлов)", render: (go) => <StoragesAdmin onOpen={go} /> },
    { id: "storage", label: "Хранилище протоколов", render: () => (
      <SettingsForm key="storage" group="storage" title="Хранилище протоколов и материалов" fields={storageFields} testable
        intro="Куда складываются стенограммы, протоколы, переписка и схемы доски. Выберите хранилище из раздела «Хранилища» — подпапки создаются автоматически." />) },
    { id: "audio_storage", label: "Хранилище записей", render: () => (
      <SettingsForm key="audio_storage" group="audio_storage" title="Хранилище аудиозаписей" fields={audioStorageFields} testable
        intro="Отдельное место для звука встреч — так большие файлы не смешиваются с протоколами. Выберите хранилище из раздела «Хранилища»." />) },
    { id: "chat_files", label: "Вложения чата", render: () => (
      <SettingsForm key="chat_files" group="chat_files" title="Вложения чата" fields={chatFilesFields} testable
        intro="Файлы и картинки в чате встречи: размер, допустимые типы и место хранения (общее хранилище, в том числе SMB)." />) },
    { id: "asr", label: "Распознавание речи (ASR)", render: () => <AsrModelsAdmin /> },
    { id: "anon", label: "Обезличивание", render: () => (
      <>
        <SettingsForm key="anonymizer" group="anonymizer" title="Обезличивание: основной API" fields={anonFields} testable
          intro="Внутренний сервис обезличивания (DocClean или совместимый JSON API). Пока он ВКЛЮЧЁН, текст проходит через него перед отправкой в языковую модель, а при сбое протокол не создаётся. Если выключить — протоколы и резюме создаются как обычно, но текст уходит в модель без обезличивания. Для отдельной переговорки обезличивание можно выключить или включить принудительно в её настройках." />
        <ApiProfilesAdmin key="anon-profiles" kind="anonymizer" fields={anonFields} />
      </>) },
    { id: "llm", label: "Языковая модель (LLM)", render: () => (
      <>
        <SettingsForm key="llm" group="llm" title="Языковая модель: основной API" fields={llmFields} testable
          intro="Модель формирует протоколы, решения и поручения. Если обезличивание включено, данные отправляются только после него; если выключено (в общих настройках или в переговорке) — в исходном виде." />
        <ApiProfilesAdmin key="llm-profiles" kind="llm" fields={llmFields} />
      </>) },
  ] },
  { title: "Система", pages: [
    { id: "screen", label: "Показ экрана", render: () => <SettingsForm key="screen" group="screen" title="Показ экрана" fields={screenFields} intro="Качество и поведение показа экрана для всех комнат, где он разрешён." /> },
    { id: "general", label: "Общие настройки", render: () => <SettingsForm key="general" group="general" title="Общие настройки" fields={generalFields} /> },
  ] },
];

export default function AdminPage({ version }: { version: string }) {
  const ids = GROUPS.flatMap((g) => g.pages.map((p) => p.id));
  const [tab, setTab] = useState(() => { const t = sessionStorage.getItem("adminTab"); return t && ids.includes(t) ? t : "system"; });
  const pick = (t: string) => { setTab(t); try { sessionStorage.setItem("adminTab", t); } catch { /* ignore */ } };
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
