import { useCallback } from "react";
import { focusInitial } from "./initialFocus";

/** Ссылка-обратный вызов для формы или панели, которая появляется по нажатию кнопки (не модальное окно): при появлении курсор встаёт в первое поле ввода по правилам `focusInitial`.
 *  Использование: `<form ref={focusOnMount}>`. Вызывается один раз при появлении элемента, поэтому фоновые перерисовки фокус не трогают. */
export function useFocusOnMount<T extends HTMLElement = HTMLElement>(): (el: T | null) => void {
  return useCallback((el: T | null) => { if (el) focusInitial(el); }, []);
}
