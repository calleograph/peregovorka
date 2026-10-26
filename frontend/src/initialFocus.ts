/**
 * Умный начальный фокус диалогов и форм (Smart Autofocus). Правило: окно, работа в котором начинается с ввода текста, ставит курсор в первое логически нужное поле сразу,
 * без щелчка и Tab. Выбор — чистая функция `chooseInitial` (проверяется тестами), работа с DOM — `focusInitial`.
 *
 *  1. Поле с `data-autofocus` выбирается первым (явное решение автора формы).
 *  2. Иначе — первое по порядку поле формы (input, textarea, select), которое видно, доступно и не только для чтения. Если оно текстовое — фокус туда;
 *     если первым идёт флажок, переключатель, кнопка выбора или список — курсор не прыгает через них к дальнему полю: фокус получает само окно.
 *  3. Окно или любой его предок с `data-no-autofocus` фокус на поле не получает (окна для чтения, выбор действия).
 *  4. Сенсорный экран (`pointer: coarse`): поле не фокусируется, чтобы не выезжала экранная клавиатура; окно получает фокус само.
 *  5. Если фокус уже внутри окна (например, поле с атрибутом `autoFocus`) — он остаётся на месте.
 */
export interface FocusCandidate {
  tag: "input" | "textarea" | "select";
  type: string;
  visible: boolean;
  disabled: boolean;
  readOnly: boolean;
  marked: boolean;
  optOut: boolean;
}

const TEXT_TYPES = new Set(["", "text", "search", "email", "password", "tel", "url", "number"]);

export const isTextEntry = (c: Pick<FocusCandidate, "tag" | "type">): boolean => c.tag === "textarea" || (c.tag === "input" && TEXT_TYPES.has(c.type.toLowerCase()));

/** Индекс поля, которому нужно отдать фокус, или null — фокус получает само окно. */
export function chooseInitial(fields: FocusCandidate[], coarsePointer: boolean): number | null {
  const usable = fields.map((c, i) => ({ c, i })).filter(({ c }) => c.visible && !c.disabled && !c.readOnly && c.type.toLowerCase() !== "hidden" && !c.optOut);
  const marked = usable.find(({ c }) => c.marked);
  const pick = marked ?? usable[0];
  if (!pick) return null;
  if (!marked && !isTextEntry(pick.c)) return null;
  if (coarsePointer && isTextEntry(pick.c)) return null;
  return pick.i;
}

const FIELD = "input, textarea, select";

const visible = (el: HTMLElement): boolean => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length) && getComputedStyle(el).visibility !== "hidden";

const isCoarse = (): boolean => { try { return window.matchMedia("(pointer: coarse)").matches && window.matchMedia("(hover: none)").matches; } catch { return false; } };

/** Ставит фокус в окно: в нужное поле или (если поля нет, окно для чтения или сенсорный экран) на само окно. Возвращает элемент, получивший фокус. */
export function focusInitial(root: HTMLElement): HTMLElement {
  if (root.contains(document.activeElement) && document.activeElement !== root) return document.activeElement as HTMLElement;
  const els = Array.from(root.querySelectorAll<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>(FIELD));
  const rootOptOut = !!root.closest("[data-no-autofocus]");
  const idx = rootOptOut ? null : chooseInitial(els.map((el) => ({
    tag: el.tagName.toLowerCase() as FocusCandidate["tag"], type: el instanceof HTMLSelectElement ? "select" : (el as HTMLInputElement).type || "",
    visible: visible(el), disabled: el.disabled, readOnly: "readOnly" in el && !!el.readOnly, marked: el.hasAttribute("data-autofocus"), optOut: !!el.closest("[data-no-autofocus]"),
  })), isCoarse());
  const target = idx === null ? root : els[idx];
  target.focus({ preventScroll: false });
  return target;
}
