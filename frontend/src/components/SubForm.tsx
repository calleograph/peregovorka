import type { HTMLAttributes, KeyboardEvent } from "react";

/**
 * Группа полей со своей отправкой (Enter в поле или кнопка с onClick) — вместо вложенного элемента form внутри формы редактора комнаты.
 * Вложенная форма в HTML недопустима: браузер отправлял её «по-своему» (GET на тот же адрес — перезагрузка страницы, предупреждение о потере данных,
 * введённое пропадало), а событие отправки ещё и всплывало до формы редактора. Поэтому кнопки здесь — type="button".
 */
export default function SubForm({ onSubmit, children, ...rest }: { onSubmit: () => void } & Omit<HTMLAttributes<HTMLDivElement>, "onSubmit">) {
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key !== "Enter" || e.nativeEvent.isComposing || (e.target as HTMLElement).tagName !== "INPUT") return;
    e.preventDefault();          // иначе Enter отправил бы внешнюю форму редактора
    onSubmit();
  };
  return <div role="form" {...rest} onKeyDown={onKeyDown}>{children}</div>;
}
