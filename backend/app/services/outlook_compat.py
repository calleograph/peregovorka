"""Матрица совместимости надстройки: что доступно в каждом сочетании почтовой системы и клиента Outlook.

Основа — набор требований Mailbox API (Office.js), который поддерживает почтовая система и клиент: базовые функции надстройки работают с Mailbox 1.3, а подписка на отправку
приглашения (Smart Alerts, OnAppointmentSend) требует Mailbox 1.12. Локальный Exchange 2016/2019 поддерживает Mailbox API не выше 1.5, поэтому на нём подписка недоступна,
а изменения встречи, сделанные вне надстройки, отслеживает серверная сверка календаря (EWS). «verified=False» — по документации Microsoft, в этой установке не проверялось.
"""
from __future__ import annotations

BASIC = ["Создать конференцию из окна встречи", "Добавить ссылку в приглашение", "Открыть конференцию по ссылке", "Войти (пароль, 2FA, CAPTCHA в окне входа)", "Получить сведения о связанной конференции"]
ADVANCED = ["Передача времени и темы при отправке приглашения (Smart Alerts, Mailbox 1.12)", "Автоматическая синхронизация переноса", "Автоматическая обработка отмены"]

MATRIX = [
    {"id": "ex2016", "title": "Exchange 2016 (локальный) + классический Outlook для Windows", "mailbox_api": "до 1.5", "basic": True, "send_tracking": False,
     "server_sync": ["EWS"], "verified": False, "note": "Базовые функции — по документации Microsoft; в этой установке не проверялось."},
    {"id": "ex2019", "title": "Exchange 2019 (локальный) + классический Outlook для Windows", "mailbox_api": "до 1.5", "basic": True, "send_tracking": False,
     "server_sync": ["EWS"], "verified": False, "note": "Основной целевой сценарий. Перенос и отмену отслеживает серверная сверка EWS, а не надстройка."},
    {"id": "exse", "title": "Exchange Subscription Edition (локальный) + классический Outlook для Windows", "mailbox_api": "уточняйте у Microsoft", "basic": True, "send_tracking": False,
     "server_sync": ["EWS"], "verified": False, "note": "Предел Mailbox API для вашей сборки Exchange SE проверьте по документации Microsoft; до подтверждения подписка на отправку не включается."},
    {"id": "ex_owa", "title": "Exchange 2016/2019/SE + Outlook в браузере (OWA)", "mailbox_api": "до 1.5", "basic": True, "send_tracking": False,
     "server_sync": ["EWS"], "verified": False, "note": "Надстройка, установленная администратором, появляется в OWA после обновления политики; пользователь может добавить файл манифеста сам, если это разрешено политикой."},
    {"id": "ex_new", "title": "Exchange (локальный) + новый Outlook для Windows", "mailbox_api": "по клиенту, не выше возможностей сервера", "basic": None, "send_tracking": False,
     "server_sync": ["EWS"], "verified": False, "note": "Поддержка надстроек для локальных ящиков в новом Outlook зависит от версии клиента и политики организации; проверьте в вашей среде."},
    {"id": "m365", "title": "Microsoft 365 (Exchange Online) + Outlook для Windows (классический и новый), Mac, Outlook в браузере", "mailbox_api": "1.12 и выше", "basic": True,
     "send_tracking": True, "server_sync": ["Graph", "EWS"], "verified": False, "note": "Мобильные клиенты этим манифестом не поддержаны."},
]


def matrix(mail_kind: str, sync_on_send_requested: bool) -> dict:
    """Матрица плюс вывод для текущей настройки: что включено и что не применяется из-за ограничений почтовой системы."""
    onprem = mail_kind in ("exchange", "exchange_se")
    notes: list[str] = []
    if sync_on_send_requested and onprem:
        notes.append("Подписка на отправку приглашения запрошена, но не применяется: локальный Exchange поддерживает Mailbox API не выше 1.5 (нужен 1.12). Включите серверную сверку календаря EWS.")
    return {"basic_functions": BASIC, "advanced_functions": ADVANCED, "environments": MATRIX, "mail_kind": mail_kind, "notes": notes}
