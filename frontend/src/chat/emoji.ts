// Эмодзи для поля ввода: популярные (по открытым рейтингам частоты использования Unicode Consortium и Emojipedia), сгруппированы, плюс недавние.
// Без внешних библиотек и сетевых загрузок: системный шрифт эмодзи.

export const EMOJI_GROUPS: { id: string; title: string; icon: string; items: string[] }[] = [
  { id: "smile", title: "Смайлы", icon: "😀", items: [..."😀😃😄😁😆😅😂🤣🙂😉😊😇🥰😍🤩😘😋😛😜🤪😎🤓🧐🤔🤨😐😑😶🙄😏😬😌😔😪😴😷🤒🤕🥵🥶🤯🥳😕🙁☹😮😲😳🥺😢😭😱😖😣😞😓😩😫🥱😤😡😠🤬🤗🤭🤫🤐😈"] },
  { id: "hands", title: "Жесты", icon: "👍", items: [..."👍👎👌🤌🤏✌🤞🤟🤘🤙👈👉👆👇☝✋🤚🖐🖖👋🤝🙏✍💪👏🙌👐🤲"] },
  { id: "hearts", title: "Сердца", icon: "❤️", items: ["❤️", "🧡", "💛", "💚", "💙", "💜", "🖤", "🤍", "🤎", "💔", "❣️", "💕", "💞", "💓", "💗", "💖", "💘", "💝"] },
  { id: "work", title: "Работа", icon: "💼", items: [..."💼📁📂📅📆📊📈📉📋📌📍📎🖇✏📝📄📃📑🗂🗓☎📞📱💻🖥⌨🖨💾💿🔒🔓🔑🔍💡⏰⏳⌛📢📣✉📧📨📩📦🏆🎯🚀🛠🔧⚙🧩"] },
  { id: "signs", title: "Знаки", icon: "✅", items: ["✅", "☑️", "✔️", "❌", "❎", "⚠️", "❗", "❓", "‼️", "⁉️", "💯", "🔥", "⭐", "🌟", "✨", "⚡", "💥", "🎉", "🎊", "🎁", "🆗", "🆕", "🆘", "⛔", "🚫", "♻️", "➕", "➖", "➡️", "⬅️", "⬆️", "⬇️", "🔴", "🟠", "🟡", "🟢", "🔵", "🟣", "⚫", "⚪"] },
  { id: "life", title: "Разное", icon: "☕", items: [..."☕🍵🍰🎂🍕🍔🍎🍏🍌🍓🍒🍉🍺🍷🥂🌞🌙⛅🌧❄🌈🌸🌹🌻🌲🐱🐶🦊🐻🐼🐸🐵🙈🙉🙊🐢⚽🏀🎮🎵🎶🎧📷🎬✈🚗🚕🚲🏠🏢"] },
];

const KEY = "pg:chat:emoji:recent";
const MAX_RECENT = 24;

export function recentEmoji(): string[] {
  try { const v = JSON.parse(localStorage.getItem(KEY) || "[]"); return Array.isArray(v) ? v.filter((x) => typeof x === "string").slice(0, MAX_RECENT) : []; } catch { return []; }
}

export function rememberEmoji(e: string): void {
  try { localStorage.setItem(KEY, JSON.stringify([e, ...recentEmoji().filter((x) => x !== e)].slice(0, MAX_RECENT))); } catch { /* не запоминается */ }
}

/** Вставка у курсора: возвращает новый текст и позицию курсора после вставки. */
export function insertAt(value: string, start: number, end: number, piece: string): { value: string; caret: number } {
  const a = Math.max(0, Math.min(start, value.length)), b = Math.max(a, Math.min(end, value.length));
  return { value: value.slice(0, a) + piece + value.slice(b), caret: a + piece.length };
}
