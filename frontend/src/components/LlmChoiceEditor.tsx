import type { LlmChoice, LlmEffective, LlmOptions } from "../api";
import { effectiveText } from "../phone";

/**
 * Выбор языковой модели для комнаты или конкретной встречи. Цепочка: системная по умолчанию → комната → встреча.
 * Если выбранная модель окажется недоступной (профиль удалён, локальная модель не загружена), комната не ломается: действует политика, заданная администратором.
 */
export default function LlmChoiceEditor({ value, onChange, options, effective, scope }: {
  value: LlmChoice; onChange: (c: LlmChoice) => void; options: LlmOptions | null | undefined; effective?: LlmEffective | null; scope: "room" | "meeting";
}) {
  if (!options) return <p className="muted">Загрузка вариантов…</p>;
  const sys = options.system;
  const sysText = sys.provider === "off" ? "отключена" : sys.provider === "local" ? "встроенная локальная модель" : `внешняя: ${sys.model ?? sys.name}`;
  const eff = effectiveText(effective);
  const who = scope === "room" ? "эта комната" : "эта встреча";
  const set = (mode: LlmChoice["mode"]) => onChange({
    mode, profile_id: mode === "profile" ? (value.profile_id ?? options.profiles.find((p) => !p.is_default)?.id ?? options.profiles[0]?.id ?? null) : null,
    local_model: mode === "local" ? (value.local_model ?? options.local[0]?.id ?? null) : null,
  });
  return (
    <div role="group" aria-label="Языковая модель">
      <p className="help" style={{ marginTop: 0 }}>Языковая модель составляет краткие резюме и протоколы. Системная модель — значение по умолчанию для всех комнат; здесь можно выбрать другую только для {scope === "room" ? "этой комнаты" : "этой встречи"}.
        Например, для обычных летучек хватает локальной Qwen 0.6B, а для сложных протоколов нужна сильная внешняя модель.</p>
      <fieldset className="group"><legend>Какая модель используется ({who})</legend>
        <label className="check"><input type="radio" name={`llm-${scope}`} checked={value.mode === "inherit"} onChange={() => set("inherit")} />
          <span className="check-body">{scope === "room" ? "Использовать системную по умолчанию" : "Как в комнате"}<span className="help">Сейчас системная: {sysText}.</span></span></label>
        <label className="check"><input type="radio" name={`llm-${scope}`} checked={value.mode === "local"} disabled={!options.local.length} onChange={() => set("local")} />
          <span className="check-body">Локальная модель (данные не покидают сервер)
            <select value={value.local_model ?? ""} disabled={value.mode !== "local"} onChange={(e) => onChange({ ...value, local_model: e.target.value })} aria-label="Локальная модель">
              {options.local.map((m) => <option key={m.id} value={m.id}>{m.title}{m.installed ? "" : " — не загружена"}{m.light ? " (облегчённая)" : ""}</option>)}
            </select>
            <span className="help">Подходит для простых задач: краткое резюме, решения, задачи, ответственные, короткий протокол. На длинных стенограммах качество ниже.</span></span></label>
        <label className="check"><input type="radio" name={`llm-${scope}`} checked={value.mode === "profile"} disabled={!options.profiles.length} onChange={() => set("profile")} />
          <span className="check-body">Внешний профиль LLM
            <select value={value.profile_id ?? ""} disabled={value.mode !== "profile"} onChange={(e) => onChange({ ...value, profile_id: e.target.value })} aria-label="Внешний профиль">
              {options.profiles.map((p) => <option key={p.id} value={p.id}>{p.name}{p.model ? ` — ${p.model}` : ""}</option>)}
            </select>
            <span className="help">Профили создаёт администратор («Языковая модель (LLM)» → профили API). Данные уходят к провайдеру модели — после обезличивания, если оно включено.</span></span></label>
        <label className="check"><input type="radio" name={`llm-${scope}`} checked={value.mode === "off"} onChange={() => set("off")} />
          <span className="check-body">Отключить языковую модель<span className="help">Протоколы и резюме для {scope === "room" ? "этой комнаты" : "этой встречи"} формироваться не будут.</span></span></label>
      </fieldset>
      {eff && <div className={`alert ${eff.tone === "ok" ? "ok" : eff.tone === "warn" ? "info" : "error"}`} role="status">{eff.text}</div>}
      <p className="help">Если выбранная модель станет недоступной (профиль удалён или локальная модель не загружена), {options.on_missing === "system"
        ? "будет использована системная модель по умолчанию, а в сведениях о протоколе появится пометка." : "протокол не создаётся, а в комнате показывается «модель недоступна» с причиной."} Эту политику задаёт администратор.</p>
    </div>
  );
}
