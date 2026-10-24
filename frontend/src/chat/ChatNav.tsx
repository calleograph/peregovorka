import { Suspense, lazy, useEffect } from "react";
import { Icon } from "../components/Icons";
import { chats, useChats } from "./chatStore";
import "./chat.css";

const ChatWindow = lazy(() => import("./ChatWindow"));

/** Кнопка «Чаты» в верхнем меню: число непрочитанных (без заглушённых переписок). Окно открывается поверх текущей страницы — встреча и раздел не теряются. */
export function ChatNavButton() {
  const s = useChats();
  if (!s.ready || !s.enabled) return null;
  const n = s.unread;
  return (
    <button type="button" className={`chat-nav ${s.open ? "active" : ""}`} onClick={() => (s.open ? chats.closeWindow() : chats.openWindow())}
            aria-haspopup="dialog" aria-expanded={s.open} aria-label={n ? `Чаты: непрочитанных ${n}` : "Чаты"}>
      <Icon name="chat" size={18} />
      <span>Чаты</span>
      <span className="chat-badge" aria-hidden hidden={!n}>{n > 99 ? "99+" : n || ""}</span>
    </button>
  );
}

/** Окно чатов и всплывающие уведомления о новых сообщениях (когда окно закрыто или открыта другая переписка). */
export function ChatHost({ userId }: { userId: string }) {
  const s = useChats();
  useEffect(() => { void chats.start(); return () => chats.stop(); }, [userId]);
  return (
    <>
      {s.open && <Suspense fallback={null}><ChatWindow /></Suspense>}
      {s.toasts.length > 0 && (
        <div className="chat-toasts" role="status" aria-live="polite">
          {s.toasts.map((t) => (
            <div key={t.id} className="chat-toast">
              <button type="button" className="chat-toast-body" onClick={() => { chats.dismiss(t.id); if (t.chatId) chats.openWindow(t.chatId); }}>
                <b>{t.title}</b><span>{t.text}</span>
              </button>
              <button type="button" className="btn ghost mini" aria-label="Скрыть уведомление" onClick={() => chats.dismiss(t.id)}><Icon name="close" size={14} /></button>
            </div>
          ))}
        </div>
      )}
    </>
  );
}
