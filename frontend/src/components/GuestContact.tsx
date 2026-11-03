import { type KeyboardEvent, useId } from "react";
import { useFocusOnMount } from "../useInitialFocus";

/** Как гость или зритель представляется: имя обязательно, почта — по желанию (для протокола и материалов встречи), согласие — отдельной галочкой.
 *  Сведения запоминаются на время гостевого сеанса (вкладка): при повторном входе вводить их заново не нужно. */
export interface Contact { name: string; email: string; consent: boolean }

const KEY = "guest.contact";
export function loadContact(): Contact {
  try {
    const c = JSON.parse(sessionStorage.getItem(KEY) ?? "null") as Partial<Contact> | null;
    const legacy = sessionStorage.getItem("guest.name") ?? "";          // прежние версии запоминали только имя
    return { name: c?.name ?? legacy, email: c?.email ?? "", consent: !!c?.consent };
  } catch { return { name: "", email: "", consent: false }; }
}
export function saveContact(c: Contact): void {
  try { sessionStorage.setItem(KEY, JSON.stringify({ name: c.name.trim(), email: c.email.trim(), consent: c.consent && !!c.email.trim() })); } catch { /* не критично */ }
}
export const nameOk = (c: Contact) => c.name.trim().replace(/\s+/g, " ").length >= 2;
/** Простая проверка вида адреса; точная — на сервере. Пустая почта допустима. */
export const emailOk = (c: Contact) => !c.email.trim() || /^[^\s@]+@[^\s@]+\.[^\s@.]+$/.test(c.email.trim());
export const contactBody = (c: Contact) => ({ display_name: c.name.trim(), email: c.email.trim() || null, materials_consent: !!c.email.trim() && c.consent });

export function GuestContactFields({ value, onChange, onEnter, preview, suffix = "" }: {
  value: Contact; onChange: (c: Contact) => void; onEnter?: () => void; preview?: string; suffix?: string;
}) {
  const id = useId();
  const focus = useFocusOnMount<HTMLInputElement>();
  const bad = !emailOk(value);
  const enter = (e: KeyboardEvent) => { if (e.key === "Enter" && onEnter) { e.preventDefault(); onEnter(); } };
  return (
    <div className="guest-contact">
      <label>Ваше имя
        <input ref={focus} value={value.name} maxLength={60} required autoComplete="name" placeholder="Например, Иван Иванов"
               onChange={(e) => onChange({ ...value, name: e.target.value })} onKeyDown={enter} />
      </label>
      {preview && value.name.trim() && <p className="muted small">{preview}: <b>{value.name.trim().replace(/\s+/g, " ")}{suffix}</b></p>}
      <label><span>Электронная почта <span className="muted small">— необязательно</span></span>
        <input type="email" inputMode="email" value={value.email} maxLength={254} autoComplete="email" placeholder="name@example.org" aria-invalid={bad}
               aria-describedby={`${id}-hint`} onChange={(e) => onChange({ ...value, email: e.target.value })} onKeyDown={enter} />
      </label>
      <p id={`${id}-hint`} className={`small ${bad ? "field-error" : "muted"}`}>
        {bad ? "Проверьте адрес или оставьте поле пустым." : "Можно указать, чтобы получить протокол и материалы встречи. Адрес видят только организаторы."}</p>
      {value.email.trim() && !bad && (
        <label className="check"><input type="checkbox" checked={value.consent} onChange={(e) => onChange({ ...value, consent: e.target.checked })} />
          <span>Присылать мне протокол и материалы встречи на этот адрес</span></label>
      )}
    </div>
  );
}
