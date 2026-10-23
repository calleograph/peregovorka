"""Стенд (см. README.md): наполняет «Предстоящие встречи» тем же путём, что надстройка Outlook — токен надстройки и POST /api/v1/outlook/meetings
с приглашёнными. Встречи: через 12 мин, через 3 ч, завтра, через полночь, идущая (вход через API), отменённая, серия, временная комната.
Сотни встреч этим путём не создать (у API надстройки предел частоты) — их проверяет backend/tests/test_upcoming.py. Запуск: python tests/live/seed_upcoming.py [--base URL]"""
import argparse
import uuid
from datetime import datetime, timedelta, timezone

import httpx

P = argparse.ArgumentParser()
P.add_argument("--base", default="http://127.0.0.1:8000")
A = P.parse_args()
API = A.base + "/api/v1"
PEOPLE = [{"email": "bob@corp.test", "name": "Борис Мартынов"}, {"email": "dave@corp.test", "name": "Дмитрий Соколов"},
          {"email": "partner@example.org", "name": "Партнёр Внешний"}, {"email": "lawyer@example.org", "name": "Юрист Консультант", "optional": True},
          {"email": "x@example.org", "name": ""}]


def session(login: str, password: str) -> httpx.Client:
    c = httpx.Client(timeout=20)
    r = c.post(f"{API}/auth/login", json={"login": login, "password": password})
    r.raise_for_status()
    c.headers["X-CSRF-Token"] = c.get(f"{API}/auth/me").json()["csrf_token"]
    return c


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


root = session("root", "root-pass")
root.put(f"{API}/admin/settings/outlook", json={"enabled": True}).raise_for_status()
root.put(f"{API}/admin/settings/general", json={"temp_rooms_enabled": True}).raise_for_status()
alice = session("alice", "alice-pass")
for who in (("bob", "bob-pass"), ("dave", "dave-pass")):
    session(*who)                                    # сотрудники уже входили: приглашённые сопоставляются по адресу
tok = alice.post(f"{API}/outlook/token").json()["token"]
addin = httpx.Client(timeout=20, headers={"Authorization": f"Bearer {tok}"})
now = datetime.now(timezone.utc)


def meet(subject: str, start: datetime, minutes: int, people=PEOPLE, **extra) -> dict:
    body = {"key": "seed" + uuid.uuid4().hex, "subject": subject, "start": iso(start), "end": iso(start + timedelta(minutes=minutes)), "attendees": people,
            "organizer": "alice@corp.test", "tz": "Europe/Moscow", **extra}
    r = addin.post(f"{API}/outlook/meetings", json=body)
    r.raise_for_status()
    return r.json()


meet("Планёрка отдела продаж", now + timedelta(minutes=12), 30)
meet("Разбор архитектуры платёжного шлюза и план миграции на новую версию API для партнёров", now + timedelta(hours=3), 90)
meet("Согласование бюджета на четвёртый квартал", (now + timedelta(days=1)).replace(hour=7, minute=0, second=0, microsecond=0), 60, people=PEOPLE[:2])
live = meet("Ежедневный стендап", now - timedelta(minutes=8), 30, people=PEOPLE[:3])
alice.post(f"{API}/rooms/{live['id']}/join", json={}).raise_for_status()          # «Идёт» — по фактической встрече
cancelled = meet("Встреча с поставщиком", now + timedelta(hours=5), 45, people=PEOPLE[2:4])
addin.delete(f"{API}/outlook/meetings/{cancelled['id']}").raise_for_status()
first = now + timedelta(days=2)
meet("Еженедельный обзор проектов", first, 60, people=PEOPLE, recurring=True, tz="UTC",       # серия с шаблоном: на главной — ближайшее занятие
     recurrence={"type": "weekly", "props": {"interval": 1, "days": [("mon", "tue", "wed", "thu", "fri", "sat", "sun")[first.weekday()]]},
                 "start_date": first.date().isoformat(), "end_date": None, "start_time": first.strftime("%H:%M"), "duration": 60})
bob = session("bob", "bob-pass")
bob.post(f"{API}/rooms/temporary", json={"name": "Срочный созвон по инциденту"})
print("Готово: встречи созданы")
