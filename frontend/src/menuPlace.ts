/** Положение контекстного меню у курсора так, чтобы оно не выходило за края экрана (чистая функция — проверяется тестами). */
export function placeMenu(x: number, y: number, w: number, h: number, vw: number, vh: number, pad = 8): { left: number; top: number } {
  let left = x, top = y;
  if (left + w + pad > vw) left = Math.max(pad, x - w);            // не помещается справа — раскрываем влево от курсора
  if (top + h + pad > vh) top = Math.max(pad, vh - h - pad);         // не помещается снизу — поднимаем
  return { left: Math.max(pad, left), top: Math.max(pad, top) };
}

/** Своё меню не должно перехватывать то, что пользователю нужно от браузера: выделенный текст (скопировать), поля ввода, Shift+ПКМ. */
export function shouldKeepNative(opts: { shift: boolean; targetTag: string; selection: string }): boolean {
  return opts.shift || /^(input|textarea|select)$/i.test(opts.targetTag) || opts.selection.trim().length > 0;
}
