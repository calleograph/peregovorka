import InfoTip from "./InfoTip";

export type AnonMode = "" | "on" | "off";
interface Limits { anonymize_default?: boolean; anonymize_available?: boolean; anonymize_can_off?: boolean }

/** Режим обезличивания одного документа. Настройки комнаты и сервера не меняются; адреса получателей материалов от него не зависят. */
export default function AnonChoice({ plan, value, onChange, disabled }: { plan: Limits; value: AnonMode; onChange: (v: AnonMode) => void; disabled?: boolean }) {
  const name = "anon-mode";
  return (
    <fieldset className="group">
      <legend>Обезличивание<InfoTip>Выбор действует только на этот документ: настройки комнаты и сервера не меняются. Адреса получателей материалов от режима не зависят. Пароли, токены и ключи скрываются всегда.</InfoTip></legend>
      <div className="checks">
        <label className="check"><input type="radio" name={name} checked={value === ""} disabled={disabled} onChange={() => onChange("")} /> По настройкам: {plan.anonymize_default ? "с обезличиванием" : "без обезличивания"}</label>
        <label className="check"><input type="radio" name={name} checked={value === "on"} disabled={disabled || plan.anonymize_available === false} onChange={() => onChange("on")} /> С обезличиванием</label>
        <label className="check"><input type="radio" name={name} checked={value === "off"} disabled={disabled || plan.anonymize_can_off === false} onChange={() => onChange("off")} /> Без обезличивания</label>
      </div>
      {plan.anonymize_can_off === false && <p className="muted small" style={{ margin: "4px 0 0" }}>Обезличивание обязательно на этом сервере: снять его для документа может только администратор.</p>}
      {plan.anonymize_available === false && <p className="muted small" style={{ margin: "4px 0 0" }}>Обезличивание выключено администратором сервера.</p>}
    </fieldset>
  );
}
