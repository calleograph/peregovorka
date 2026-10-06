import { useState } from "react";
import RoomsAdmin from "./admin/RoomsAdmin";
import SettingsForm, { type Field } from "./admin/SettingsForm";
import { AuditAdmin, MeetingsAdmin, RecordingsAdmin, SystemAdmin, UsersAdmin } from "./admin/Tables";

const storageFields: Field[] = [
  { name: "enabled", label: "Выгружать протоколы в хранилище", type: "bool", help: "При завершении встречи стенограмма и краткий протокол записываются по структуре «комната / дата, день недели / время начала»." },
  { name: "mode", label: "Тип хранилища", type: "select", options: [["local", "Локальный каталог на сервере"], ["smb", "Сетевой ресурс SMB"]] },
  { name: "local_path", label: "Каталог", type: "text", help: "Абсолютный путь внутри контейнера (по умолчанию /data/exports = $DATA_ROOT/exports).", showIf: (v) => v.mode === "local" },
  { name: "smb_server", label: "Сервер (имя или IP)", type: "text", showIf: (v) => v.mode === "smb" },
  { name: "smb_share", label: "Общий ресурс", type: "text", showIf: (v) => v.mode === "smb" },
  { name: "smb_base_path", label: "Подкаталог на ресурсе", type: "text", showIf: (v) => v.mode === "smb" },
  { name: "smb_domain", label: "Домен учётной записи", type: "text", showIf: (v) => v.mode === "smb" },
  { name: "smb_username", label: "Учётная запись (право записи на ресурс)", type: "text", showIf: (v) => v.mode === "smb" },
  { name: "smb_password", label: "Пароль", type: "secret", showIf: (v) => v.mode === "smb" },
  { name: "export_transcript", label: "Выгружать стенограмму (protocol.txt)", type: "bool" },
  { name: "export_summary", label: "Выгружать краткий протокол (summary.txt)", type: "bool" },
  { name: "export_audio", label: "Выгружать аудиозаписи (подкаталог audio/)", type: "bool", help: "Файлы могут быть большими; срок хранения на самом ресурсе приложение не контролирует." },
];

const anonFields: Field[] = [
  { name: "enabled", label: "Обезличивание включено", type: "bool", help: "Без него краткий протокол не создаётся: в LLM уходит только обезличенный текст (fail closed)." },
  { name: "profile", label: "Тип API", type: "select", options: [["docclean", "DocClean (api_text)"], ["generic", "Произвольный JSON API"]] },
  { name: "base_url", label: "Адрес сервиса (Base URL)", type: "text", help: "https://anon.corp.local — только https (http — по явному разрешению ниже)." },
  { name: "token", label: "Токен / ключ API", type: "secret" },
  { name: "docclean_mode", label: "Режим DocClean", type: "select", options: [["ai_ready", "ai_ready"], ["full", "full"], ["personal_corporate", "personal_corporate"], ["personal", "personal"]], showIf: (v) => v.profile === "docclean" },
  { name: "docclean_groups", label: "Группы данных (через запятую)", type: "text", help: "pdn, corporate, secrets, network, other; пусто — по умолчанию сервиса.", showIf: (v) => v.profile === "docclean" },
  { name: "endpoint", label: "Путь метода", type: "text", showIf: (v) => v.profile === "generic" },
  { name: "request_field", label: "Поле запроса (JSON-путь)", type: "text", showIf: (v) => v.profile === "generic" },
  { name: "response_field", label: "Поле ответа (JSON-путь)", type: "text", showIf: (v) => v.profile === "generic" },
  { name: "status_field", label: "Поле статуса (необязательно)", type: "text", showIf: (v) => v.profile === "generic" },
  { name: "status_ok_value", label: "Значение «успех»", type: "text", showIf: (v) => v.profile === "generic" },
  { name: "auth_type", label: "Авторизация", type: "select", options: [["bearer", "Bearer"], ["header", "Заголовок"], ["basic", "Basic"], ["none", "Нет"]], showIf: (v) => v.profile === "generic" },
  { name: "auth_header_name", label: "Имя заголовка", type: "text", showIf: (v) => v.profile === "generic" && v.auth_type === "header" },
  { name: "auth_username", label: "Пользователь (Basic)", type: "text", showIf: (v) => v.profile === "generic" && v.auth_type === "basic" },
  { name: "extra_body", label: "Доп. поля тела (JSON-объект)", type: "textarea", showIf: (v) => v.profile === "generic" },
  { name: "connect_timeout", label: "Таймаут соединения, с", type: "number", min: 1, max: 60 },
  { name: "timeout", label: "Таймаут ответа, с", type: "number", min: 1, max: 600 },
  { name: "max_chunk_chars", label: "Размер фрагмента, символов", type: "number", min: 500, max: 1000000 },
  { name: "use_corporate_ca", label: "Проверять сертификат по корпоративному CA (LDAP_CA_FILE)", type: "bool" },
  { name: "allow_http", label: "Разрешить http:// (небезопасно)", type: "bool" },
];

const llmFields: Field[] = [
  { name: "enabled", label: "LLM включена", type: "bool" },
  { name: "type", label: "Тип API", type: "select", options: [["openai_compatible", "OpenAI-совместимый (в т.ч. polza.ai, локальные)"], ["openai", "OpenAI"], ["anthropic", "Anthropic"]] },
  { name: "base_url", label: "Адрес API", type: "text", help: "Для OpenAI/Anthropic можно оставить пустым. Пример: https://api.polza.ai/api/v1" },
  { name: "model", label: "Модель", type: "text" },
  { name: "api_key", label: "Ключ API", type: "secret" },
  { name: "routing_provider", label: "Фиксировать провайдера маршрута (шлюзы)", type: "text", showIf: (v) => v.type === "openai_compatible" },
  { name: "max_tokens", label: "Максимум токенов ответа", type: "number", min: 64, max: 64000 },
  { name: "temperature", label: "Температура", type: "number", min: 0, max: 2 },
  { name: "timeout", label: "Таймаут, с", type: "number", min: 5, max: 1800 },
  { name: "use_corporate_ca", label: "Проверять сертификат по корпоративному CA", type: "bool" },
  { name: "allow_http", label: "Разрешить http:// (небезопасно)", type: "bool" },
];

const protocolFields: Field[] = [
  { name: "instructions", label: "Общие инструкции для краткого протокола", type: "textarea", help: "Инструкции конкретной комнаты (в настройках комнаты) добавляются к ним." },
  { name: "auto_generate", label: "Создавать краткий протокол автоматически по завершении встречи", type: "bool" },
  { name: "max_input_chars", label: "Максимум символов стенограммы за один запрос к LLM", type: "number", min: 2000, max: 1000000, help: "Длиннее — стенограмма обрабатывается по частям." },
];

const screenFields: Field[] = [
  { name: "profile", label: "Профиль трансляции экрана", type: "select", options: [
    ["sharp", "Чёткость — текст, слайды, код (1080p, 15 к/с)"], ["balanced", "Сбалансированный (1080p, 20 к/с)"], ["motion", "Плавность — видео, анимация (1080p, 30 к/с)"]],
    help: "Один клик у участника — «Показать экран». Профиль применяется ко всем комнатам, где разрешена демонстрация экрана." },
  { name: "share_audio", label: "Передавать звук вкладки/системы вместе с экраном", type: "bool", help: "Звук экрана в транскрибацию не попадает." },
  { name: "one_sharer_at_a_time", label: "Только один показывающий одновременно", type: "bool" },
];

const generalFields: Field[] = [
  { name: "timezone", label: "Часовой пояс", type: "text", help: "IANA-имя, например Europe/Moscow. Используется в именах папок и подписях времени протоколов." },
];

const TABS: [string, string][] = [
  ["rooms", "Переговорки"], ["meetings", "Встречи"], ["storage", "Хранилище"], ["anon", "Обезличивание"], ["llm", "LLM и протокол"],
  ["screen", "Экран"], ["users", "Пользователи"], ["recordings", "Записи"], ["audit", "Аудит"], ["system", "Система"],
];

export default function AdminPage({ version }: { version: string }) {
  const [tab, setTab] = useState(() => sessionStorage.getItem("adminTab") || "rooms");
  const pick = (t: string) => { setTab(t); try { sessionStorage.setItem("adminTab", t); } catch { /* ignore */ } };
  return (
    <section>
      <div className="row"><h1>Администрирование</h1><div className="spacer" /><span className="muted small">Версия: {version || "—"}</span></div>
      <div className="tabs" role="tablist">
        {TABS.map(([k, l]) => <button key={k} role="tab" aria-selected={tab === k} className={`tab ${tab === k ? "active" : ""}`} onClick={() => pick(k)}>{l}</button>)}
      </div>
      {tab === "rooms" && <RoomsAdmin />}
      {tab === "meetings" && <MeetingsAdmin />}
      {tab === "storage" && <SettingsForm key="storage" group="storage" title="Хранилище протоколов" fields={storageFields} testable
        intro="Куда складываются протоколы встреч. Для SMB укажите сервисную учётную запись с правом записи на ресурс — приложение подключается само, монтирование не требуется." />}
      {tab === "anon" && <SettingsForm key="anonymizer" group="anonymizer" title="API обезличивания" fields={anonFields} testable
        intro="Внутренний сервис обезличивания (DocClean или совместимый JSON API). Любой текст проходит через него перед отправкой в LLM." />}
      {tab === "llm" && (
        <>
          <SettingsForm key="llm" group="llm" title="LLM для краткого протокола" fields={llmFields} testable
            intro="Модель формирует краткий протокол, решения и поручения. Данные отправляются только после обезличивания." />
          <SettingsForm key="protocol" group="protocol" title="Инструкции и режим протокола" fields={protocolFields} />
        </>
      )}
      {tab === "screen" && <SettingsForm key="screen" group="screen" title="Трансляция экрана" fields={screenFields} />}
      {tab === "users" && <UsersAdmin />}
      {tab === "recordings" && <RecordingsAdmin />}
      {tab === "audit" && <AuditAdmin />}
      {tab === "system" && <><SystemAdmin /><div style={{ marginTop: 16 }}><SettingsForm key="general" group="general" title="Общие настройки" fields={generalFields} /></div></>}
    </section>
  );
}
