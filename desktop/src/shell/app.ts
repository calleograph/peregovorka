// Доверенная оболочка: боковая панель организаций и разделы приложения (подключение, настройки, диагностика). Страница сервера показывается главным процессом
// справа от панели; когда открыт раздел оболочки, страница скрыта (встреча и чаты при этом продолжают работать).
import { parseMeetingUrl } from "../common/links";
import { call, h, icon, orgTile, pg, type OrgView, type State } from "./ui";

let S: State | null = null;
let page = "org";                 // org | add | settings
let tab = "general";
let addPrefill: { url?: string; route?: string; error?: string | null } = {};
const app = document.getElementById("app")!;
const side = h("nav", { class: "side", "aria-label": "Организации" });
const main = h("main", { class: "main" });
app.append(side, main);

// ------------------------------------------------------------------------------------------ тема
function applyTheme(): void {
  const t = S?.orgs.find((o) => o.id === S!.activeId)?.theme;
  if (t === "dark" || t === "light") document.documentElement.setAttribute("data-theme", t);
  else document.documentElement.removeAttribute("data-theme");
}

// ------------------------------------------------------------------------------------------ боковая панель
function renderSide(): void {
  if (!S) return;
  const st = S;
  const tiles = st.orgs.map((o) => {
    const tile = orgTile(o, { role: "button", tabindex: "0", title: `${o.name}${o.unread ? ` — непрочитанных: ${o.unread}` : ""}${o.status === "offline" ? " — нет связи" : ""}`,
      "aria-current": String(page === "org" && o.id === st.activeId), "aria-label": o.name,
      on: { click: () => go("org", o.id), keydown: (e: Event) => { const k = (e as KeyboardEvent).key; if (k === "Enter" || k === " ") { e.preventDefault(); go("org", o.id); } } } });
    if (o.unread) tile.append(h("span", { class: "badge" }, o.unread > 99 ? "99+" : String(o.unread)));
    const dot = o.inCall ? "call" : o.status === "offline" || o.status === "error" ? "offline" : o.status === "connecting" ? "connecting" : "";
    if (dot) tile.append(h("span", { class: `dot ${dot}` }));
    return tile;
  });
  const tools = [
    st.canAdd ? h("button", { class: "tool", title: "Подключить организацию", "aria-label": "Подключить организацию", "aria-current": String(page === "add"), on: { click: () => go("add") } }, icon("plus")) : null,
    h("button", { class: "tool", title: "Настройки", "aria-label": "Настройки", "aria-current": String(page === "settings"), on: { click: () => go("settings") } }, icon("gear")),
  ];
  side.replaceChildren(...([h("div", { class: "orgs" }, tiles), tiles.length ? h("div", { class: "sep" }) : null, ...tools] as (HTMLElement | null)[]).filter((x): x is HTMLElement => !!x));
}

// ------------------------------------------------------------------------------------------ переходы
function go(p: string, orgId?: string): void {
  page = p;
  if (p === "org" && orgId) void call("shell:activate", { id: orgId });
  else void call("shell:page", { page: p });
  renderSide();
  renderMain(true);
}

let rendered = "";
function renderMain(force = false): void {
  if (!S) return;
  const o = S.orgs.find((x) => x.id === S!.activeId);
  const key = page === "org" ? `org:${o?.id}:${o?.status}:${o?.problem?.code}:${o?.retryAt}` : page === "settings" ? `settings:${tab}` : `add`;
  if (!force && key === rendered && page !== "settings") return;
  if (!force && page === "settings" && key === rendered) { refreshSettings(); return; }
  rendered = key;
  if (page === "add" || (page === "org" && !o)) main.replaceChildren(addPage());
  else if (page === "settings") main.replaceChildren(settingsPage());
  else main.replaceChildren(statusPage(o!));
}

// ------------------------------------------------------------------------------------------ состояние организации
let countdown: number | undefined;
function statusPage(o: OrgView): HTMLElement {
  window.clearInterval(countdown);
  if (o.status === "connecting") return h("div", { class: "status" }, h("div", { class: "box" }, h("div", { class: "spinner", role: "progressbar", "aria-label": "Подключение" }),
    h("h2", null, `Подключение к «${o.name}»…`), h("p", { class: "muted" }, o.host)));
  if (o.status === "offline" || o.status === "error") {
    const left = h("span");
    const tick = () => { const s = o.retryAt ? Math.max(0, Math.round((o.retryAt - Date.now()) / 1000)) : 0; left.textContent = s > 0 ? `Повторная попытка через ${s} с` : "Повторная попытка…"; };
    tick();
    countdown = window.setInterval(tick, 1000);
    return h("div", { class: "status" }, h("div", { class: "box" },
      orgTile(o), h("h2", null, o.name),
      h("div", { class: "alert err", role: "alert" }, icon("info"), h("div", { class: "body" }, h("b", null, o.problem?.title ?? "Нет связи с сервером"), h("span", null, o.problem?.hint ?? ""),
        o.problem?.code ? h("span", { class: "small mono" }, `${o.host} · ${o.problem.code}`) : null)),
      h("div", { class: "row" }, h("span", { class: "muted small" }, left)),
      h("div", { class: "actions" }, h("button", { class: "btn primary", on: { click: () => void call("shell:reconnect", { id: o.id }) } }, icon("retry"), "Повторить сейчас"),
        h("button", { class: "btn", on: { click: () => { tab = "diag"; go("settings"); } } }, "Диагностика"))));
  }
  return h("div", { class: "status" });
}

// ------------------------------------------------------------------------------------------ мастер подключения
function addPage(): HTMLElement {
  const st = S!;
  const first = st.orgs.length === 0;
  const url = h("input", { class: "input", id: "srv", type: "text", placeholder: "meet.example.org", autocomplete: "off", spellcheck: false, value: addPrefill.url ?? "", "aria-describedby": "srv-help" });
  const name = h("input", { class: "input", id: "nm", type: "text", placeholder: "Например: Головной офис", maxlength: 60, autocomplete: "off" });
  const out = h("div", { class: "stack" });
  const btn = h("button", { class: "btn primary", type: "submit" }, "Проверить и подключить");
  const policyErr = addPrefill.error || (!st.canAdd ? "Добавление серверов запрещено политикой организации. Подключения задаёт администратор." : null);
  const form = h("form", { class: "card stack", on: { submit: (e: Event) => { e.preventDefault(); void submit(); } } },
    h("label", { class: "field" }, h("span", null, "Адрес сервера Peregovorka"), url, h("small", { id: "srv-help" }, "Тот же адрес, что открываете в браузере. Можно вставить ссылку на встречу.")),
    h("label", { class: "field" }, h("span", null, "Название подключения (необязательно)"), name),
    st.allowedDomains.length ? h("p", { class: "small muted" }, `Разрешены домены: ${st.allowedDomains.join(", ")}`) : null,
    h("div", { class: "actions" }, first ? null : h("button", { class: "btn", type: "button", on: { click: () => go("org") } }, "Отмена"), btn),
    out);
  let route = addPrefill.route;
  async function submit(): Promise<void> {
    let raw = url.value.trim();
    const meeting = /^https?:\/\/[^/]+\/.+/.test(raw) ? parseMeetingUrl(raw) : null;
    if (meeting) { raw = meeting.origin; route = meeting.route; }
    btn.disabled = true;
    btn.replaceChildren(h("span", { class: "spinner", style: { width: "16px", height: "16px", borderWidth: "2px" } }), "Проверка…");
    out.replaceChildren();
    url.removeAttribute("aria-invalid");
    try {
      const r = (await call("shell:probe", { url: raw })) as Record<string, unknown>;
      if (r.inputError) { url.setAttribute("aria-invalid", "true"); out.replaceChildren(alertBox("err", INPUT_ERRORS[String(r.inputError)] ?? "Проверьте адрес")); url.focus(); return; }
      if (r.policyError) { out.replaceChildren(alertBox("err", String(r.policyError))); return; }
      if (r.duplicate) { out.replaceChildren(alertBox("info", "Эта организация уже подключена.")); return; }
      out.replaceChildren(checksList(r.steps as Step[]));
      if (!r.ok) {
        const p = r.problem as { title: string; hint: string; code: string; cert?: Record<string, string> } | undefined;
        if (p) out.append(alertBox("err", p.title, p.hint, p.cert ? certDl(p.cert) : null, p.code));
        return;
      }
      btn.replaceChildren("Подключение…");
      const a = (await call("shell:add", { url: raw, name: name.value, route })) as { ok: boolean };
      if (a.ok) { addPrefill = {}; page = "org"; renderSide(); renderMain(true); }
    } finally {
      btn.disabled = false;
      if (btn.isConnected) btn.replaceChildren("Проверить и подключить");
    }
  }
  setTimeout(() => (st.canAdd ? url.focus() : undefined), 0);
  const head = h("div", { class: "page-head" }, h("img", { class: "brandmark", src: "assets/icon.svg", alt: "" }),
    h("div", null, h("h1", null, first ? "Добро пожаловать в Peregovorka" : "Подключение организации"),
      h("p", { class: "lead" }, first ? "Укажите адрес корпоративного сервера — затем войдите учётной записью организации." : "Каждая организация работает отдельно: свой вход, свои чаты и уведомления.")));
  if (policyErr && !st.canAdd) return h("div", { class: "page narrow" }, head, alertBox("warn", policyErr));
  return h("div", { class: "page narrow" }, head, policyErr ? alertBox("err", policyErr) : null, form);
}

const INPUT_ERRORS: Record<string, string> = { empty: "Укажите адрес сервера.", invalid: "Адрес не похож на адрес сервера.", not_https: "Нужен защищённый адрес https://. Незащищённые подключения не поддерживаются.",
  credentials: "Адрес не должен содержать логин или пароль.", path: "Укажите адрес сервера без пути, например meet.example.org." };
type Step = { id: string; title: string; state: string; text: string };
const ST_ICON: Record<string, string> = { ok: "check", fail: "close", warn: "info", skip: "more" };
function checksList(steps: Step[]): HTMLElement {
  return h("ul", { class: "checks", "aria-label": "Результаты проверки" }, steps.map((s) => h("li", { class: s.state }, h("span", { class: "st", "aria-label": s.state }, icon(ST_ICON[s.state] ?? "info")),
    h("div", { class: "t" }, h("b", null, s.title), h("span", null, s.text)))));
}
function alertBox(kind: "err" | "warn" | "info", title: string, hint?: string, extra?: Node | null, code?: string): HTMLElement {
  return h("div", { class: `alert ${kind}`, role: kind === "err" ? "alert" : "status" }, icon("info"), h("div", { class: "body" }, h("b", null, title), hint ? h("span", null, hint) : null, extra ?? null,
    code ? h("span", { class: "small mono" }, code) : null));
}
function certDl(c: Record<string, string>): HTMLElement {
  return h("dl", null, h("dt", null, "Выдан для"), h("dd", null, c.subject || "—"), h("dt", null, "Кем выдан"), h("dd", null, c.issuer || "—"),
    h("dt", null, "Действует"), h("dd", null, `${c.validFrom} — ${c.validTo}`), h("dt", null, "SHA-256"), h("dd", { class: "mono" }, c.sha256 || "—"));
}

// ------------------------------------------------------------------------------------------ настройки
const TABS: [string, string, string][] = [["general", "Общие", "sliders"], ["notify", "Уведомления", "bell"], ["orgs", "Организации", "users"], ["network", "Сеть", "shield"],
  ["updates", "Обновления", "download"], ["diag", "Диагностика", "info"], ["about", "О программе", "sparkle"]];
let panel: HTMLElement | null = null;
function settingsPage(): HTMLElement {
  const nav = h("div", { class: "tabs", role: "tablist", "aria-orientation": "vertical" }, h("h1", null, "Настройки"),
    TABS.map(([id, label, ic]) => h("button", { role: "tab", "aria-selected": String(tab === id), id: `tab-${id}`, on: { click: () => { tab = id; renderMain(true); } } }, icon(ic), label)));
  panel = h("section", { class: "panel", role: "tabpanel", "aria-labelledby": `tab-${tab}` });
  fillPanel();
  const back = h("button", { class: "btn icon ghost", title: "Закрыть настройки", "aria-label": "Закрыть настройки", style: { position: "absolute", top: "16px", right: "16px" }, on: { click: () => go("org") } }, icon("close"));
  return h("div", { class: "settings" }, nav, h("div", { style: { position: "relative", overflow: "auto" } }, panel, S!.orgs.length ? back : null));
}
function refreshSettings(): void {
  if (tab === "orgs" || tab === "updates" || tab === "about") fillPanel();         // живые списки; формы с вводом не перерисовываются
}
const isLocked = (f: string) => S!.locked.includes(f);
const lockNote = () => h("span", { class: "locked" }, icon("lock"), "Задано администратором");
function setS(patch: Record<string, unknown>): void { void call("shell:settings", patch); }

function sw(label: string, hint: string | null, checked: boolean, onChange: (v: boolean) => void, locked = false): HTMLElement {
  const inp = h("input", { type: "checkbox", role: "switch", checked, disabled: locked, on: { change: (e: Event) => onChange((e.target as HTMLInputElement).checked) } });
  return h("label", { class: "switch" }, h("span", { class: "txt" }, h("b", null, label), hint ? h("small", null, hint) : null, locked ? lockNote() : null), inp);
}
function seg(options: [string, string][], value: string, onPick: (v: string) => void, locked = false): HTMLElement {
  const box = h("div", { class: "seg", role: "radiogroup" });
  for (const [v, label] of options) {
    const b = h("button", { type: "button", role: "radio", "aria-checked": String(v === value), "aria-pressed": String(v === value), disabled: locked }, label);
    b.addEventListener("click", () => {
      onPick(v);
      for (const x of box.children) { x.setAttribute("aria-pressed", String(x === b)); x.setAttribute("aria-checked", String(x === b)); }
    });
    box.append(b);
  }
  return box;
}

function fillPanel(): void {
  if (!panel || !S) return;
  const s = S.settings;
  const P = panel;
  const T = (t: string) => h("h2", null, t);
  if (tab === "general") {
    P.replaceChildren(T("Общие"),
      h("div", { class: "card flat stack" },
        h("div", { class: "field" }, h("span", null, "Запускать вместе с Windows"), seg([["off", "Не запускать"], ["tray", "Свёрнутым в трей"], ["window", "С открытым окном"]], s.autostart,
          (v) => setS({ autostart: v }), isLocked("autostart")), h("small", null, "Рекомендуется «свёрнутым в трей»: сообщения и уведомления приходят сразу после входа в Windows."),
          isLocked("autostart") ? lockNote() : null),
        h("div", { class: "field" }, h("span", null, "Кнопка «Закрыть» окна"), seg([["tray", "Свернуть в трей"], ["quit", "Выйти из приложения"], ["ask", "Спрашивать"]], s.closeAction,
          (v) => setS({ closeAction: v }), isLocked("closeAction")), h("small", null, "Во время встречи приложение всегда спросит, продолжать ли её в фоне."), isLocked("closeAction") ? lockNote() : null)));
  } else if (tab === "notify") {
    const dnd = s.notifications.dndUntil === -1 || s.notifications.dndUntil > Date.now();
    P.replaceChildren(T("Уведомления"),
      h("div", { class: "card flat" },
        sw("Уведомления Windows", "Новые сообщения, упоминания и события встреч из всех подключённых организаций", s.notifications.enabled, (v) => setS({ notifications: { enabled: v } }), isLocked("notifications.enabled")),
        sw("Звук", null, s.notifications.sound, (v) => setS({ notifications: { sound: v } })),
        sw("Скрывать текст сообщений", "В уведомлении — только «Новое сообщение», без автора и текста", s.notifications.hideContent, (v) => setS({ notifications: { hideContent: v } }), isLocked("notifications.hideContent")),
        sw("Не беспокоить", dnd && s.notifications.dndUntil > 0 ? `До ${new Date(s.notifications.dndUntil).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })}` : "Уведомления не показываются, сообщения копятся в непрочитанных",
          dnd, (v) => setS({ notifications: { dndUntil: v ? -1 : 0 } }))),
      h("div", { class: "row" }, h("span", { class: "muted small" }, "Не беспокоить на:"), ...[[60, "1 час"], [240, "4 часа"]].map(([m, l]) => h("button", { class: "btn sm", on: { click: () => setS({ notifications: { dndUntil: Date.now() + Number(m) * 60_000 } }) } }, String(l)))),
      h("p", { class: "small muted" }, "Отключить уведомления отдельного чата можно в самом чате («Без уведомлений»). Режим фокусировки Windows тоже учитывается."));
  } else if (tab === "orgs") {
    P.replaceChildren(T("Организации"),
      h("div", { class: "card flat list" }, S.orgs.length ? S.orgs.map((o, i) => orgRow(o, i)) : h("p", { class: "muted" }, "Нет подключённых организаций.")),
      h("div", { class: "actions" }, S.orgs.length > 1 ? h("button", { class: "btn", on: { click: () => void call("shell:signoutAll") } }, icon("power"), "Выйти из всех") : null,
        S.canAdd ? h("button", { class: "btn primary", on: { click: () => go("add") } }, icon("plus"), "Подключить организацию") : null));
  } else if (tab === "network") {
    const p = s.proxy, locked = isLocked("proxy");
    const rules = h("input", { class: "input", value: p.rules, placeholder: "proxy.example.org:3128", disabled: locked });
    const bypass = h("input", { class: "input", value: p.bypass, placeholder: "<local>;*.example.org", disabled: locked });
    const pac = h("input", { class: "input", value: p.pac, placeholder: "https://proxy.example.org/proxy.pac", disabled: locked });
    let mode = p.mode;
    const extra = h("div", { class: "stack" });
    const show = () => extra.replaceChildren(...(mode === "manual" ? [h("label", { class: "field" }, h("span", null, "Прокси-сервер"), rules), h("label", { class: "field" }, h("span", null, "Без прокси для"), bypass)]
      : mode === "pac" ? [h("label", { class: "field" }, h("span", null, "Адрес сценария PAC"), pac)] : []));
    show();
    P.replaceChildren(T("Сеть"),
      h("div", { class: "card flat stack" },
        h("div", { class: "field" }, h("span", null, "Прокси"), seg([["system", "Как в Windows"], ["direct", "Без прокси"], ["manual", "Вручную"], ["pac", "Сценарий PAC"]], mode, (v) => { mode = v; show(); }, locked), locked ? lockNote() : null),
        extra,
        h("div", { class: "actions" }, h("button", { class: "btn primary", disabled: locked, on: { click: () => setS({ proxy: { mode, rules: rules.value, bypass: bypass.value, pac: pac.value } }) } }, "Применить"))),
      h("div", { class: "alert info" }, icon("shield"), h("div", { class: "body" }, h("b", null, "Сертификаты"),
        h("span", null, "Приложение доверяет сертификатам из хранилища Windows. Для внутреннего центра сертификации организации администратор устанавливает его корневой сертификат групповой политикой. Проверка сертификатов не отключается."))));
  } else if (tab === "updates") {
    const u = S.update;
    const status: Record<string, string> = { idle: "Проверка ещё не выполнялась", checking: "Проверка…", none: "Установлена последняя версия", available: `Доступна версия ${u.version}`,
      downloading: `Загрузка версии ${u.version}… ${u.progress ?? 0}%`, ready: `Версия ${u.version} готова к установке`, error: u.error ?? "Ошибка проверки", disabled: u.error ?? "Проверка обновлений выключена",
      managed: "Версию приложения устанавливает администратор (корпоративная установка)" };
    const inCall = S.orgs.some((o) => o.inCall || o.sharing);
    const fixed = u.status === "managed" || (u.status === "disabled" && !!u.error);          // корпоративная установка или сборка без ключа: проверять нечего
    P.replaceChildren(T("Обновления"),
      h("div", { class: "card flat stack" },
        h("dl", { class: "kv" }, h("dt", null, "Установлена"), h("dd", null, `${S.version}${S.buildKind !== "release" ? " (тестовая сборка)" : ""}`), h("dt", null, "Состояние"), h("dd", null, status[u.status] ?? u.status),
          u.checkedAt ? h("dt", null, "Проверено") : null, u.checkedAt ? h("dd", null, new Date(u.checkedAt).toLocaleString("ru-RU")) : null),
        u.notes ? h("pre", { class: "log" }, u.notes) : null,
        u.status === "ready" && inCall ? alertBox("info", "Установка — после встречи", "Во время встречи и показа экрана приложение не перезапускается.") : null,
        fixed ? null : h("div", { class: "field" }, h("span", null, "Режим"), seg([["auto", "Загружать автоматически"], ["notify", "Только сообщать"], ["off", "Не проверять"]], S.settings.updateMode,
          (v) => setS({ updateMode: v }), isLocked("updateMode")), isLocked("updateMode") ? lockNote() : null,
          h("small", null, "Обновление устанавливается, только если пакет подписан ключом издателя Peregovorka.")),
        fixed && u.status !== "ready" ? null : h("div", { class: "actions" },
          h("button", { class: "btn", disabled: u.status === "checking" || u.status === "downloading", on: { click: () => void call("shell:update:check") } }, icon("retry"), "Проверить обновления"),
          u.status === "ready" ? h("button", { class: "btn", disabled: !!u.installOnQuit, on: { click: () => void call("shell:update:install", { now: false }) } }, u.installOnQuit ? "Установится при выходе" : "Установить при выходе") : null,
          u.status === "ready" ? h("button", { class: "btn primary", disabled: inCall, on: { click: () => void call("shell:update:install", { now: true }) } }, "Перезапустить и обновить") : null)));
  } else if (tab === "diag") {
    const box = h("div", { class: "stack" }, h("div", { class: "spinner" }));
    P.replaceChildren(T("Диагностика"), box);
    void devices().then((d) => call("shell:diag", { devices: d })).then((r) => box.replaceChildren(diagView(r as Diag)));
  } else if (tab === "about") {
    P.replaceChildren(T("О программе"),
      h("div", { class: "card flat stack" },
        h("div", { class: "row" }, h("img", { class: "brandmark", src: "assets/icon.svg", alt: "" }), h("div", null, h("h2", null, "Peregovorka для Windows"), h("p", { class: "muted" }, `Версия ${S.version}`))),
        S.buildKind !== "release" ? alertBox("warn", "Тестовая сборка", "Сборка не подписана сертификатом издателя и не является доверенным корпоративным выпуском.") : null,
        h("p", { class: "muted" }, "Клиент подключается к корпоративным серверам Peregovorka. Пароли не хранятся; вход сохраняется отзываемым токеном устройства, защищённым средствами Windows.")));
  }
}

function orgRow(o: OrgView, i: number): HTMLElement {
  const st = o.status === "online" ? h("span", { class: "pill ok" }, o.user ? "Вход выполнен" : "В сети") : o.status === "signed-out" ? h("span", { class: "pill warn" }, "Нужен вход")
    : o.status === "connecting" ? h("span", { class: "pill" }, "Подключение…") : h("span", { class: "pill err" }, "Нет связи");
  const nameInp = h("input", { class: "input", value: o.name, maxlength: 60, "aria-label": "Название подключения", style: { height: "30px" } });
  let editing = false;
  const sub = [o.host, o.user, o.serverVersion ? `сервер ${o.serverVersion}` : null, o.managed ? "задано администратором" : null].filter(Boolean).join(" · ");
  const who = h("div", { class: "who" }, h("b", { title: o.name }, o.name), h("span", { title: sub }, sub));
  const rename = h("button", { class: "btn icon sm ghost", title: "Переименовать", "aria-label": "Переименовать", on: { click: () => {
    if (!editing) { editing = true; who.replaceChildren(nameInp); nameInp.focus(); nameInp.select(); return; }
    editing = false; void call("shell:rename", { id: o.id, name: nameInp.value });
  } } }, icon("edit"));
  nameInp.addEventListener("keydown", (e) => { if (e.key === "Enter") rename.click(); if (e.key === "Escape") { editing = false; fillPanel(); } });
  return h("div", { class: "item" }, orgTile(o), who, st,
    h("div", { class: "row" },
      h("button", { class: "btn icon sm ghost", title: "Выше", "aria-label": "Выше", disabled: i === 0, on: { click: () => void call("shell:move", { id: o.id, dir: -1 }) } }, icon("chevronD", "i flip")),
      rename,
      o.status === "offline" ? h("button", { class: "btn icon sm ghost", title: "Переподключиться", "aria-label": "Переподключиться", on: { click: () => void call("shell:reconnect", { id: o.id }) } }, icon("retry")) : null,
      h("button", { class: "btn icon sm ghost", title: "Выйти из организации", "aria-label": "Выйти из организации", disabled: o.status !== "online", on: { click: () => void call("shell:signout", { id: o.id }) } }, icon("power")),
      o.managed ? null : h("button", { class: "btn icon sm ghost danger", title: "Удалить подключение", "aria-label": "Удалить подключение", on: { click: () => void call("shell:remove", { id: o.id }) } }, icon("trash"))));
}

type Diag = Record<string, unknown> & { client: Record<string, unknown>; organizations: Record<string, unknown>[]; permissions: Record<string, string>; devices: { audioinput: number; audiooutput: number; videoinput: number } | null;
  processes: { type: string; mb: number; cpu: number }[]; log_tail: string; update: { status: string } };
async function devices() {
  try {
    const d = await navigator.mediaDevices.enumerateDevices();
    const n = (k: string) => d.filter((x) => x.kind === k).length;
    return { audioinput: n("audioinput"), audiooutput: n("audiooutput"), videoinput: n("videoinput"), labels: d.map((x) => x.label).filter(Boolean) };
  } catch { return null; }
}
const ORG_ST: Record<string, string> = { online: "в сети", "signed-out": "нужен вход", connecting: "подключение", offline: "нет связи", error: "ошибка" };
const UPD_ST: Record<string, string> = { idle: "не проверялись", checking: "проверка", none: "последняя версия", available: "есть новая версия", downloading: "загрузка", ready: "готово к установке",
  error: "ошибка проверки", disabled: "выключены", managed: "управляет администратор" };
const WS_ST: Record<string, string> = { online: "подключён", connecting: "подключается", reconnecting: "переподключается", offline: "нет связи" };
const PERM: Record<string, string> = { granted: "разрешено", denied: "запрещено в параметрах конфиденциальности Windows", restricted: "ограничено", "not-determined": "не запрашивалось", unknown: "нет данных" };
function diagView(r: Diag): HTMLElement {
  const c = r.client;
  const mem = r.processes.reduce((n, p) => n + p.mb, 0);
  return h("div", { class: "stack" },
    h("div", { class: "card flat" }, h("dl", { class: "kv" },
      h("dt", null, "Версия клиента"), h("dd", null, `${c.version} (${c.build === "release" ? "выпуск" : "тестовая сборка"})`),
      h("dt", null, "Electron / Chromium"), h("dd", null, `${c.electron} / ${c.chrome}`),
      h("dt", null, "Система"), h("dd", null, `${c.os}, ${c.arch}`),
      h("dt", null, "Мониторы"), h("dd", null, (c.displays as { w: number; h: number; scale: number }[]).map((d) => `${d.w}×${d.h} @ ${Math.round(d.scale * 100)}%`).join("; ")),
      h("dt", null, "Камера"), h("dd", null, PERM[r.permissions.camera] ?? r.permissions.camera),
      h("dt", null, "Микрофон"), h("dd", null, PERM[r.permissions.microphone] ?? r.permissions.microphone),
      h("dt", null, "Устройства"), h("dd", null, r.devices ? `микрофонов ${r.devices.audioinput}, динамиков ${r.devices.audiooutput}, камер ${r.devices.videoinput}` : "нет данных"),
      h("dt", null, "Память"), h("dd", null, `${mem} МБ, процессов ${r.processes.length}`),
      h("dt", null, "Запуск"), h("dd", null, c.startup_ms ? `${c.startup_ms} мс` : "—"),
      h("dt", null, "Обновления"), h("dd", null, UPD_ST[r.update.status] ?? r.update.status))),
    h("div", { class: "card flat list" }, r.organizations.map((o) => h("div", { class: "item" }, h("div", { class: "who" }, h("b", null, String(o.host)),
      h("span", null, [ORG_ST[String(o.status)] ?? String(o.status), o.problem ? `ошибка ${o.problem}` : null, o.server_version ? `сервер ${o.server_version}` : null, `канал событий: ${WS_ST[String(o.websocket)] ?? "—"}`,
        o.in_call ? "идёт встреча" : null, o.saved_sign_in ? "вход сохранён" : "вход не сохранён"].filter(Boolean).join(" · ")))))),
    h("div", { class: "actions" }, h("button", { class: "btn", on: { click: () => void call("shell:logs") } }, icon("folder"), "Папка журналов"),
      h("button", { class: "btn primary", on: { click: () => void devices().then((d) => call("shell:diag:export", { devices: d })) } }, icon("download"), "Сохранить отчёт")),
    h("p", { class: "small muted" }, "В отчёт не попадают пароли, токены, cookie и содержимое сообщений. Его можно передать администратору или в службу поддержки."));
}

// ------------------------------------------------------------------------------------------ значок на панели задач
let lastBadge = -1;
function taskbarBadge(): void {
  const n = S ? S.orgs.reduce((a, o) => a + o.unread, 0) : 0;
  if (n === lastBadge) return;
  lastBadge = n;
  if (!n) { void call("shell:badge", { png: "", label: "" }); return; }
  const c = document.createElement("canvas");
  c.width = c.height = 32;
  const g = c.getContext("2d")!;
  g.fillStyle = "#cc3535";
  g.beginPath(); g.arc(16, 16, 16, 0, Math.PI * 2); g.fill();
  g.fillStyle = "#fff";
  g.font = `700 ${n > 99 ? 13 : n > 9 ? 17 : 20}px Inter, Segoe UI, sans-serif`;
  g.textAlign = "center"; g.textBaseline = "middle";
  g.fillText(n > 99 ? "99+" : String(n), 16, 17);
  void call("shell:badge", { png: c.toDataURL("image/png"), label: `Непрочитанных: ${n}` });
}

// ------------------------------------------------------------------------------------------ события главного процесса
function onState(st: State): void {
  S = st;
  if (page === "org" && st.page !== "org" && st.page) page = st.page;
  applyTheme();
  renderSide();
  renderMain();
  taskbarBadge();
}
pg.on("shell:state", (d) => onState(d as State));
pg.on("shell:go", (d) => {
  const g = d as { page: string; tab?: string; url?: string; route?: string; error?: string | null };
  if (g.page === "add") addPrefill = { url: g.url, route: g.route, error: g.error };
  if (g.tab) tab = g.tab;
  page = g.page;
  renderSide();
  renderMain(true);
});
pg.on("shell:toast", (d) => {
  const t = h("div", { class: "toast", role: "status" }, String((d as { text?: string }).text ?? ""));
  document.body.append(t);
  setTimeout(() => t.remove(), 5000);
});
void call<State>("shell:state").then((st) => { page = st.page || (st.orgs.length ? "org" : "add"); onState(st); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && page === "settings" && S?.orgs.length) go("org"); });
