import { useState, type ReactNode } from "react";
import AsrModelsAdmin from "./admin/AsrModelsAdmin";
import ClientDiagAdmin from "./admin/ClientDiagAdmin";
import { anonFields, audioStorageFields, generalFields, llmFields, protocolFields, screenFields, storageFields } from "./admin/fields";
import RoomsAdmin from "./admin/RoomsAdmin";
import SettingsForm from "./admin/SettingsForm";
import SystemAdmin from "./admin/SystemAdmin";
import { AuditAdmin, MeetingsAdmin, RecordingsAdmin, UsersAdmin } from "./admin/Tables";
import TemplatesAdmin from "./admin/TemplatesAdmin";

interface Page { id: string; label: string; render: () => ReactNode }
interface Group { title: string; pages: Page[] }

/** Пункты сгруппированы по задачам администратора; названия, пояснения и примеры заполнения — внутри форм. */
const GROUPS: Group[] = [
  { title: "Обзор", pages: [
    { id: "system", label: "Состояние системы", render: () => <SystemAdmin /> },
    { id: "clients", label: "Диагностика клиентов", render: () => <ClientDiagAdmin /> },
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
    { id: "storage", label: "Хранилище протоколов", render: () => (
      <SettingsForm key="storage" group="storage" title="Хранилище протоколов" fields={storageFields} testable
        intro="Куда складываются стенограммы и готовые протоколы. Для SMB укажите сервисную учётную запись с правом записи — приложение подключается само, монтирование не требуется." />) },
    { id: "audio_storage", label: "Хранилище записей", render: () => (
      <SettingsForm key="audio_storage" group="audio_storage" title="Хранилище аудиозаписей" fields={audioStorageFields} testable
        intro="Отдельное место для звука встреч — так большие файлы не смешиваются с протоколами. Адрес сервера и пути задаются здесь, а не в файлах установки." />) },
    { id: "asr", label: "Распознавание речи (ASR)", render: () => <AsrModelsAdmin /> },
    { id: "anon", label: "Обезличивание", render: () => (
      <SettingsForm key="anonymizer" group="anonymizer" title="API обезличивания" fields={anonFields} testable
        intro="Внутренний сервис обезличивания (DocClean или совместимый JSON API). Любой текст проходит через него перед отправкой в языковую модель." />) },
    { id: "llm", label: "Языковая модель (LLM)", render: () => (
      <SettingsForm key="llm" group="llm" title="Языковая модель для протоколов" fields={llmFields} testable
        intro="Модель формирует протоколы, решения и поручения. Данные отправляются только после обезличивания." />) },
  ] },
  { title: "Система", pages: [
    { id: "screen", label: "Показ экрана", render: () => <SettingsForm key="screen" group="screen" title="Показ экрана" fields={screenFields} intro="Качество и поведение показа экрана для всех комнат, где он разрешён." /> },
    { id: "general", label: "Общие настройки", render: () => <SettingsForm key="general" group="general" title="Общие настройки" fields={generalFields} /> },
    { id: "audit", label: "Журнал аудита", render: () => <AuditAdmin /> },
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
        <div style={{ minWidth: 0 }}>{page?.render()}</div>
      </div>
    </section>
  );
}
