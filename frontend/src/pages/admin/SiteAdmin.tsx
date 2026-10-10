import { useCallback, useEffect, useRef, useState } from "react";
import { api, type ApiError, type LegalAdminDoc, type LegalRevisionRow } from "../../api";
import { Markdown } from "../../components/Markdown";
import { Brand } from "../../components/Brand";
import { ConfirmDialog } from "../../components/Dialogs";
import { loadSite, useSite } from "../../site";
import { fmt } from "../../util";
import type { Field } from "./SettingsForm";
import SettingsForm from "./SettingsForm";

const BRAND_FIELDS: Field[] = [
  { name: "name", label: "Название системы", type: "text", example: "Переговорка", help: "Показывается в верхней панели, на странице входа, во вкладке браузера и в письмах ({{project_name}})." },
  { name: "short_name", label: "Краткое название", type: "text", help: "Для узких экранов и компактной навигации. Пусто — как название." },
  { name: "subtitle", label: "Подзаголовок", type: "text", example: "Корпоративные видеоконференции" },
  { name: "description", label: "Описание системы", type: "textarea", rows: 2, help: "Для метаданных страницы." },
  { name: "primary_color", label: "Основной цвет", type: "text", placeholder: "#2456d3", example: "#1a56db", help: "Акцент кнопок и элементов интерфейса (вид #rrggbb). Пусто — стандартный. К цветным темам пользователя не применяется." },
  { name: "accent2_color", label: "Дополнительный цвет", type: "text", placeholder: "необязательно", help: "Второй цвет градиента кнопок. Пусто — вычисляется из основного." },
  { name: "theme", label: "Тема по умолчанию", type: "select", options: [["system", "Как в системе пользователя"], ["light", "Светлая"], ["dark", "Тёмная"]], help: "Пользователь может выбрать свою тему в личном кабинете — его выбор важнее." },
];
const ORG_FIELDS: Field[] = [
  { name: "org_full", label: "Полное название организации", type: "text", section: "Организация", help: "Все поля необязательны: организация сама решает, что показывать пользователям." },
  { name: "org_short", label: "Краткое название организации", type: "text", section: "Организация", help: "Показывается в подвале вместо названия системы." },
  { name: "org_url", label: "Адрес корпоративного сайта", type: "text", section: "Организация", placeholder: "https://example.com" },
  { name: "org_unit", label: "Подразделение, предоставляющее сервис", type: "text", section: "Организация" },
  { name: "org_legal_name", label: "Юридическое наименование", type: "text", section: "Организация", help: "Показывается в окне «О системе»." },
  { name: "org_address", label: "Почтовый или юридический адрес", type: "textarea", rows: 2, section: "Организация" },
  { name: "footer_text", label: "Дополнительный текст нижнего колонтитула", type: "text", section: "Организация" },
  { name: "welcome_text", label: "Приветственное сообщение", type: "textarea", rows: 2, section: "Тексты для пользователей", help: "На странице входа." },
  { name: "guest_text", label: "Сообщение для гостей", type: "textarea", rows: 3, section: "Тексты для пользователей", help: "Перед подключением по гостевой ссылке: кто организовал встречу, правила, уведомление о возможной записи." },
  { name: "recording_text", label: "Информация о записи и транскрипции", type: "textarea", rows: 3, section: "Тексты для пользователей", help: "Справочный текст на экране перед входом. Он не заменяет индикаторы записи и транскрипции — те показывают фактическое состояние." },
];
const SUPPORT_FIELDS: Field[] = [
  { name: "support_mode", label: "Кому показывать контакты", type: "select", options: [["all", "Всем пользователям"], ["auth", "Только вошедшим"], ["off", "Не показывать"]] },
  { name: "support_email", label: "Электронная почта поддержки", type: "text", help: "Только для связи с людьми. Это НЕ адрес отправителя почты системы — тот задаётся в разделе «Исходящая почта»." },
  { name: "support_phone", label: "Телефон поддержки", type: "text", placeholder: "+7 (000) 000-00-00" },
  { name: "support_url", label: "Service Desk или система заявок", type: "text", placeholder: "https://" },
  { name: "portal_url", label: "Корпоративный портал", type: "text", placeholder: "https://" },
  { name: "support_text", label: "Инструкция обращения в поддержку", type: "textarea", rows: 4 },
];

const SLOTS: { kind: "logo" | "logo_compact" | "favicon"; label: string; help: string; accept: string }[] = [
  { kind: "logo", label: "Логотип", help: "Верхняя панель, вход. PNG, WebP или JPEG до 2 МБ; вписывается в 480×160 без искажения.", accept: "image/png,image/webp,image/jpeg" },
  { kind: "logo_compact", label: "Компактный логотип", help: "Узкие экраны и панели; вписывается в 128×128.", accept: "image/png,image/webp,image/jpeg" },
  { kind: "favicon", label: "Значок вкладки (favicon)", help: "PNG, WebP или ICO; будет приведён к 64×64.", accept: "image/png,image/webp,image/x-icon,.ico" },
];

/** Выбор изображения: предпросмотр на светлом и тёмном фоне до сохранения, безопасная обработка на сервере, возможность удалить и вернуть стандартное. */
function ImageSlot({ slot, current, onChanged }: { slot: (typeof SLOTS)[number]; current: string | null; onChanged: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => { if (!file) { setPreview(""); return; } const u = URL.createObjectURL(file); setPreview(u); return () => URL.revokeObjectURL(u); }, [file]);
  const shown = preview || current;
  const run = async (fn: () => Promise<unknown>, ok: string) => {
    setBusy(true); setMsg(null);
    try { await fn(); setFile(null); setMsg({ ok: true, text: ok }); await loadSite(); onChanged(); }
    catch (e) { setMsg({ ok: false, text: (e as ApiError).message }); }
    finally { setBusy(false); }
  };
  return (
    <div className="site-slot">
      <b>{slot.label}</b>
      <div className="site-plates">
        <div className="plate light">{shown ? <img src={shown} alt="" /> : <span className="muted small">стандартный</span>}</div>
        <div className="plate dark">{shown ? <img src={shown} alt="" /> : <span className="muted small">стандартный</span>}</div>
      </div>
      <span className="muted small">{slot.help}</span>
      <div className="row">
        <input ref={input} type="file" accept={slot.accept} hidden onChange={(e) => { setFile(e.target.files?.[0] ?? null); setMsg(null); e.target.value = ""; }} />
        <button type="button" className="btn mini" onClick={() => input.current?.click()} disabled={busy}>{current ? "Заменить…" : "Выбрать файл…"}</button>
        {file && <button type="button" className="btn mini primary" disabled={busy} onClick={() => void run(() => api.admin.site.uploadAsset(slot.kind, file), "Изображение сохранено")}>Сохранить</button>}
        {file && <button type="button" className="btn mini ghost" onClick={() => setFile(null)}>Отмена</button>}
        {!file && current && <button type="button" className="btn mini ghost danger" disabled={busy} onClick={() => void run(() => api.admin.site.deleteAsset(slot.kind), "Возвращено стандартное")}>Удалить</button>}
      </div>
      {msg && <div className={`alert ${msg.ok ? "ok" : "error"}`} role="status">{msg.text}</div>}
    </div>
  );
}

function BrandTab() {
  const site = useSite();
  const [k, setK] = useState(0);
  const [confirm, setConfirm] = useState<string | null>(null);
  return (
    <>
      <SettingsForm key={`brand${k}`} group="site" title="Название и оформление" fields={BRAND_FIELDS} onSaved={() => void loadSite()}
        intro="Применяется сразу во всех разделах, без пересборки и перезапуска. Настройки хранятся в базе и сохраняются при обновлениях." />
      <div className="card">
        <h2>Изображения</h2>
        <div className="site-slots">{SLOTS.map((s) => <ImageSlot key={s.kind} slot={s} current={site.assets[s.kind]} onChanged={() => setK((x) => x + 1)} />)}</div>
        <p className="muted small">Файлы перекодируются в PNG, метаданные и активное содержимое удаляются; SVG и анимация не принимаются. Адрес изображения содержит версию, поэтому после замены браузер не показывает старое.</p>
      </div>
      <div className="row"><button className="btn ghost danger" onClick={() => setConfirm("brand")}>Вернуть стандартное оформление…</button></div>
      {confirm && (
        <ConfirmDialog title="Вернуть стандартное оформление?" confirmLabel="Вернуть" onClose={() => setConfirm(null)} body={<p>Название, цвета, тема и загруженные изображения будут сброшены к стандартным значениям Peregovorka. Контакты, сведения об организации и документы не изменятся.</p>}
                       onConfirm={async () => { await api.admin.site.reset("brand"); await loadSite(); setK((x) => x + 1); }} />
      )}
    </>
  );
}

function GroupTab({ title, fields, intro, group }: { title: string; fields: Field[]; intro: string; group: "org" | "contacts" }) {
  const [k, setK] = useState(0);
  const [confirm, setConfirm] = useState(false);
  return (
    <>
      <SettingsForm key={`${group}${k}`} group="site" title={title} fields={fields} intro={intro} onSaved={() => void loadSite()} />
      <div className="row"><button className="btn ghost danger" onClick={() => setConfirm(true)}>Очистить значения этого раздела…</button></div>
      {confirm && <ConfirmDialog title="Очистить значения?" confirmLabel="Очистить" onClose={() => setConfirm(false)} body={<p>Поля этого раздела вернутся к значениям по умолчанию (пустым).</p>}
                                 onConfirm={async () => { await api.admin.site.reset(group === "org" ? "org" : "contacts"); if (group === "org") await api.admin.site.reset("texts"); await loadSite(); setK((x) => x + 1); }} />}
    </>
  );
}

function LegalTab() {
  const [docs, setDocs] = useState<LegalAdminDoc[]>([]);
  const [kind, setKind] = useState("privacy_policy");
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [consent, setConsent] = useState(false);
  const [view, setView] = useState<"edit" | "preview" | "history" | "consents">("edit");
  const [revs, setRevs] = useState<LegalRevisionRow[]>([]);
  const [cons, setCons] = useState<{ total: number; items: { subject_type: string; subject_name: string | null; version: number; accepted_at: string }[] } | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const file = useRef<HTMLInputElement>(null);
  const doc = docs.find((d) => d.kind === kind);

  const reload = useCallback(async () => { const r = await api.admin.site.legal(); setDocs(r.items); return r.items; }, []);
  useEffect(() => { void reload().catch((e) => setMsg({ ok: false, text: (e as ApiError).message })); }, [reload]);
  useEffect(() => { if (doc) { setTitle(doc.title); setText(doc.draft_md); setConsent(doc.require_consent); setView("edit"); setMsg(null); } }, [kind, docs.length]);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (view === "history") void api.admin.site.revisions(kind).then((r) => setRevs(r.items));
    if (view === "consents") void api.admin.site.consents(kind).then(setCons);
  }, [view, kind]);

  const run = async (fn: () => Promise<LegalAdminDoc>, ok: string) => {
    setBusy(true); setMsg(null);
    try { const d = await fn(); setDocs((all) => all.map((x) => (x.kind === d.kind ? d : x))); setMsg({ ok: true, text: ok }); await loadSite(); }
    catch (e) { setMsg({ ok: false, text: (e as ApiError).message }); }
    finally { setBusy(false); }
  };
  const save = () => api.admin.site.saveLegal(kind, { title, draft_md: text, require_consent: consent });
  const onFile = (f: File | undefined) => {
    if (!f) return;
    if (f.size > 200_000) { setMsg({ ok: false, text: "Файл больше 200 КБ." }); return; }
    void f.text().then((t) => { setText(t.replace(/\r\n/g, "\n")); if (!title.trim()) setTitle(f.name.replace(/\.md$/i, "")); setMsg({ ok: true, text: "Текст загружен в редактор — сохраните черновик." }); });
  };
  if (!doc) return <div className="muted">Загрузка…</div>;
  const dirty = text !== doc.draft_md || title !== doc.title || consent !== doc.require_consent;
  return (
    <div className="card form">
      <h2>Юридические документы организации</h2>
      <p className="muted">Тексты, их публикацию и необходимость подтверждения определяет организация: продукт не содержит юридических формулировок и не заявляет соответствия законодательству. Каждый документ необязателен. Показ — безопасный Markdown, без HTML.</p>
      <div className="tabs" role="tablist">{docs.map((d) => <button key={d.kind} role="tab" aria-selected={d.kind === kind} className={`tab ${d.kind === kind ? "active" : ""}`} onClick={() => setKind(d.kind)}>{d.label}{d.published ? " ●" : ""}</button>)}</div>
      <p className="small">{doc.published ? <><span className="badge ok">опубликован</span> редакция {doc.version} от {doc.published_at ? fmt(doc.published_at) : "—"}</> : <span className="badge">{doc.version ? `снят с публикации (последняя редакция ${doc.version})` : "не опубликован"}</span>}
        {doc.has_changes && doc.published && <span className="badge warn" style={{ marginLeft: 8 }}>есть неопубликованные правки</span>}
        <span className="muted"> · изменён {fmt(doc.updated_at)}{doc.updated_by ? `, ${doc.updated_by}` : ""}</span></p>
      <div className="row"><button type="button" className={`btn mini ${view === "edit" ? "primary" : ""}`} onClick={() => setView("edit")}>Редактор</button>
        <button type="button" className={`btn mini ${view === "preview" ? "primary" : ""}`} onClick={() => setView("preview")}>Предпросмотр</button>
        <button type="button" className={`btn mini ${view === "history" ? "primary" : ""}`} onClick={() => setView("history")}>История редакций</button>
        <button type="button" className={`btn mini ${view === "consents" ? "primary" : ""}`} onClick={() => setView("consents")}>Подтверждения</button></div>
      {view === "edit" && (
        <>
          <label>Название документа<input value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} /></label>
          <label>Текст (Markdown)<textarea className="md-editor" rows={14} value={text} maxLength={200000} onChange={(e) => setText(e.target.value)} spellCheck /></label>
          <div className="row">
            <input ref={file} type="file" accept=".md,text/markdown,text/plain" hidden onChange={(e) => { onFile(e.target.files?.[0]); e.target.value = ""; }} />
            <button type="button" className="btn mini" onClick={() => file.current?.click()}>Загрузить файл .md…</button>
          </div>
          <label className="check"><input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
            <span className="check-body">Требовать подтверждение при входе<span className="help">Сотрудник после входа, а гость перед подключением явно подтверждает ознакомление. Подтверждение хранится вместе с версией документа; при публикации новой редакции запрашивается заново.</span></span></label>
        </>
      )}
      {view === "preview" && <div className="card legal-card"><h1>{title || doc.label}</h1><Markdown source={text} /></div>}
      {view === "history" && (revs.length === 0 ? <p className="muted">Опубликованных редакций пока не было.</p> : revs.map((r) => (
        <details key={r.version} className="card"><summary><b>Редакция {r.version}</b> · {fmt(r.published_at)}{r.published_by ? `, ${r.published_by}` : ""}{r.unpublished_at ? ` · действовала до ${fmt(r.unpublished_at)}` : " · действует"}</summary><Markdown source={r.content_md} /></details>)))}
      {view === "consents" && (!cons ? <p className="muted">Загрузка…</p> : (
        <>
          <p>Всего подтверждений: <b>{cons.total}</b>. Последние:</p>
          <table className="table compact"><thead><tr><th>Когда</th><th>Кто</th><th>Тип</th><th>Редакция</th></tr></thead><tbody>
            {cons.items.length === 0 && <tr><td colSpan={4} className="muted">Подтверждений пока нет.</td></tr>}
            {cons.items.slice(0, 50).map((c, i) => <tr key={i}><td>{fmt(c.accepted_at)}</td><td>{c.subject_name ?? "—"}</td><td>{c.subject_type === "guest" ? "гость" : "сотрудник"}</td><td>{c.version}</td></tr>)}
          </tbody></table>
        </>
      ))}
      {view === "edit" && (
        <div className="row">
          <button type="button" className="btn" disabled={busy || !dirty} onClick={() => void run(save, "Черновик сохранён")}>Сохранить черновик</button>
          <button type="button" className="btn primary" disabled={busy || !text.trim()} onClick={() => void run(async () => { await save(); return api.admin.site.publish(kind); }, "Опубликовано новой редакцией")}>Опубликовать</button>
          {doc.published && <button type="button" className="btn ghost danger" disabled={busy} onClick={() => void run(() => api.admin.site.unpublish(kind), "Снят с публикации")}>Снять с публикации</button>}
        </div>
      )}
      {msg && <div className={`alert ${msg.ok ? "ok" : "error"}`} role="status">{msg.text}</div>}
    </div>
  );
}

function PreviewTab() {
  const site = useSite();
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [confirm, setConfirm] = useState<File | null>(null);
  const pick = useRef<HTMLInputElement>(null);
  return (
    <div className="card form">
      <h2>Предварительный просмотр</h2>
      <p className="muted">Так выглядят сохранённые настройки. Верхняя панель, страница входа и подвал используют одни и те же данные.</p>
      <div className="site-preview">
        <div className="pv-top"><span className="brand"><Brand /></span><span className="spacer" /><span className="muted small">Переговорки · История</span></div>
        <div className="pv-login">
          <div className="pv-brand">{site.assets.logo ? <img src={site.assets.logo} alt="" /> : <b>{site.name}</b>}<small>{site.subtitle}</small></div>
          <div className="pv-form"><span>Логин</span><i /><span>Пароль</span><i /><button type="button" className="btn primary" tabIndex={-1}>Войти</button></div>
        </div>
        <div className="pv-foot">{site.org.short || site.name}{site.documents.map((d) => ` · ${d.title}`).join("")}{site.support ? " · Помощь и поддержка" : ""}{site.footer_text ? ` · ${site.footer_text}` : ""} · Peregovorka (open source)</div>
      </div>
      <h3>Резервная копия оформления</h3>
      <p className="muted small">Архив содержит настройки сайта, документы с редакциями и изображения. Секретов и подтверждений пользователей в нём нет.</p>
      <div className="row">
        <a className="btn" href="/api/v1/admin/site/export" download>Скачать архив</a>
        <input ref={pick} type="file" accept=".zip,application/zip" hidden onChange={(e) => { setConfirm(e.target.files?.[0] ?? null); e.target.value = ""; }} />
        <button type="button" className="btn" onClick={() => pick.current?.click()}>Загрузить архив…</button>
      </div>
      {msg && <div className={`alert ${msg.ok ? "ok" : "error"}`} role="status">{msg.text}</div>}
      {confirm && <ConfirmDialog title="Применить архив оформления?" confirmLabel="Применить" onClose={() => setConfirm(null)}
        body={<p>Текущие настройки сайта, изображения и документы будут заменены содержимым файла «{confirm.name}». Подтверждения пользователей сохранятся.</p>}
        onConfirm={async () => { try { await api.admin.site.importZip(confirm); await loadSite(); setMsg({ ok: true, text: "Архив применён" }); } catch (e) { setMsg({ ok: false, text: (e as ApiError).message }); throw e; } }} />}
    </div>
  );
}

const TABS = [
  { id: "brand", label: "Название и оформление" },
  { id: "org", label: "Информация об организации" },
  { id: "support", label: "Контакты и поддержка" },
  { id: "legal", label: "Юридические документы" },
  { id: "preview", label: "Предварительный просмотр" },
] as const;

/** Администрирование → Настройки сайта: оформление установки без изменения исходного кода; всё хранится в базе и каталоге данных и переживает обновления. */
export default function SiteAdmin() {
  const [tab, setTab] = useState<(typeof TABS)[number]["id"]>("brand");
  return (
    <section>
      <h2>Настройки сайта</h2>
      <div className="tabs" role="tablist">{TABS.map((t) => <button key={t.id} role="tab" aria-selected={tab === t.id} className={`tab ${tab === t.id ? "active" : ""}`} onClick={() => setTab(t.id)}>{t.label}</button>)}</div>
      {tab === "brand" && <BrandTab />}
      {tab === "org" && <GroupTab group="org" title="Информация об организации и тексты" fields={ORG_FIELDS} intro="Необязательные сведения и тексты для пользователей. Реальные данные вашей организации хранятся только в вашей установке." />}
      {tab === "support" && <GroupTab group="contacts" title="Контакты и техническая поддержка" fields={SUPPORT_FIELDS} intro="Пункт «Помощь и поддержка» показывает заполненные поля; пустые не отображаются." />}
      {tab === "legal" && <LegalTab />}
      {tab === "preview" && <PreviewTab />}
    </section>
  );
}
