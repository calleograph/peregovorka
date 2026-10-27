/** Нейтральная страница отказа: адрес клиента вне разрешённых подсетей. Ничего не раскрывает: ни списка сетей, ни настроек, ни причины. */
export default function RestrictedPage() {
  return (
    <main className="restricted-page" role="alert">
      <div className="restricted-card">
        <h1>Доступ ограничен</h1>
        <p>Доступ к сервису ограничен. Обратитесь к администратору организации.</p>
      </div>
    </main>
  );
}
