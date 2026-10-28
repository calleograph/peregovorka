export interface ModelOption { key: string; label: string; local: boolean }

/** Выбор модели «только для этого формирования» — общий для окна протокола и окна карты разговора. Пустое значение — как настроено (настройки не меняются). */
export default function ModelChoice({ choices, value, onChange, currentName, once, disabled, what = "формирования" }:
  { choices: ModelOption[]; value: string; onChange: (key: string) => void; currentName?: string; once?: boolean; disabled?: boolean; what?: string }) {
  return (
    <label style={{ marginTop: 6 }}>Использовать другую модель только для этого {what}
      <select value={value} onChange={(e) => onChange(e.target.value)} disabled={disabled}>
        <option value="">Не менять: как настроено ({currentName && !once ? currentName : "по настройкам"})</option>
        {choices.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
      </select>
      <span className="help">Настройки переговорки и системы не меняются. В списке только настроенные и включённые модели. Для внешней модели действует обезличивание по правилам переговорки; факт выбора записывается в журнал.</span>
    </label>
  );
}
