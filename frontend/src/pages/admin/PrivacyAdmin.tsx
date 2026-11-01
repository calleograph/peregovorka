import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { api, type ApiError, type PrivacyAction, type PrivacyCatalog, type PrivacyLabResult, type PrivacyProfile, type SettingsValues } from "../../api";

type Draft = Record<string, unknown>;
import InfoTip from "../../components/InfoTip";
import { useFocusOnMount } from "../../useInitialFocus";
import { mergeTerms, parseTermsCsv, type Term, termsToCsv } from "../../termsCsv";
import { addException, type Finding, labFindings } from "../../labFindings";

const TABS = [["general", "Общие"], ["categories", "Категории"], ["rules", "Правила"], ["people", "Участники и имена"], ["dict", "Словари и исключения"],
  ["profiles", "Профили"], ["lab", "Тестирование"]] as const;
type Tab = (typeof TABS)[number][0];
const ACTION_RU: Record<PrivacyAction, string> = { keep: "оставить", mask: "метка без номера", pseudonym: "обозначение с номером" };
const PROFILES: PrivacyProfile[] = ["basic", "strict", "custom"];
const TERM_CATS: [string, string][] = [["project", "Проект или внутренняя система"], ["organization", "Организация, клиент"], ["custom", "Свой термин"], ["host", "Сервер"], ["person", "Человек"], ["location", "Место"]];
const SAMPLE = `Протокол совещания по переносу CRM
Присутствовали: Иванов Иван Петрович, Петрова Мария Сергеевна, Кузнецов Сергей Андреевич.

1. Иван Петрович доложил о сбоях на сервере crm-app-02.corp.example.local (192.0.2.17) и предложил перенос.
2. Мария Сергеевна сообщила, что бюджет — 2,4 млн руб., договор № ДП-2026/118 с ООО «СтройИнвест» подписан.
3. Кузнецов напомнил: пароль администратора стенда P@ssw0rd-Crm-2026 нужно сменить.

Поручения:
- Петровой — согласовать оплату, срок 1 ноября.
- Кузнецову С. А. — перенести CRM, почта для связи s.kuznetsov@example.org, тел. +7 912 345-67-89.`;
const SAMPLE_PEOPLE = "Иванов Иван Петрович\nПетрова Мария Сергеевна\nКузнецов Сергей Андреевич";

/** «Администрирование → Обезличивание»: встроенная подсистема на этом сервере. Пароли и ключи скрываются всегда — здесь настраивается остальное. */
export default function PrivacyAdmin() {
  const [tab, setTab] = useState<Tab>(() => { try { const t = sessionStorage.getItem("privacyTab"); return TABS.some(([k]) => k === t) ? (t as Tab) : "general"; } catch { return "general"; } });
  const pick = (t: Tab) => { setTab(t); try { sessionStorage.setItem("privacyTab", t); } catch { /* не запомнится */ } };
  const [cat, setCat] = useState<PrivacyCatalog | null>(null);
  const [saved, setSaved] = useState<Draft | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [err, setErr] = useState("");
  useEffect(() => {
    api.admin.privacy.catalog().then(setCat).catch((e) => setErr((e as ApiError).message));
    api.admin.settings("anonymizer").then((s) => { setSaved(s); setDraft(s); }).catch((e) => setErr((e as ApiError).message));
  }, []);
  const dirty = useMemo(() => JSON.stringify(saved) !== JSON.stringify(draft), [saved, draft]);
  const set = (k: string, v: unknown) => setDraft((d) => (d ? { ...d, [k]: v as never } : d));
  const save = async () => {
    if (!draft) return;
    setBusy(true); setErr(""); setNote("");
    try { const s = await api.admin.saveSettings("anonymizer", draft as unknown as SettingsValues); setSaved(s); setDraft(s); setNote("Сохранено. Изменение записано в журнал аудита."); }
    catch (e) { setErr((e as ApiError).message); } finally { setBusy(false); }
  };
  if (!cat || !draft) return <section><h2>Обезличивание</h2>{err ? <div className="alert error">{err}</div> : <p className="muted">Загрузка…</p>}</section>;
  const allowed = (draft.allowed_profiles as PrivacyProfile[]) ?? PROFILES;
  const custom = { ...cat.profiles.basic.actions, ...((draft.custom_actions as Record<string, PrivacyAction>) ?? {}) };
  return (
    <section className="privacy-admin">
      <h2 style={{ marginBottom: 6 }}>Обезличивание</h2>
      <p className="muted small" style={{ marginTop: 0 }}>Перед отправкой в языковую модель материалы встреч обрабатываются на этом сервере: людей, серверы, контакты, реквизиты и суммы
        заменяют обозначения. <b>Пароли, токены и ключи скрываются всегда</b> — при любых настройках и даже в комнатах без обезличивания.</p>
      {!cat.nlp.ready && <div className="alert error" role="alert">{cat.nlp.note}</div>}
      <div className="tabs" role="tablist" aria-label="Разделы обезличивания">
        {TABS.map(([k, label]) => <button key={k} role="tab" aria-selected={tab === k} className={`tab ${tab === k ? "active" : ""}`} onClick={() => pick(k)}>{label}</button>)}
      </div>

      {tab === "general" && (
        <div role="tabpanel">
          <label className="check"><input type="checkbox" checked={!!draft.enabled} onChange={(e) => set("enabled", e.target.checked)} />
            <span className="check-body">Руководители комнат могут включать обезличивание<InfoTip>По умолчанию в комнате оно выключено; включает его руководитель комнаты в её настройках (вкладка «Встреча и запись» → «Обезличивание»).</InfoTip></span></label>
          <label className="check"><input type="checkbox" checked={!!draft.required} onChange={(e) => set("required", e.target.checked)} />
            <span className="check-body">Обязательно для всех комнат<InfoTip>Комната не сможет его выключить. Если обезличивание не выполнится (например, не установлены модели), документ не формируется и текст в модель не отправляется.</InfoTip></span></label>
          <div className="cols">
            <label>Профиль по умолчанию
              <select value={String(draft.default_profile)} onChange={(e) => set("default_profile", e.target.value)}>
                {allowed.map((p) => <option key={p} value={p}>{cat.profile_titles[p]}</option>)}
              </select></label>
            <fieldset className="group"><legend>Профили, которые может выбрать комната</legend>
              {PROFILES.map((p) => <label key={p} className="check"><input type="checkbox" checked={allowed.includes(p)}
                onChange={(e) => set("allowed_profiles", e.target.checked ? [...allowed, p] : allowed.filter((x) => x !== p))} /> {cat.profile_titles[p]}</label>)}
            </fieldset>
          </div>
          <div className="cols">
            <label>Лаборатория: наибольший файл, МБ<input type="number" min={1} max={100} value={Number(draft.lab_max_mb)} onChange={(e) => set("lab_max_mb", Number(e.target.value))} /></label>
            <label>Лаборатория: наибольший объём текста, знаков<input type="number" min={1000} max={2000000} step={1000} value={Number(draft.lab_max_chars)} onChange={(e) => set("lab_max_chars", Number(e.target.value))} /></label>
          </div>
          <p className="muted small">Распознавание: {cat.nlp.engine}. Замены согласованы в пределах одной встречи и нигде не сохраняются; в журналы попадают только категории и число замен.</p>
        </div>
      )}

      {tab === "categories" && (
        <div role="tabpanel" className="table-scroll">
          <table className="table">
            <thead><tr><th>Категория</th><th>Базовый</th><th>Строгий</th><th>Настраиваемый</th></tr></thead>
            <tbody>{cat.categories.map((c) => (
              <tr key={c.id}><td>{c.title}{c.mandatory && <span className="badge warn"> всегда</span>}</td>
                {c.mandatory ? <td colSpan={3}>скрывается во всех профилях и комнатах: «[секрет удалён]»</td> : <>
                  <td>{ACTION_RU[cat.profiles.basic.actions[c.id]] ?? "оставить"}</td><td>{ACTION_RU[cat.profiles.strict.actions[c.id]] ?? "оставить"}</td>
                  <td>{ACTION_RU[custom[c.id]] ?? "оставить"}</td></>}
              </tr>))}</tbody>
          </table>
          <p className="muted small">«Метка без номера» — например «[телефон]»; «обозначение с номером» — «[сервер 2]», «Организация 1»: один и тот же объект обозначается одинаково во всём документе.</p>
        </div>
      )}

      {tab === "rules" && (
        <div role="tabpanel">
          <div className="table-scroll"><table className="table">
            <thead><tr><th>Правило</th><th>Категория</th></tr></thead>
            <tbody>{cat.rules.map((r) => <tr key={`${r.id}-${r.category}`}><td>{r.title}</td><td>{cat.categories.find((c) => c.id === r.category)?.title ?? r.category}</td></tr>)}</tbody>
          </table></div>
          <h3 className="field-title">Секреты (обязательная защита)</h3>
          <p className="muted small">Точные форматы ключей и токенов провайдеров, закрытые ключи, строки подключения к базам, заголовки авторизации, ключевые слова «пароль», «токен»,
            «password=» рядом со значением и случайные на вид строки рядом с «временный ключ», «одноразовый код». Правил: {cat.secret_rules.length}.</p>
          <p className="muted small">Обычные слова с заглавной буквы («Вера», «Надежда», «Роман»), даты, количества и номера версий не заменяются; общеизвестные сайты (github.com, example.org) — тоже, пока они не указаны как внутренние.</p>
        </div>
      )}

      {tab === "people" && (
        <div role="tabpanel">
          <p>Участники встречи известны заранее: их имена распознаются во всех падежах и формах («Иванову», «с Ивановым», «И. П. Иванов», «Иван Петрович») и обозначаются одинаково
            во всём протоколе. Разные люди с одинаковым именем не объединяются; одно имя без фамилии при двух тёзках остаётся как есть.</p>
          <table className="table"><tbody>
            <tr><td>Базовый профиль</td><td>имя и первая буква фамилии: «Присутствовали: Иван И., Мария П.», «поручено Марии П.»</td></tr>
            <tr><td>Строгий профиль</td><td>«Участник 1», «Участнику 2 поручено»; люди не из списка участников — «Лицо 4»</td></tr>
          </tbody></table>
          <label>Настраиваемый профиль: как обозначать людей
            <select value={String(draft.custom_person_format)} onChange={(e) => set("custom_person_format", e.target.value)}>
              <option value="short">Имя и инициал фамилии («Иван И.»)</option><option value="role">Номер участника («Участник 2»)</option>
            </select></label>
        </div>
      )}

      {tab === "dict" && <Dictionaries draft={draft} set={set} />}

      {tab === "profiles" && (
        <div role="tabpanel">
          <p className="muted small">Базовый и строгий профили встроены; «Настраиваемый» начинается с действий базового — здесь можно изменить любую категорию.</p>
          <label>Организации в настраиваемом профиле
            <select value={String(draft.custom_org_scope)} onChange={(e) => set("custom_org_scope", e.target.value)}>
              <option value="legal">только с правовой формой (ООО «…») и из словаря</option><option value="all">все найденные в тексте</option>
            </select></label>
          <div className="table-scroll"><table className="table">
            <thead><tr><th>Категория</th><th>Настраиваемый профиль</th></tr></thead>
            <tbody>{cat.categories.filter((c) => !c.mandatory).map((c) => (
              <tr key={c.id}><td>{c.title}</td><td>
                <select value={custom[c.id] ?? "keep"} aria-label={`${c.title}: действие`}
                  onChange={(e) => set("custom_actions", { ...((draft.custom_actions as Record<string, string>) ?? {}), [c.id]: e.target.value })}>
                  {cat.actions.map((a) => <option key={a} value={a}>{ACTION_RU[a]}</option>)}
                </select></td></tr>))}</tbody>
          </table></div>
        </div>
      )}

      {tab === "lab" && <Lab cat={cat} draft={draft} dirty={dirty} allowed={allowed} set={set} />}

      {tab !== "lab" && <div className="row form-actions">
        <button className="btn primary" onClick={save} disabled={busy || !dirty}>{busy ? "Сохранение…" : "Сохранить"}</button>
        {dirty && <button className="btn ghost" onClick={() => setDraft(saved)}>Отменить изменения</button>}
      </div>}
      {note && <div className="alert ok" role="status">{note}</div>}
      {err && <div className="alert error" role="alert">{err}</div>}
    </section>
  );
}

function Dictionaries({ draft, set }: { draft: Draft; set: (k: string, v: unknown) => void }) {
  const terms = (draft.terms as Term[]) ?? [];
  const lines = (k: string) => ((draft[k] as string[]) ?? []).join("\n");
  const upd = (i: number, k: string, v: string) => set("terms", terms.map((t, j) => (j === i ? { ...t, [k]: v } : t)));
  const [csvNote, setCsvNote] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const importCsv = async (file: File | undefined) => {
    if (!file) return;
    if (file.size > 1024 * 1024) { setCsvNote("Файл больше 1 МБ — не загружен."); return; }
    const { terms: added, skipped } = parseTermsCsv(await file.text());
    const merged = mergeTerms(terms, added);
    set("terms", merged.terms);
    setCsvNote(`Добавлено терминов: ${merged.added}` + (added.length - merged.added ? `, уже были: ${added.length - merged.added}` : "") + (skipped ? `, пропущено строк: ${skipped}` : "") + ". Сохраните изменения.");
  };
  const exportCsv = () => {
    const url = URL.createObjectURL(new Blob(["﻿" + termsToCsv(terms)], { type: "text/csv;charset=utf-8" }));
    const a = document.createElement("a");
    a.href = url; a.download = "slovar-obezlichivaniya.csv"; a.rel = "noopener";
    document.body.appendChild(a); a.click(); a.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 30000);
  };
  return (
    <div role="tabpanel">
      <h3 className="field-title">Словарь<InfoTip>Клиенты, проекты, внутренние системы, свои термины. Узнаются в любой форме слова («Альфа», «по Альфе»). Замена — своя или обозначение категории («Проект 1»).</InfoTip></h3>
      <div className="table-scroll"><table className="table">
        <thead><tr><th>Термин</th><th>Категория</th><th>Своя замена</th><th /></tr></thead>
        <tbody>{terms.map((t, i) => (
          <tr key={i}>
            <td><input value={t.term} onChange={(e) => upd(i, "term", e.target.value)} aria-label="Термин" maxLength={200} /></td>
            <td><select value={t.category} onChange={(e) => upd(i, "category", e.target.value)} aria-label="Категория">{TERM_CATS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></td>
            <td><input value={t.replacement} onChange={(e) => upd(i, "replacement", e.target.value)} placeholder="по категории" aria-label="Своя замена" maxLength={100} /></td>
            <td><button type="button" className="btn mini ghost danger" onClick={() => set("terms", terms.filter((_, j) => j !== i))}>Удалить</button></td>
          </tr>))}</tbody>
      </table></div>
      <div className="row">
        <button type="button" className="btn mini" onClick={() => set("terms", [...terms, { term: "", category: "project", replacement: "" }])}>＋ Термин</button>
        <button type="button" className="btn mini ghost" onClick={() => fileRef.current?.click()}>Загрузить CSV</button>
        <input ref={fileRef} type="file" accept=".csv,text/csv" hidden onChange={(e) => { void importCsv(e.target.files?.[0]); e.target.value = ""; }} />
        <button type="button" className="btn mini ghost" onClick={exportCsv} disabled={!terms.length}>Выгрузить CSV</button>
        <InfoTip>Столбцы «термин, категория, замена». Подходит и выгрузка прежнего обезличивателя документов («значение, метка»): метка станет своей заменой. Повторы не добавляются.</InfoTip>
      </div>
      {csvNote && <p className="help" role="status">{csvNote}</p>}
      <div className="cols" style={{ marginTop: 12 }}>
        <label>Исключения — не заменять никогда (кроме паролей и ключей), по строке на термин
          <textarea rows={6} value={lines("exceptions")} onChange={(e) => set("exceptions", e.target.value.split("\n"))} placeholder="Например: название своей компании, общеизвестные продукты" /></label>
        <label>Внутренние домены — адреса и серверы в них заменяются
          <textarea rows={6} value={lines("internal_domains")} onChange={(e) => set("internal_domains", e.target.value.split("\n"))} placeholder="corp.example.org" />
          <span className="help">Домены *.local, *.corp, *.lan, *.internal, *.test и имена с внутренней частью (db.corp.…) считаются внутренними всегда.</span></label>
      </div>
    </div>
  );
}

function Lab({ cat, draft, dirty, allowed, set }: { cat: PrivacyCatalog; draft: Draft; dirty: boolean; allowed: PrivacyProfile[]; set: (k: string, v: unknown) => void }) {
  const focusOnMount = useFocusOnMount<HTMLTextAreaElement>();
  const [text, setText] = useState("");
  const [people, setPeople] = useState("");
  const [profile, setProfile] = useState<PrivacyProfile>((draft.default_profile as PrivacyProfile) ?? "basic");
  const [useDraft, setUseDraft] = useState(true);
  const [file, setFile] = useState<File | null>(null);
  const [res, setRes] = useState<PrivacyLabResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [exNote, setExNote] = useState("");
  const catTitle = (id: string) => cat.categories.find((c) => c.id === id)?.title ?? id;
  const participants = () => people.split("\n").map((s) => s.trim()).filter(Boolean);
  const run = async () => {
    setBusy(true); setErr(""); setExNote("");
    try {
      setRes(file ? await api.admin.privacy.labFile(file, profile, participants())
        : await api.admin.privacy.lab({ text, profile, participants: participants(), ...(useDraft && dirty ? { draft } : {}) }));
    } catch (e) { setErr((e as ApiError).message); setRes(null); } finally { setBusy(false); }
  };
  // ошибочное срабатывание → исключение в черновике настроек и сразу повторная проверка того же текста (в том числе извлечённого из файла)
  const except = async (original: string) => {
    if (!res) return;
    const next = { ...draft, exceptions: addException(draft.exceptions, original) };
    set("exceptions", next.exceptions);
    setBusy(true); setErr("");
    try {
      setRes({ ...(await api.admin.privacy.lab({ text: res.source, profile, participants: participants(), draft: next })), info: res.info });
      setExNote(`«${original}» добавлено в исключения черновика, текст проверен заново. Чтобы исключение действовало во встречах, сохраните его на вкладке «Словари и исключения».`);
    } catch (e) { setErr((e as ApiError).message); } finally { setBusy(false); }
  };
  const found = useMemo(() => (res ? labFindings(res) : null), [res]);
  const marked = useMemo(() => {
    if (!res) return null;
    const out: ReactNode[] = [];
    let pos = 0;
    res.replacements.forEach((r, i) => {
      out.push(res.source.slice(pos, r.start));
      out.push(<mark key={i} className={`pv-mark pv-${r.category}`} title={`${catTitle(r.category)} → ${r.replacement}`}>{res.source.slice(r.start, r.end)}</mark>);
      pos = r.end;
    });
    out.push(res.source.slice(pos));
    return out;
  }, [res]);  // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div role="tabpanel">
      <p className="muted small">Проверка выполняется только на этом сервере: текст и файл не сохраняются и никуда не отправляются. Форматы: {cat.formats.join(", ")}.
        Сканы PDF без текстового слоя (OCR) пока не распознаются.</p>
      <div className="cols">
        <label>Текст для проверки
          <textarea ref={focusOnMount} rows={10} value={text} onChange={(e) => { setText(e.target.value); setFile(null); }} placeholder="Вставьте стенограмму, протокол или документ" /></label>
        <label>Участники встречи — по строке на человека (необязательно)
          <textarea rows={10} value={people} onChange={(e) => setPeople(e.target.value)} placeholder="Иванов Иван Петрович" />
          <span className="help">Так же, как в настоящей встрече: участники распознаются во всех формах имени.</span></label>
      </div>
      <div className="row" style={{ gap: 12, flexWrap: "wrap", alignItems: "center" }}>
        <button type="button" className="btn mini ghost" onClick={() => { setText(SAMPLE); setPeople(SAMPLE_PEOPLE); setFile(null); }}>Пример протокола</button>
        <label className="small">Или файл <input type="file" accept={cat.formats.join(",")} onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></label>
        <label className="small">Профиль <select value={profile} onChange={(e) => setProfile(e.target.value as PrivacyProfile)}>
          {allowed.map((p) => <option key={p} value={p}>{cat.profile_titles[p]}</option>)}</select></label>
        {dirty && !file && <label className="check small"><input type="checkbox" checked={useDraft} onChange={(e) => setUseDraft(e.target.checked)} /> с несохранёнными изменениями настроек</label>}
        <button className="btn primary" onClick={run} disabled={busy || (!file && !text.trim())}>{busy ? "Обработка…" : "Проверить"}</button>
      </div>
      {err && <div className="alert error" role="alert">{err}</div>}
      {res && (
        <div className="pv-result">
          <p className="small"><b>{cat.profile_titles[res.profile]}</b> · {res.ms} мс · замен: {res.replacements.length}{res.info.format ? ` · ${res.info.format}` : ""}{res.info.pages ? `, страниц: ${res.info.pages}` : ""}
            {Object.entries(res.stats).map(([k, n]) => <span key={k} className="badge"> {catTitle(k)}: {n}</span>)}</p>
          {res.info.note && <div className="alert info">{res.info.note}</div>}
          {res.warnings.map((w) => <div key={w} className="alert info">{w}</div>)}
          <div className="pv-diff">
            <div><h3 className="field-title">Исходный текст (найденное выделено)</h3><pre className="pv-text">{marked}</pre></div>
            <div><h3 className="field-title">Что уйдёт в модель</h3><pre className="pv-text">{res.text}</pre></div>
          </div>
          {exNote && <div className="alert ok" role="status">{exNote}</div>}
          {found && <FindingTable title="Люди: признаны персональными данными" items={found.people} noteTitle="Основание"
            empty="Людей не найдено." action={(f) => <button type="button" className="btn mini ghost" disabled={busy} onClick={() => void except(f.original)}
              aria-label={`Ошибка: «${f.original}» — не человек. Добавить в исключения и проверить заново`}>Ошибка — в исключения</button>} />}
          {found && found.disputed.length > 0 && <FindingTable title="Спорные кандидаты: оставлены текстом" items={found.disputed} noteTitle="Почему не человек"
            hint="Распознавание имён отметило эти слова, но проверка по составу, части речи, месту в реплике и списку участников признала их обычной речью." />}
          <details><summary>Все замены ({res.replacements.length})</summary>
            <div className="table-scroll"><table className="table"><thead><tr><th>Найдено</th><th>Категория</th><th>Правило</th><th>Основание</th><th>Замена</th></tr></thead>
              <tbody>{res.replacements.map((r, i) => <tr key={i}><td>{r.category === "secret" ? "••••" : r.original}</td><td>{catTitle(r.category)}</td><td><code>{r.rule}</code></td><td>{r.evidence}</td><td>{r.replacement}</td></tr>)}</tbody>
            </table></div></details>
        </div>)}
    </div>
  );
}

function FindingTable({ title, items, noteTitle, empty, hint, action }: { title: string; items: Finding[]; noteTitle: string; empty?: string; hint?: string;
  action?: (f: Finding) => ReactNode }) {
  return (
    <details className="pv-findings" open>
      <summary>{title} ({items.reduce((n, f) => n + f.count, 0)})</summary>
      {hint && <p className="help">{hint}</p>}
      {!items.length ? <p className="muted small">{empty}</p> : (
        <div className="table-scroll"><table className="table">
          <thead><tr><th>Фрагмент</th><th>Контекст</th>{items.some((f) => f.replacement) && <th>Замена</th>}<th>{noteTitle}</th><th>Раз</th>{action && <th />}</tr></thead>
          <tbody>{items.map((f) => (
            <tr key={f.original + f.note}>
              <td><b>{f.original}</b></td>
              <td className="pv-context"><span>{f.context.before}<mark className="pv-mark">{f.context.match}</mark>{f.context.after}</span></td>
              {items.some((x) => x.replacement) && <td>{f.replacement}</td>}
              <td>{f.note}</td><td>{f.count}</td>{action && <td>{action(f)}</td>}
            </tr>))}</tbody>
        </table></div>)}
    </details>
  );
}
