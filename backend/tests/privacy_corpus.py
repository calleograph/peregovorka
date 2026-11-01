"""Регрессионный набор для распознавания людей: синтетическая стенограмма переговоров (структура — как у настоящей часовой встречи, все ФИО,
организации и сведения вымышлены) с разметкой персональных данных и измеритель качества.

Стенограмма собирается из шаблонов реплик с фиксированным зерном: два основных участника, третьи лица в разных падежах и формах (полное ФИО,
фамилия, имя и отчество, инициалы, иностранная и короткая фамилии), междометия, повторы, обрывки и ошибки распознавания речи, должности,
подразделения и организации. Три уровня качества распознавания: high (пунктуация и заглавные), medium (часть знаков и заглавных потеряна),
low (всё строчными, без знаков). Метрики — по упоминаниям людей: полнота, точность, ложные и пропущенные, сохранность прочего текста,
согласованность обозначений одного человека, целость временных меток и полей говорящего.

Отчёт: PYTHONPATH=backend python -m tests.privacy_corpus [число реплик]
"""
from __future__ import annotations

import random
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

P1 = "Воронцов Андрей Викторович"
P2 = "Зайцева Ольга Николаевна"
PARTICIPANTS = [P1, P2]

# формы упоминаний: (текст, кто). Кто: P1, P2 — участники; X1…X5 — третьи лица
FORMS = {
    "P1": {"io": "Андрей Викторович", "io_gen": "Андрея Викторовича", "surn_dat": "Воронцову", "surn_abl": "Воронцовым", "surn": "Воронцов", "first": "Андрей"},
    "P2": {"io": "Ольга Николаевна", "io_dat": "Ольге Николаевне", "surn_gen": "Зайцевой", "surn": "Зайцева", "first": "Ольга"},
    "X1": {"full": "Кузнецов Сергей Павлович", "fs": "Сергей Кузнецов", "surn": "Кузнецов", "surn_dat": "Кузнецову", "surn_gen": "Кузнецова",
           "io": "Сергей Павлович", "init": "С. П. Кузнецов", "fs_gen": "Сергея Кузнецова"},
    "X2": {"fs": "Мария Петрова", "surn_gen": "Петровой", "surn_abl": "Петровой", "fs_dat": "Марии Петровой", "surn": "Петрова"},
    "X3": {"surn": "Гусев", "surn_dat": "Гусеву", "surn_abl": "Гусевым", "fs": "Игорь Гусев"},
    "X4": {"surn": "Шульц", "fs": "Олег Шульц"},
    "X5": {"fs": "Анна Ким", "surn": "Ким"},
}
# контрольные слова: должны остаться как есть (в том числе в начале реплики, где заглавная буква ничего не значит)
FILLERS = ["Угу", "Ага", "Ого", "Хм", "Хмм", "Да-да-да", "Раз-раз-раз", "Ток-ток", "Так-так", "Ну", "Эээ", "Мм", "Окей", "Ладно", "Алло"]
ROLES = ["Бизнес-заказчик", "Закупщик", "Подрядчик", "Главбух", "Юрист", "Заказчик", "Исполнитель", "Техлид", "Руководитель проекта"]
DEPTS = ["отдел снабжения", "АХО", "бухгалтерия", "ИТ-отдел", "юридический отдел"]
ORGS = ["ООО «Вектор-Снаб»", "«Альфа-Логистик»", "Ростелеком", "АО «Северный терминал»"]

# шаблоны реплик: {F} — междометие, {R} — роль, {D} — подразделение, {O} — организация, {X1|fs} — упоминание (размечается), {OTHER|io} — собеседник
TEMPLATES = [
    ("{F}.", 9), ("{F}, {F}.", 3), ("{F}. Слышно меня?", 2), ("Раз-раз-раз. Проверка связи.", 1), ("Да-да-да, слышно.", 1),
    ("{R} просил ускорить поставку.", 3), ("{R} говорит, что стрелочки на схеме надо поправить.", 2), ("Бизнес-заказчик снова переносит сроки.", 1),
    ("Закупщик прислал новую таблицу.", 1), ("Подрядчика выбрали? Надо слеживать сроки.", 2), ("Согласен. {X3|surn} подготовит смету.", 2),
    ("Шах и мат, {X4|surn} нас опередил.", 1), ("Отделён ли бюджет? Я потерялся.", 1), ("Переносишься на пятницу? Возвращаешь документы {X1|surn_dat}?", 2),
    ("Ток-ток. Ага. {X5|fs} из {O} подключится.", 1), ("Нету подписи, {D} не согласовал.", 2), ("Соорганизует встречу {X3|surn} из отдела снабжения.", 1),
    ("{O} подключил канал, {F}.", 1), ("Проблинка в том, что полноплан не утверждён.", 1), ("Стрелочки уберём, полноплан пересоберём.", 1),
    ("Ахо не согласовало закупку.", 1), ("Хм. Потерялся звук.", 1), ("Хмм, а где {X2|fs}?", 1),
    ("Надо позвонить {X1|surn_dat}, он в курсе по {O}.", 2), ("{X1|full} вчера звонил по договору с {O}.", 2), ("С {X2|surn_abl} всё согласовали.", 2),
    ("Это вопрос к {X1|io}, он отвечает за склад.", 1), ("Передайте {X2|fs_dat} акт сверки.", 1), ("Договор подписал {X1|init}.", 1),
    ("{X3|surn} и {X4|surn} будут в четверг.", 1), ("{OTHER|io}, вы получили письмо?", 3), ("Это вопрос к {OTHER|surn_x}.", 2),
    ("Да, {OTHER|first}, согласен.", 1), ("Без {X1|fs_gen} не решим, {F}.", 1), ("{X2|surn_gen} сегодня нет, она в отпуске.", 1),
    ("Я спрошу у {OTHER|io_x}.", 1), ("{X3|fs} предложил перенести на {D}.", 1), ("{X5|surn} сделала расчёт, {R} его принял.", 1),
    ("Давайте зафиксируем: поставка в пятницу, оплата после акта.", 3), ("Ну, по смете вопросов нет, по срокам — есть.", 2),
    ("Коммерческое предложение отправим в {O} до среды.", 2), ("Руководитель проекта согласует с {D}.", 1), ("Потерялся, повторите, пожалуйста.", 1),
    ("Возвращаешь мне файл? Я поправлю стрелочки.", 1), ("Окей, тогда {X1|surn} и {X3|surn_abl} созвонятся.", 1), ("Ого, уже половина первого.", 1),
]
_OTHER = {"P1": {"io": "io", "surn_x": "surn_dat", "first": "first", "io_x": "io_gen"}, "P2": {"io": "io", "surn_x": "surn_gen", "first": "first", "io_x": "io"}}


@dataclass
class Mention:
    start: int
    end: int
    who: str
    speaker: bool = False


@dataclass
class Corpus:
    text: str
    mentions: list[Mention]
    lines: int
    quality: str
    controls: list[tuple[int, int, str]] = field(default_factory=list)      # контрольные слова (междометия, роли, обрывки) с позициями


def _lower(s: str) -> str:
    return s.lower()


def build(n_lines: int = 1100, quality: str = "high", seed: int = 7) -> Corpus:
    """Стенограмма ≈ 1 ч 40 мин при 1100 репликах. Разметка — по позициям в итоговом тексте (с учётом искажений уровня качества)."""
    rnd = random.Random(seed)
    qrnd = random.Random(seed + 1)
    parts: list[str] = []
    mentions: list[Mention] = []
    controls: list[tuple[int, int, str]] = []
    pos = 0

    def put(s: str, who: str | None = None, speaker: bool = False, control: bool = False) -> None:
        nonlocal pos
        if who:
            mentions.append(Mention(pos, pos + len(s), who, speaker))
        if control:
            controls.append((pos, pos + len(s), s))
        parts.append(s)
        pos += len(s)

    put(f"Переговорка: Закупки\nДата: 2026-10-20 (вторник)\nНачало: 10:00 — окончание: 11:42\nУчаствовали: ")
    put(P1, "P1", True)
    put(", ")
    put(P2, "P2", True)
    put("\n\n")
    weights = [w for _, w in TEMPLATES]
    t = 5
    controls_words = set(FILLERS) | set(ROLES) | {"Стрелочки", "Проблинка", "Полноплан", "Соорганизует", "Отделён", "Нету", "Потерялся", "Переносишься",
                                                 "Возвращаешь", "Подрядчика", "Согласен", "Шах", "Ахо", "слеживать", "полноплан", "стрелочки"}
    spk = "P1"
    for _ in range(n_lines):
        spk = spk if rnd.random() < 0.25 else ("P2" if spk == "P1" else "P1")
        other = "P2" if spk == "P1" else "P1"
        put(f"[{t // 3600:02d}:{t // 60 % 60:02d}:{t % 60:02d}] ")
        put(P1 if spk == "P1" else P2, spk, True)
        put(": ")
        tpl = rnd.choices(TEMPLATES, weights)[0][0]
        first = True
        for chunk in re.split(r"(\{[^}]+\})", tpl):
            if not chunk:
                continue
            m = re.fullmatch(r"\{(\w+)(?:\|(\w+))?\}", chunk)
            if not m:
                s = chunk
                if quality != "high":
                    s = _degrade(s, quality, qrnd, first)
                for w in re.finditer(r"[А-Яа-яЁё-]+", s):
                    if w.group(0).strip("-") in controls_words:
                        controls.append((pos + w.start(), pos + w.end(), w.group(0)))
                put(s)
            elif m.group(1) in ("F", "R", "D", "O"):
                s = rnd.choice({"F": FILLERS, "R": ROLES, "D": DEPTS, "O": ORGS}[m.group(1)])
                if quality == "low" or (quality == "medium" and not first and qrnd.random() < 0.5):
                    s = _lower(s)
                put(s, control=m.group(1) in ("F", "R"))
            else:
                who = other if m.group(1) == "OTHER" else m.group(1)
                key = _OTHER[who][m.group(2)] if m.group(1) == "OTHER" else m.group(2)
                s = FORMS[who][key]
                if quality == "low":
                    s = _lower(s)
                put(s, who)
            first = False
        put("\n")
        t += rnd.randint(3, 8)
    return Corpus("".join(parts), mentions, n_lines, quality, controls)


def _degrade(s: str, quality: str, rnd: random.Random, first: bool) -> str:
    """Искажения распознавания речи в тексте шаблона (не в упоминаниях): medium — часть запятых и заглавных теряется, low — строчные и без знаков."""
    if quality == "low":
        return re.sub(r"[,.?!;—]", "", s.lower())
    if rnd.random() < 0.4:
        s = s.replace(",", "")
    if first and rnd.random() < 0.3 and s[:1].isupper():
        s = s[0].lower() + s[1:]
    return s


# ------------------------------------------------------------------------------------------------------------- измерение
@dataclass
class Report:
    found: int = 0           # упоминания людей, заменённые полностью
    partial: int = 0         # заменена только часть упоминания
    missed: int = 0
    false_pos: int = 0       # замены «человек» вне упоминаний людей
    fp_words: Counter = field(default_factory=Counter)
    missed_words: Counter = field(default_factory=Counter)
    lost_chars: int = 0      # знаки вне персональных данных, попавшие в замену (потеря содержания)
    lost_words: Counter = field(default_factory=Counter)
    controls_kept: int = 0
    controls_total: int = 0
    controls_lost: Counter = field(default_factory=Counter)
    inconsistent: dict = field(default_factory=dict)          # кто → разные обозначения
    merged: list = field(default_factory=list)                # одно обозначение у разных людей
    persons_numbered: int = 0                                 # сколько разных «Лицо N» появилось
    broken_lines: int = 0
    total: int = 0

    @property
    def recall(self) -> float:
        return self.found / max(self.total, 1)

    @property
    def precision(self) -> float:
        return self.found / max(self.found + self.partial + self.false_pos, 1)


def _alias_key(rep: str) -> str:
    """Обозначение без падежа: «Лицу 3» → «лицо 3», «Сергея К.» → «сергей к.», «Участнику 2» → «участник 2»."""
    from app.privacy import names as nm

    words = rep.split()
    out = []
    for w in words:
        w = w.rstrip(".")
        if re.fullmatch(r"[А-Яа-яЁё]+", w):
            out.append(nm.morph().parse(w.lower())[0].normal_form)
        else:
            out.append(w.lower())
    return " ".join(out)


def evaluate(c: Corpus, res) -> Report:
    r = Report(total=len(c.mentions))
    pers = [x for x in res.replacements if x.category == "person"]
    covered = [False] * len(c.text)
    for x in pers:
        for i in range(x.start, x.end):
            covered[i] = True
    gold = [False] * len(c.text)
    for m in c.mentions:
        for i in range(m.start, m.end):
            gold[i] = True
    letters = lambda a, b: [i for i in range(a, b) if c.text[i].isalpha()]          # noqa: E731
    aliases: dict[str, set[str]] = defaultdict(set)
    for m in c.mentions:
        idx = letters(m.start, m.end)
        hit = sum(covered[i] for i in idx)
        if hit == len(idx):
            r.found += 1
            for x in pers:
                if x.start < m.end and m.start < x.end:
                    aliases[m.who].add(_alias_key(x.replacement))
        elif hit:
            r.partial += 1
            r.missed_words[c.text[m.start:m.end]] += 1
        else:
            r.missed += 1
            r.missed_words[c.text[m.start:m.end]] += 1
    for x in pers:
        if not any(gold[i] for i in range(x.start, x.end)):
            r.false_pos += 1
            r.fp_words[c.text[x.start:x.end]] += 1
    lost = [i for i in range(len(c.text)) if covered[i] and not gold[i] and c.text[i].isalnum()]
    r.lost_chars = len(lost)
    for x in pers:
        extra = "".join(ch if (not gold[i]) else " " for i, ch in enumerate(c.text[x.start:x.end], x.start)).split()
        for w in extra:
            if re.search(r"\w", w) and any(gold[i] for i in range(x.start, x.end)):
                r.lost_words[w] += 1
    allrep = sorted(res.replacements, key=lambda x: x.start)
    for a, b, w in c.controls:
        r.controls_total += 1
        if any(x.start < b and a < x.end for x in allrep):
            r.controls_lost[w] += 1
        else:
            r.controls_kept += 1
    r.inconsistent = {k: sorted(v) for k, v in aliases.items() if len(v) > 1}
    owner: dict[str, set[str]] = defaultdict(set)
    for k, v in aliases.items():
        for a in v:
            owner[a].add(k)
    r.merged = sorted((a, sorted(w)) for a, w in owner.items() if len(w) > 1)
    r.persons_numbered = len({re.sub(r"\D", "", x.replacement) for x in pers if x.replacement.startswith(("Лиц", "Участ")) and re.search(r"\d", x.replacement)})
    for line in res.text.splitlines():
        if line.startswith("[") and not re.match(r"\[\d\d:\d\d:\d\d\] [^:\n]+: \S", line):
            r.broken_lines += 1
    return r


def summary(r: Report) -> str:
    return (f"упоминаний {r.total}: найдено {r.found}, частично {r.partial}, пропущено {r.missed} · полнота {100 * r.recall:.1f} %, точность {100 * r.precision:.1f} % · "
            f"ложных {r.false_pos} · потеряно знаков текста {r.lost_chars} · контрольных слов сохранено {r.controls_kept}/{r.controls_total} · "
            f"несогласованных лиц {len(r.inconsistent)}, слитых {len(r.merged)} · номеров лиц {r.persons_numbered} · повреждённых строк {r.broken_lines}")


if __name__ == "__main__":
    import sys
    import time

    from app.privacy.engine import Anonymizer, Policy

    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1100
    for quality in ("high", "medium", "low"):
        c = build(n, quality)
        for prof in ("basic", "strict"):
            t0 = time.monotonic()
            res = Anonymizer(Policy.from_profile(prof), PARTICIPANTS).process(c.text)
            r = evaluate(c, res)
            print(f"\n== {quality} / {prof}: {len(c.text)} знаков, {time.monotonic() - t0:.1f} с\n" + summary(r))
            if r.fp_words:
                print("  ложные:", ", ".join(f"{w}×{k}" for w, k in r.fp_words.most_common(25)))
            if r.missed_words:
                print("  пропущены:", ", ".join(f"{w}×{k}" for w, k in r.missed_words.most_common(15)))
            if r.controls_lost:
                print("  контрольные потеряны:", ", ".join(f"{w}×{k}" for w, k in r.controls_lost.most_common(15)))
            if r.lost_words:
                print("  слова текста внутри замен:", ", ".join(f"{w}×{k}" for w, k in r.lost_words.most_common(10)))
            if r.inconsistent:
                print("  несогласованно:", r.inconsistent)
            if r.merged:
                print("  слиты:", r.merged)


# ---------------------------------------------------------------------------------------------- отложенная проверка
# Написана вручную, по ней правила не подбирались: другие люди, междометия, роли, сленг и ошибки распознавания. Разметка: {{кто|текст}}.
HOLDOUT_PEOPLE = ["Тарасов Денис Игоревич", "Полякова Наталья Сергеевна", "Абрамян Карен"]
HOLDOUT = """Переговорка: Планёрка ИТ
Участвовали: {{T|Тарасов Денис Игоревич}}, {{N|Полякова Наталья Сергеевна}}, {{K|Абрамян Карен}}

[09:00:03] {{T|Тарасов Денис Игоревич}}: Аллё. Мгм. Меня слышно?
[09:00:07] {{N|Полякова Наталья Сергеевна}}: Слышно-слышно. Не-не-не, подождите, я ещё подключаюсь.
[09:00:15] {{K|Абрамян Карен}}: Так-так-так. Короче, начинаем?
[09:00:19] {{T|Тарасов Денис Игоревич}}: Вот-вот. Финдиректор просил итоги по бюджету до обеда.
[09:00:26] {{N|Полякова Наталья Сергеевна}}: Эм, кадровик ещё не прислал штатку. Безопасник тоже молчит.
[09:00:33] {{K|Абрамян Карен}}: Сисадмин сказал, что серверная щас перегружена. Чё делать будем?
[09:00:40] {{T|Тарасов Денис Игоревич}}: Типа подождём. {{X1|Егоров}} обещал разобраться к среде.
[09:00:46] {{N|Полякова Наталья Сергеевна}}: Я напомню {{X1|Егорову}}. А {{X2|Мельник}} в отпуске?
[09:00:51] {{K|Абрамян Карен}}: Ой, не знаю. Спросите у {{X3|Новиковой}}, она ведёт график.
[09:00:58] {{T|Тарасов Денис Игоревич}}: {{N|Наталья Сергеевна}}, возьмёте на себя?
[09:01:04] {{N|Полякова Наталья Сергеевна}}: Возьму. Но генеральный хочет, чтобы аналитик тоже посмотрел.
[09:01:12] {{K|Абрамян Карен}}: Аналитик — это {{X4|Белов}}? Или {{X5|Ли}} из отдела продаж?
[09:01:18] {{T|Тарасов Денис Игоревич}}: {{X4|Белов}}. {{X5|Ли}} занимается CRM.
[09:01:24] {{N|Полякова Наталья Сергеевна}}: Ёлки, опять CRM. Блин, в прошлый раз {{X6|Игорь Валентинович}} полдня её чинил.
[09:01:31] {{K|Абрамян Карен}}: Нормально. Короче, кароче — я договорюсь с {{X6|Игорем Валентиновичем}}.
[09:01:38] {{T|Тарасов Денис Игоревич}}: Слушай, {{K|Карен}}, а договор с «Технопарк-Сервис» продлили?
[09:01:44] {{K|Абрамян Карен}}: Продлили. Юрист визирует, менеджер отправит оригиналы.
[09:01:50] {{N|Полякова Наталья Сергеевна}}: Угу-угу. Отлично. Ваще супер.
[09:01:55] {{T|Тарасов Денис Игоревич}}: Хорошо. {{N|Полякова}} — бюджет, {{K|Абрамян}} — договор, я — серверная.
[09:02:01] {{N|Полякова Наталья Сергеевна}}: Записала. Перенесём на четверг, если {{X1|Егоров}} не успеет.
[09:02:07] {{K|Абрамян Карен}}: Агась. Всё, отключаюсь. Пока-пока.
[09:02:10] {{T|Тарасов Денис Игоревич}}: Спасибо всем. Админ, выключай запись.
"""


def holdout() -> Corpus:
    text, mentions, pos, out = HOLDOUT, [], 0, []
    for m in re.finditer(r"\{\{(\w+)\|([^}]+)\}\}", text):
        out.append(text[pos:m.start()])
        start = sum(len(x) for x in out)
        out.append(m.group(2))
        mentions.append(Mention(start, start + len(m.group(2)), m.group(1)))
        pos = m.end()
    out.append(text[pos:])
    plain = "".join(out)
    words = {"Аллё", "Мгм", "Слышно-слышно", "Не-не-не", "Так-так-так", "Короче", "Вот-вот", "Финдиректор", "Эм", "Безопасник", "Сисадмин", "Чё", "Типа",
             "Ой", "Ёлки", "Блин", "Нормально", "Слушай", "Юрист", "Угу-угу", "Ваще", "Агась", "Пока-пока", "Админ", "Аналитик", "Возьму", "Записала"}
    controls = [(m.start(), m.end(), m.group(0)) for m in re.finditer(r"[А-Яа-яЁё]+(?:-[А-Яа-яЁё]+)*", plain) if m.group(0) in words]
    return Corpus(plain, mentions, plain.count("\n["), "holdout", controls)
