#!/usr/bin/env python3
"""Собирает библиотеку значков комнат из набора Lucide (лицензия ISC — разрешает копирование и изменение при сохранении уведомления об авторских правах).

Использование: python3 scripts/build_room_icons.py <каталог_icons_из_пакета_lucide-static> [<файл_LICENSE>]
Пишет: frontend/src/roomIcons/icons.ts (разметка значков — встроена в приложение, внешних запросов нет), backend/app/assets/room_icons.json (допустимые ключи для проверки на сервере),
frontend/src/roomIcons/LICENSE-lucide.txt. Новые наборы добавляются так же: свой словарь CATALOG и разрешённая лицензия (см. docs/ROOM_ICONS.md).
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATEGORIES = [("it", "IT"), ("business", "Бизнес"), ("comms", "Связь и коммуникации"), ("docs", "Документы и обучение"), ("security", "Безопасность"),
              ("support", "Поддержка"), ("general", "Общие символы"), ("neutral", "Нейтральные")]
# файл Lucide -> (ключ в Peregovorka, название, категория, слова для поиска)
CATALOG: list[tuple[str, str, str, str]] = [
    # IT
    ("server", "Сервер", "it", "сервер хост"), ("monitor", "Монитор", "it", "экран дисплей"), ("laptop", "Ноутбук", "it", "компьютер"), ("cpu", "Процессор", "it", "чип железо"),
    ("database", "База данных", "it", "бд хранилище"), ("cloud", "Облако", "it", "сервис"), ("network", "Сеть", "it", "топология узлы"), ("wifi", "Wi-Fi", "it", "беспроводная связь"),
    ("terminal", "Терминал", "it", "консоль командная строка"), ("code", "Код", "it", "разработка программирование"), ("bug", "Ошибка", "it", "баг тестирование"),
    ("hard-drive", "Диск", "it", "накопитель хранение"), ("git-branch", "Ветка", "it", "git версии"), ("smartphone", "Смартфон", "it", "телефон мобильный"),
    ("keyboard", "Клавиатура", "it", "ввод"), ("mouse", "Мышь", "it", "ввод"), ("router", "Маршрутизатор", "it", "роутер сеть"), ("tv", "Телевизор", "it", "экран"),
    # Бизнес
    ("briefcase", "Портфель", "business", "работа дела"), ("chart-bar", "Гистограмма", "business", "график отчёт"), ("chart-line", "График", "business", "рост динамика"),
    ("chart-pie", "Диаграмма", "business", "доли отчёт"), ("handshake", "Рукопожатие", "business", "договор партнёры сделка"), ("building", "Здание", "business", "офис компания"),
    ("landmark", "Банк", "business", "финансы"), ("wallet", "Кошелёк", "business", "деньги бюджет"), ("credit-card", "Карта", "business", "оплата платежи"),
    ("coins", "Монеты", "business", "деньги финансы"), ("target", "Цель", "business", "задачи kpi"), ("award", "Награда", "business", "достижения"), ("calculator", "Калькулятор", "business", "бухгалтерия расчёты"),
    ("store", "Магазин", "business", "продажи торговля"), ("truck", "Доставка", "business", "логистика"), ("factory", "Завод", "business", "производство"), ("package", "Посылка", "business", "склад товары"),
    ("scale", "Весы", "business", "право юристы баланс"), ("gavel", "Молоток", "business", "суд юристы"),
    # Связь
    ("mic", "Микрофон", "comms", "звук запись"), ("headphones", "Наушники", "comms", "звук слушать"), ("headset", "Гарнитура", "comms", "звонки колл-центр"), ("phone", "Телефон", "comms", "звонок"),
    ("message-square", "Сообщение", "comms", "чат"), ("message-circle", "Чат", "comms", "переписка"), ("messages-square", "Обсуждение", "comms", "чаты диалог"), ("mail", "Почта", "comms", "письмо email"),
    ("video", "Видео", "comms", "камера видеосвязь"), ("radio", "Радио", "comms", "эфир"), ("megaphone", "Рупор", "comms", "объявление анонс"), ("speaker", "Колонка", "comms", "звук"),
    ("share-2", "Поделиться", "comms", "обмен"), ("link", "Ссылка", "comms", "связь"),
    # Документы и обучение
    ("folder", "Папка", "docs", "каталог файлы"), ("file-text", "Документ", "docs", "файл текст"), ("files", "Документы", "docs", "файлы"), ("book-open", "Книга", "docs", "чтение учёба"),
    ("graduation-cap", "Обучение", "docs", "шапочка курс"), ("clipboard", "Планшет", "docs", "список проверка"), ("notebook", "Блокнот", "docs", "заметки"), ("library", "Библиотека", "docs", "архив знания"),
    ("pencil", "Карандаш", "docs", "правка"), ("bookmark", "Закладка", "docs", "избранное"), ("presentation", "Презентация", "docs", "доклад слайды"), ("projector", "Проектор", "docs", "показ"),
    ("archive", "Архив", "docs", "хранение"), ("microscope", "Микроскоп", "docs", "исследования наука"), ("flask-conical", "Колба", "docs", "лаборатория наука"),
    # Безопасность
    ("shield", "Щит", "security", "защита"), ("shield-check", "Защищено", "security", "проверено безопасность"), ("lock", "Замок", "security", "доступ закрыто"), ("key", "Ключ", "security", "доступ пароль"),
    ("fingerprint", "Отпечаток", "security", "идентификация"), ("eye", "Наблюдение", "security", "мониторинг"), ("scan", "Сканирование", "security", "проверка"), ("siren", "Тревога", "security", "инцидент сирена"),
    # Поддержка
    ("life-buoy", "Спасательный круг", "support", "помощь поддержка"), ("wrench", "Гаечный ключ", "support", "ремонт обслуживание"), ("settings", "Настройки", "support", "шестерёнка"),
    ("hand-helping", "Помощь", "support", "поддержка рука"), ("heart-handshake", "Забота", "support", "клиенты сервис"), ("ticket", "Обращение", "support", "заявка тикет"),
    ("circle-help", "Справка", "support", "вопрос faq"), ("stethoscope", "Медицина", "support", "здоровье врач"),
    # Общие
    ("star", "Звезда", "general", "избранное"), ("flag", "Флажок", "general", "веха"), ("users", "Люди", "general", "команда группа"), ("user", "Человек", "general", "пользователь"),
    ("calendar", "Календарь", "general", "даты встреча"), ("clock", "Часы", "general", "время"), ("lightbulb", "Лампочка", "general", "идея"), ("rocket", "Ракета", "general", "запуск старт"),
    ("globe", "Глобус", "general", "мир международный"), ("house", "Дом", "general", "главная"), ("map-pin", "Метка", "general", "место адрес"), ("zap", "Молния", "general", "быстро энергия"),
    ("sun", "Солнце", "general", "день"), ("moon", "Луна", "general", "ночь"), ("flame", "Огонь", "general", "срочно"), ("leaf", "Лист", "general", "природа экология"),
    ("puzzle", "Пазл", "general", "интеграция"), ("compass", "Компас", "general", "направление"), ("gift", "Подарок", "general", "праздник"), ("trophy", "Кубок", "general", "победа"),
    ("coffee", "Кофе", "general", "перерыв кухня"), ("plane", "Самолёт", "general", "поездки"), ("camera", "Камера", "general", "фото"), ("palette", "Палитра", "general", "дизайн"),
    # Нейтральные
    ("circle", "Круг", "neutral", "фигура"), ("square", "Квадрат", "neutral", "фигура"), ("triangle", "Треугольник", "neutral", "фигура"), ("hexagon", "Шестиугольник", "neutral", "фигура соты"),
    ("diamond", "Ромб", "neutral", "фигура"), ("hash", "Решётка", "neutral", "тег"), ("layers", "Слои", "neutral", "уровни"), ("box", "Куб", "neutral", "объект"), ("infinity", "Бесконечность", "neutral", "цикл"),
    ("tag", "Метка-тег", "neutral", "категория"),
]


def inner(svg: str) -> str:
    m = re.search(r"<svg[^>]*>(.*)</svg>", svg, re.S)
    body = re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
    if re.search(r"<(script|style|foreignObject|image|a)\b|on\w+=|javascript:|href=", body, re.I):
        raise SystemExit("в значке недопустимая разметка")
    return body


def main(src: Path, lic: Path | None) -> None:
    items, missing = [], []
    for fname, name, cat, words in CATALOG:
        f = src / f"{fname}.svg"
        if not f.is_file():
            missing.append(fname); continue
        items.append({"key": fname, "name": name, "category": cat, "words": words, "body": inner(f.read_text(encoding="utf-8"))})
    if missing:
        raise SystemExit(f"нет в наборе: {missing}")
    keys = [i["key"] for i in items]
    assert len(keys) == len(set(keys)), "повторяющиеся ключи"
    cats = [{"id": i, "name": n} for i, n in CATEGORIES]
    out = ROOT / "frontend/src/roomIcons/icons.ts"
    head = ("// Файл создан scripts/build_room_icons.py из набора Lucide (ISC, см. LICENSE-lucide.txt). Вручную не править.\n"
            "export interface RoomIconDef { key: string; name: string; category: string; words: string; body: string }\n"
            "export const ROOM_ICON_CATEGORIES: { id: string; name: string }[] = " + json.dumps(cats, ensure_ascii=False) + ";\n"
            "export const ROOM_ICONS: RoomIconDef[] = " + json.dumps(items, ensure_ascii=False, indent=1) + ";\n")
    out.write_text(head, encoding="utf-8")
    (ROOT / "backend/app/assets/room_icons.json").write_text(json.dumps({"source": "lucide-static (ISC)", "keys": keys}, ensure_ascii=False, indent=1), encoding="utf-8")
    if lic and lic.is_file():
        shutil.copyfile(lic, ROOT / "frontend/src/roomIcons/LICENSE-lucide.txt")
    print(f"значков: {len(items)}, категорий: {len(cats)}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    main(Path(sys.argv[1]), Path(sys.argv[2]) if len(sys.argv) > 2 else None)
