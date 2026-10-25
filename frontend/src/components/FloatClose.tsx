import { Icon } from "./Icons";

/**
 * Кнопка «закрыть» плавающего окна (чаты, плеер, общая доска): один вид, размер и состояния везде.
 * `corner` — в правом верхнем углу всего окна (родитель с `position: relative|fixed|absolute`, класс `.fw-corner` не нужен);
 * без `corner` — обычный элемент в строке заголовка (последний справа).
 * Содержимое заголовка рядом резервирует место отступом `padding-inline-end: var(--fw-reserve)`.
 */
export default function FloatClose({ onClick, label, title, corner = false }: { onClick: () => void; label: string; title?: string; corner?: boolean }) {
  return (
    <button type="button" className={`fw-close ${corner ? "corner" : ""}`} onClick={onClick} aria-label={label} title={title ?? label}>
      <Icon name="close" size={18} />
    </button>
  );
}
