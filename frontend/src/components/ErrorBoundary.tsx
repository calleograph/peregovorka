import { Component, type ReactNode } from "react";

/** Исключение в отрисовке не должно оставлять пустую страницу. */
export default class ErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch(error: Error) { console.error("UI error:", error.message); }
  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <div className="center"><div className="card" style={{ maxWidth: 420 }}>
        <h2>Что-то пошло не так</h2>
        <p className="muted">Страница столкнулась с ошибкой. Обновите её; если повторяется — сообщите администратору.</p>
        <button className="btn primary" onClick={() => location.reload()}>Обновить страницу</button>
      </div></div>
    );
  }
}
