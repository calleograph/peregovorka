import { useState } from "react";
import type { RoomSip, SipOptions } from "../api";
import { normalizeNumber } from "../phone";

/** Раздел «Телефония» в настройках комнаты: SIP-профиль, внутренний номер для входящих, разрешение входящих/исходящих, сохранённые номера. */
export default function TelephonyEditor({ sip, options, onChange }: { sip: RoomSip; options: SipOptions | null | undefined; onChange: (s: RoomSip) => void }) {
  const [name, setName] = useState("");
  const [num, setNum] = useState("");
  const [err, setErr] = useState("");
  if (!options) return <p className="muted">Загрузка…</p>;
  const set = (patch: Partial<RoomSip>) => onChange({ ...sip, ...patch });
  const add = () => {
    const n = normalizeNumber(num);
    if (!n) { setErr("Номер: только цифры и символы + * # (например, +70001112233)."); return; }
    if (sip.contacts.length >= 30) { setErr("Сохранённых номеров не больше 30."); return; }
    setErr(""); set({ contacts: [...sip.contacts, { name: name.trim().slice(0, 80), number: n }] }); setName(""); setNum("");
  };
  const off = sip.mode === "off";
  return (
    <div role="tabpanel">
      {!options.server_enabled && <div className="alert info">Телефония не включена на сервере. Настройки комнаты можно подготовить заранее, но звонки заработают после включения (Администрирование → SIP-телефония).</div>}
      {options.server_enabled && !options.profiles.length && <div className="alert info">Нет ни одного включённого SIP-профиля. Администратор добавляет подключение к АТС в разделе «Администрирование → SIP-телефония».</div>}
      <fieldset className="group"><legend>Телефония в этой комнате</legend>
        <label className="check"><input type="radio" name="sipmode" checked={off} onChange={() => set({ mode: "off", profile_id: null })} />
          <span className="check-body">Телефония отключена<span className="help">Позвонить в комнату и из комнаты нельзя.</span></span></label>
        <label className="check"><input type="radio" name="sipmode" checked={sip.mode === "default"} onChange={() => set({ mode: "default", profile_id: null })} />
          <span className="check-body">Использовать SIP-профиль по умолчанию<span className="help">Сейчас по умолчанию: {options.default ?? "не выбран"}.</span></span></label>
        <label className="check"><input type="radio" name="sipmode" checked={sip.mode === "profile"} disabled={!options.profiles.length}
                                         onChange={() => set({ mode: "profile", profile_id: sip.profile_id ?? options.profiles[0]?.id ?? null })} />
          <span className="check-body">Конкретный SIP-профиль
            <select value={sip.profile_id ?? ""} disabled={sip.mode !== "profile"} onChange={(e) => set({ profile_id: e.target.value })} aria-label="SIP-профиль">
              {options.profiles.map((p) => <option key={p.id} value={p.id}>{p.name}{p.synced ? "" : " — не синхронизирован"}</option>)}
            </select></span></label>
      </fieldset>
      {!off && (
        <>
          <fieldset className="group"><legend>Звонки</legend>
            <label className="check"><input type="checkbox" checked={sip.allow_outbound} onChange={(e) => set({ allow_outbound: e.target.checked })} />
              <span className="check-body">Разрешить исходящие звонки<span className="help">Руководитель нажимает «Позвонить» во время встречи, вводит номер или выбирает сохранённый — абонент появляется среди участников как «Телефон: номер». Его речь попадает в стенограмму и запись.</span></span></label>
            <label className="check"><input type="checkbox" checked={sip.allow_inbound} onChange={(e) => set({ allow_inbound: e.target.checked })} />
              <span className="check-body">Разрешить входящие звонки<span className="help">Звонок на внутренний номер комнаты попадает в идущую встречу. Когда встречи нет, звонок отклоняется.</span></span></label>
            <label>Внутренний номер комнаты <span className="muted small">(для входящих; 2–16 цифр, уникален)</span>
              <input value={sip.extension ?? ""} onChange={(e) => set({ extension: e.target.value.replace(/[^0-9]/g, "").slice(0, 16) || null })} inputMode="numeric" placeholder="201" style={{ maxWidth: 200 }} aria-invalid={sip.allow_inbound && !sip.extension} />
              {sip.allow_inbound && !sip.extension && <span className="field-err" role="alert">Для входящих звонков укажите внутренний номер.</span>}
            </label>
          </fieldset>
          <fieldset className="group"><legend>Сохранённые номера</legend>
            {sip.contacts.length === 0 && <p className="muted small" style={{ margin: 0 }}>Пока нет. Добавьте номера, на которые часто звоните, — они будут в списке в окне «Позвонить».</p>}
            {sip.contacts.length > 0 && (
              <ul className="chips-list">{sip.contacts.map((c, i) => (
                <li key={`${c.number}-${i}`}>{c.name || "Без названия"} <span className="muted small">{c.number}</span>
                  <button type="button" className="chip-x" aria-label={`Убрать ${c.name || c.number}`} onClick={() => set({ contacts: sip.contacts.filter((_, j) => j !== i) })}>✕</button></li>))}</ul>)}
            <div className="row">
              <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Название (например, Секретариат)" aria-label="Название номера" maxLength={80} />
              <input value={num} onChange={(e) => setNum(e.target.value)} placeholder="+70001112233" aria-label="Номер" inputMode="tel" style={{ maxWidth: 220 }}
                     onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); add(); } }} />
              <button type="button" className="btn" onClick={add} disabled={!num.trim()}>Добавить</button>
            </div>
            {err && <div className="field-err" role="alert">{err}</div>}
          </fieldset>
        </>
      )}
      <p className="help">Телефония работает через LiveKit SIP и использует отдельные порты (SIP и RTP) — не те, что нужны браузерным участникам (443 и порты LiveKit). Настройки подключения к АТС задаёт администратор.</p>
    </div>
  );
}
