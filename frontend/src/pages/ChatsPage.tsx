import { useEffect } from "react";
import { Icon } from "../components/Icons";
import { chats, useChats } from "../chat/chatStore";

/** Раздел «Чаты»: открывает окно чатов (то же, что кнопка «Чаты» в меню). После закрытия окна остаётся короткая страница с кнопкой — а не страница переговорок,
 *  которую раньше видел человек за закрытым окном. Ссылка `/chats` используется и мобильным приложением для вкладки «Чаты». */
export default function ChatsPage() {
  const s = useChats();
  useEffect(() => { if (s.ready && s.enabled) chats.openWindow(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [s.ready, s.enabled]);
  if (!s.ready) return <div className="muted">Загрузка…</div>;
  if (!s.enabled) return <section className="card"><h1>Чаты</h1><p className="muted">Корпоративные чаты отключены администратором.</p></section>;
  return (
    <section className="card chats-home">
      <h1>Чаты</h1>
      <p className="muted">{s.unread ? `Непрочитанных сообщений: ${s.unread}.` : "Переписки с коллегами вне встреч."}</p>
      <button type="button" className="btn primary" onClick={() => chats.openWindow()}><Icon name="chat" size={18} /> Открыть чаты</button>
    </section>
  );
}
