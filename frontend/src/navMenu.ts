/** Логика клавиатурной навигации по выпадающему меню (чистые функции — проверяются тестами без DOM). */

export type MenuItem =
  | { kind: "link"; key: string; label: string; to: string; hint?: string; icon?: string }
  | { kind: "action"; key: string; label: string; onSelect: () => void; hint?: string; danger?: boolean; disabled?: boolean }
  | { kind: "text"; key: string; label: string }
  | { kind: "divider"; key: string };

/** Пункты, на которые можно встать фокусом (без разделителей, поясняющего текста и отключённых). */
export const focusable = (items: MenuItem[]): number[] =>
  items.flatMap((it, i) => (it.kind === "link" || (it.kind === "action" && !it.disabled) ? [i] : []));

/** Следующий пункт при нажатии клавиши; null — клавиша не относится к навигации. cur — индекс в списке focusable (-1: ничего не выбрано). */
export function nextIndex(key: string, cur: number, count: number): number | null {
  if (count <= 0) return null;
  switch (key) {
    case "ArrowDown": return cur < 0 ? 0 : (cur + 1) % count;
    case "ArrowUp": return cur < 0 ? count - 1 : (cur - 1 + count) % count;
    case "Home": return 0;
    case "End": return count - 1;
    default: return null;
  }
}

/** Ссылки, ведущие в раздел администрирования с готовой вкладкой. */
export const adminHref = (tab: string): string => `/admin?tab=${encodeURIComponent(tab)}`;

/** Вкладка из строки запроса (?tab=…) — только если она есть в списке известных. */
export function tabFromSearch(search: string, known: string[]): string | null {
  const t = new URLSearchParams(search).get("tab");
  return t && known.includes(t) ? t : null;
}

/** Пункты меню «Администрирование» для повседневной работы; полный список — в левом меню админки. */
export const ADMIN_QUICK: { tab: string; label: string; hint: string }[] = [
  { tab: "system", label: "Состояние системы", hint: "сервисы, проблемы, «Исправить автоматически»" },
  { tab: "updates", label: "Обновления и версии", hint: "обновить проект одной кнопкой" },
  { tab: "rooms", label: "Переговорки", hint: "комнаты, доступ, роли" },
  { tab: "llm", label: "Языковая модель (LLM)", hint: "локальная Qwen или внешняя" },
  { tab: "sip", label: "SIP-телефония", hint: "транки и звонки в комнаты" },
  { tab: "journal", label: "Журнал событий", hint: "что происходит в системе" },
];
