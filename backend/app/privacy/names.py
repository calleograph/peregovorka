"""Люди, организации и места в русском тексте: NER (slovnet + navec, модели проекта Natasha), морфология (pymorphy3), связывание упоминаний.

Одно и то же лицо в разных формах («Иванов Иван Петрович», «Иван Петрович», «Ивану», «с Ивановым», «И. П. Иванов») сводится к одной записи:
части имени приводятся к именительному падежу с учётом рода, затем сопоставляются со списком участников встречи и с уже найденными лицами.
Модели загружаются один раз на процесс (≈ 30 МБ, ≈ 1 с) и работают на процессоре; вызывать из потока (asyncio.to_thread).
"""
from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

NLP_DIR = Path(os.environ.get("PRIVACY_NLP_DIR", "/opt/peregovorka/nlp"))
NAVEC_FILE = "navec_news_v1_1B_250K_300d_100q.tar"
NER_FILE = "slovnet_ner_news_v1.tar"

_lock = threading.Lock()
_models: dict = {}


class NlpUnavailable(RuntimeError):
    """Модели не найдены или не загрузились — обработка с распознаванием имён невозможна (fail closed: текст наружу не уходит)."""


def models():
    """(ner, morph) — загрузка один раз; повторные вызовы дешёвые."""
    with _lock:
        if "ner" not in _models:
            try:
                import pymorphy3  # noqa: PLC0415
                from navec import Navec  # noqa: PLC0415
                from slovnet import NER  # noqa: PLC0415

                navec = Navec.load(str(NLP_DIR / NAVEC_FILE))
                ner = NER.load(str(NLP_DIR / NER_FILE))
                ner.navec(navec)
                _models["ner"], _models["morph"] = ner, pymorphy3.MorphAnalyzer()
            except Exception as exc:  # noqa: BLE001
                raise NlpUnavailable(f"модели распознавания имён недоступны ({type(exc).__name__}): {NLP_DIR}") from None
        return _models["ner"], _models["morph"]


def morph():
    return models()[1]


# ------------------------------------------------------------------------------------------------ части имени
@dataclass
class NameParts:
    first: str = ""          # нормальная форма, нижний регистр
    patronymic: str = ""
    surname: str = ""
    initials: str = ""       # буквы инициалов («ип»)
    gender: str = ""         # masc | femn | ""
    case: str = "nomn"       # падеж упоминания (по главному слову)

    def key_parts(self) -> tuple[str, str, str]:
        return self.first, self.patronymic, self.surname


_INITIAL = re.compile(r"^[А-ЯЁ]\.$")
_WORD = re.compile(r"[А-ЯЁA-Z][а-яёa-z]+(?:-[А-ЯЁ][а-яё]+)?|[А-ЯЁ]\.")


ROLES = ("Name", "Surn", "Patr")
PREP_CASE = {"с": "ablt", "со": "ablt", "за": "ablt", "над": "ablt", "под": "ablt", "перед": "ablt", "между": "ablt",
             "у": "gent", "от": "gent", "для": "gent", "без": "gent", "до": "gent", "из": "gent", "около": "gent", "после": "gent", "кроме": "gent", "вместо": "gent",
             "к": "datv", "ко": "datv", "по": "datv", "благодаря": "datv", "согласно": "datv",
             "о": "loct", "об": "loct", "обо": "loct", "при": "loct"}
PREP_EITHER = {"в": ("accs", "loct"), "во": ("accs", "loct"), "на": ("accs", "loct"), "про": ("accs",), "через": ("accs",)}   # падеж — по форме самого слова
CASE_ORDER = ("nomn", "datv", "gent", "accs", "ablt", "loct")


def prep_case(prev: str, cases: set[str]) -> str | None:
    """Падеж после предлога prev для слова с возможными падежами cases (пусто — слово не склоняется: решает предлог)."""
    want = PREP_CASE.get(prev.lower())
    if want:
        return want if not cases or want in cases else None
    opts = [c for c in PREP_EITHER.get(prev.lower(), ()) if not cases or c in cases]
    return opts[0] if len(opts) == 1 else None


def word_cases(word: str) -> set[str]:
    """Падежи, в которых может стоять слово (существительное в единственном числе); несклоняемое — пусто."""
    cases = {p.tag.case for p in morph().parse(word) if ("NOUN" in p.tag or "ADJF" in p.tag) and "sing" in p.tag}
    return set() if "Fixd" in str(morph().parse(word)[0].tag) else cases


def _role(word: str, gender: str = ""):
    """(роль, разбор) — самый вероятный разбор слова как имени, фамилии или отчества в единственном числе; None — не похоже на часть имени."""
    best = None
    for p in morph().parse(word):
        g = next((r for r in ROLES if r in p.tag), None)
        if g and "sing" in p.tag and (not gender or p.tag.gender in (gender, None)):
            if best is None or p.score > best[1].score:
                best = (g, p)
    return best


def _nomn(p, gender: str) -> str:
    want = {"nomn", "sing"} | ({gender} if gender and p.tag.gender else set())
    f = p.inflect(want) or p.inflect({"nomn"})
    return (f.word if f else p.normal_form).lower()


def _case(word: str, role: str, gender: str, prev: str) -> str:
    cases = {p.tag.case for p in morph().parse(word) if role in p.tag and "sing" in p.tag and (not gender or p.tag.gender in (gender, None))}
    want = prep_case(prev, cases) if cases else None
    return want or next((c for c in CASE_ORDER if c in cases), "nomn")


def parse_name(tokens: list[str], prev: str = "") -> NameParts:
    """Слова одного упоминания → части имени (именительный падеж, род, падеж упоминания). prev — слово перед упоминанием (предлог задаёт падеж)."""
    out = NameParts()
    words = [t for t in tokens if _WORD.fullmatch(t)]
    initials = [w[0].lower() for w in words if _INITIAL.match(w)]
    words = [w for w in words if not _INITIAL.match(w)]
    roles = [_role(w) for w in words]
    gender = next((r[1].tag.gender for r in roles if r and r[0] in ("Name", "Patr") and r[1].tag.gender in ("masc", "femn")), "")
    if gender:
        roles = [_role(w, gender) or r for w, r in zip(words, roles)]
    head = None
    for w, r in zip(words, roles):
        role = r[0] if r else ""
        field_ = {"Name": "first", "Patr": "patronymic", "Surn": "surname"}.get(role, "surname")
        if getattr(out, field_):                     # второе слово той же роли («Иванов Петров») — считаем фамилией, если она свободна
            field_ = "surname" if not out.surname else ""
        if not field_:
            continue
        setattr(out, field_, _nomn(r[1], gender) if r else w.lower())
        if head is None or (role == "Name" and head[1] != "Name"):
            head = (w, role or "Surn")
    out.gender = gender
    out.initials = "".join(initials)
    out.case = _case(head[0], head[1], gender, prev) if head else "nomn"
    return out


_FEM_SURNAME = (("цкая", "цкий"), ("ская", "ский"), ("ова", "ов"), ("ева", "ев"), ("ёва", "ёв"), ("ина", "ин"), ("ына", "ын"))


def surname_key(s: str) -> str:
    """Фамилия без родового окончания: «полякова» и «поляков» (так разбирается одиночное «Полякова» — как род. падеж мужской) — одна основа."""
    return next((s[: -len(f)] + m for f, m in _FEM_SURNAME if s.endswith(f) and len(s) > len(f) + 1), s)


def same_person(a: NameParts, b: NameParts) -> bool:
    """Совместимы ли два набора частей имени (ни одна из известных частей не противоречит, есть хотя бы одна общая)."""
    common = 0
    for f in ("first", "patronymic", "surname"):
        x, y = getattr(a, f), getattr(b, f)
        if x and y:
            if x != y and not (f == "surname" and surname_key(x) == surname_key(y) and not (a.gender and b.gender and a.gender != b.gender)):
                return False
            common += 1
    if a.initials and b.first and a.initials[0] != b.first[0]:
        return False
    if b.initials and a.first and b.initials[0] != a.first[0]:
        return False
    if a.gender and b.gender and a.gender != b.gender and not (a.surname and b.surname):
        return False
    return common > 0 or bool((a.initials and b.surname and a.surname == b.surname))


@dataclass
class Person:
    """Одно лицо в документе: части имени (объединённые из всех упоминаний) и номер для строгого режима."""
    parts: NameParts
    number: int
    participant: bool = False
    mentions: int = 0

    def absorb(self, p: NameParts) -> None:
        for f in ("first", "patronymic", "surname", "gender"):
            if not getattr(self.parts, f) and getattr(p, f):
                setattr(self.parts, f, getattr(p, f))


@dataclass
class Roster:
    """Лица документа. Участники встречи регистрируются заранее (их имена известны точно); остальные — по мере упоминаний."""
    people: list[Person] = field(default_factory=list)

    def add_participant(self, display_name: str) -> None:
        words = _WORD.findall(display_name)
        if words and not any(_role(w) for w in words) and all(morph().word_is_known(w.lower()) for w in words):
            return                                     # «Локальный администратор» — обычные слова, а не ФИО: в список людей не входит (латиница и редкие имена — входят)
        p = parse_name(words)
        if not (p.first or p.surname):
            return
        for x in self.people:
            if same_person(x.parts, p) and x.parts.key_parts() == p.key_parts():
                return
        self.people.append(Person(p, len(self.people) + 1, participant=True))

    def _match(self, p: NameParts) -> list[Person]:
        match = [x for x in self.people if same_person(x.parts, p)]
        if len(match) > 1 and p.surname:                    # «Иванову» при Петре Иванове и Анне Ивановой — тот, чья форма фамилии совпадает точно
            match = [x for x in match if x.parts.surname == p.surname] or match
        return match

    def known(self, p: NameParts) -> Person | None:
        """Уже известное лицо (участник или подтверждённое упоминание) — без заведения нового и без учёта упоминания."""
        match = self._match(p)
        return match[0] if len(match) == 1 else None

    def resolve(self, p: NameParts) -> Person | None:
        """Лицо для упоминания: единственный совместимый — он; несколько (например, только имя «Сергей» при двух Сергеях) — None (неоднозначно)."""
        match = self._match(p)
        if len(match) == 1:
            match[0].absorb(p)
            match[0].mentions += 1
            return match[0]
        if not match:
            if not (p.surname or (p.first and p.patronymic)):
                return None                 # одно имя без фамилии у неизвестного лица — не заводим новое лицо (это может быть кто угодно)
            person = Person(NameParts(**{**p.__dict__}), len(self.people) + 1)
            person.mentions = 1
            self.people.append(person)
            return person
        return None


@dataclass
class Span:
    start: int
    end: int
    kind: str                # PER | ORG | LOC


def ner_spans(text: str, known_surnames: set[str] | frozenset[str] = frozenset()) -> list[Span]:
    """Сущности NER с дополнением морфологией: соседние слова-фамилии/имена/отчества присоединяются к лицу; одиночные слова, которые словарь
    определяет как фамилию или имя с высокой уверенностью и которые стоят с заглавной не в начале предложения, тоже считаются упоминанием лица."""
    ner, m = models()
    out: list[Span] = []
    for chunk_start, chunk in _chunks(text, 1500):
        for s in ner(chunk).spans:
            out.append(Span(chunk_start + s.start, chunk_start + s.stop, s.type))
    if known_surnames:                    # фамилии участников узнаются и там, где NER промолчал (в начале предложения, в списках)
        for t in _TOKEN.finditer(text):
            w = t.group(0)
            if _INITIAL.match(w):
                continue
            r = _role(w)
            if r and r[0] == "Surn" and _nomn(r[1], r[1].tag.gender or "") in known_surnames or w.lower() in known_surnames:
                out.append(Span(t.start(), t.end(), "PER"))
            elif any("Surn" in p.tag and _nomn(p, p.tag.gender or "") in known_surnames for p in m.parse(w)):
                out.append(Span(t.start(), t.end(), "PER"))
    out = _extend_people(text, out, m)
    return sorted(out, key=lambda s: s.start)


_TRANSLIT = [("shch", "щ"), ("sch", "щ"), ("zh", "ж"), ("kh", "х"), ("ts", "ц"), ("ch", "ч"), ("sh", "ш"), ("yu", "ю"), ("ya", "я"), ("yo", "ё"),
             ("iy", "ий"), ("yy", "ый"), ("ey", "ей"), ("ay", "ай"), ("oy", "ой")]
_LAT1 = str.maketrans("abvgdezijklmnoprstufhcwxyq", "абвгдезийклмнопрстуфхквхык")


def to_cyrillic(name: str) -> str:
    """Латинская запись русского имени → кириллица для разбора: «IVANOV MAKSIM» → «Иванов Максим», «Dmitry» → «Дмитрий»; кириллица — с заглавной."""
    words = []
    for w in name.split():
        x = w.lower()
        if re.search(r"[a-z]", x):
            x = re.sub(r"(?<=[bcdfghjklmnpqrstvwxz])y$", "iy", x).replace("x", "ks")
            for a, b in _TRANSLIT:
                x = x.replace(a, b)
            x = x.translate(_LAT1)
        words.append(x.capitalize())
    return " ".join(words)


_CAPS_SEQ = re.compile(r"(?<![\w.])(?:[A-Z][a-z]+|[A-Z]{2,}|[А-ЯЁ]{2,})(?:[ \t]+(?:[A-Z][a-z]+|[A-Z]{2,}|[А-ЯЁ]{2,})){1,2}(?![\w])")
_SURNAME_END = ("ов", "ова", "ев", "ева", "ёв", "ёва", "ин", "ина", "ын", "ына", "ский", "ская", "цкий", "цкая", "ко", "ук", "юк", "ич")


_INITIALS_NAME = re.compile(r"(?<![\w.])(?:[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?\s+[А-ЯЁ]\.\s?(?:[А-ЯЁ]\.)?|[А-ЯЁ]\.\s?(?:[А-ЯЁ]\.)?\s?[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?)(?![\w])")


def initials_people(text: str) -> list[tuple[int, int]]:
    """«Иванов М.С.», «М.С.Иванов», «Петрова Е.А.» — фамилия с инициалами узнаётся и там, где NER промолчал (в начале строки, в обрывках)."""
    m = morph()
    out = []
    for c in _INITIALS_NAME.finditer(text):
        word = re.search(r"[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?", c.group(0).replace(".", " ").strip())
        w = word.group(0) if word else ""
        if w and len(w) > 2 and (any("Surn" in p.tag for p in m.parse(w)) or w.lower().endswith(_SURNAME_END)):
            out.append((c.start(), c.end()))
    return out


def caps_people(text: str) -> list[tuple[int, int, str]]:
    """Имена, которые NER не видит: латиницей («ELENA PETROVA», «Dmitry Morozov») и прописными кириллицей («ВОРОНОВА МАРИНА СЕРГЕЕВНА»).
    [(начало, конец, кириллическая запись для разбора)] — только если есть имя и ещё одна часть ФИО (фамилия, отчество или второе имя)."""
    m = morph()
    out = []
    for c in _CAPS_SEQ.finditer(text):
        words = c.group(0).split()
        if not (all(re.fullmatch(r"[A-Za-z]+", w) for w in words) or all(w.isupper() for w in words)):
            continue
        cyr = to_cyrillic(c.group(0)).split()
        tags = [{g for p in m.parse(w.lower()) if p.score > 0.05 for g in ("Name", "Surn", "Patr") if g in p.tag} for w in cyr]
        names = sum("Name" in t for t in tags)
        other = sum(bool(t - {"Name"}) or (not t and w.lower().endswith(_SURNAME_END)) for t, w in zip(tags, cyr))
        if names and names + other >= 2 and all(t or w.lower().endswith(_SURNAME_END) for t, w in zip(tags, cyr)):
            if all(re.fullmatch(r"[A-Za-z]+", w) for w in words) and not any(m.word_is_known(w.lower()) and name_is_top(w) and "Name" in m.parse(w.lower())[0].tag for w in cyr):
                continue                                  # «Windows NTLM», «Exchange Online» — названия продуктов: латиницей ФИО — только с известным словарю именем
            out.append((c.start(), c.end(), " ".join(cyr)))
    return out


def _chunks(text: str, size: int):
    pos = 0
    while pos < len(text):
        end = min(len(text), pos + size)
        if end < len(text):
            cut = max(text.rfind("\n", pos, end), text.rfind(". ", pos, end))
            end = cut + 1 if cut > pos else end
        yield pos, text[pos:end]
        pos = end


_TOKEN = re.compile(r"[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?|[А-ЯЁ]\.")


def _name_like(w: str, m) -> bool:
    """Слово рядом с найденным именем — часть имени, если словарь хоть в одном разборе видит в нём имя, фамилию или отчество
    («Петровой» чаще разбирается как прилагательное, но рядом с «Марии» это фамилия)."""
    if _INITIAL.match(w):
        return True
    return any(g in p.tag for p in m.parse(w) for g in ("Name", "Surn", "Patr"))


# междометия, заполнители речи и звукоподражания: «Угу», «Хмм», «Эээ», «Ага» — никогда не имя
_FILLER = re.compile(r"(?i)(?:у+г+у+|а+г+а+|о+г+о+|х+м+|м+|э+|а+|о+|у+|ах+|ох+|ух+|эх+|ой|ай|ну|неа|ладно|окей|ок|алло|ау|кхм|тсс|аха+|ахах\w*)")
_NOT_NAME_POS = {"VERB", "INFN", "GRND", "PRTF", "PRTS", "ADVB", "INTJ", "PRCL", "CONJ", "PREP", "NPRO", "PRED", "COMP", "NUMR", "ADJS"}
_POS_TITLE = {"VERB": "глагол", "INFN": "глагол", "GRND": "деепричастие", "PRTF": "причастие", "PRTS": "причастие", "ADVB": "наречие", "INTJ": "междометие",
              "PRCL": "частица", "CONJ": "союз", "PREP": "предлог", "NPRO": "местоимение", "PRED": "предикатив", "COMP": "сравнительная степень",
              "NUMR": "числительное", "ADJS": "краткое прилагательное"}


def name_score(word: str, roles: tuple[str, ...] = ROLES) -> float:
    """Наибольшая уверенность словаря, что слово — имя, фамилия или отчество (roles) в единственном числе (0 — таких разборов нет)."""
    return max((p.score for p in morph().parse(word.lower()) if "sing" in p.tag and any(g in p.tag for g in roles)), default=0.0)


# служебные и рабочие термины, которые словарь знает как редкие имена или фамилии: в речи это не люди
_NOT_NAMES = {"токен", "токена", "токену", "токеном", "токене", "токены", "роут", "лид", "лида", "лиды", "техлид", "тимлид", "спринт", "релиз", "билд"}


def other_score(word: str) -> float:
    """Наибольшая уверенность словаря в обычном значении слова (не имя, не фамилия, не отчество): «роза» — цветок, «лука» — лук."""
    return max((p.score for p in morph().parse(word.lower()) if not any(g in p.tag for g in ROLES)), default=0.0)


def name_word(word: str) -> str:
    """Пусто — слово может быть частью имени; иначе причина, почему нет (для лаборатории): междометие, повтор, часть речи, обычное слово, организация."""
    if _INITIAL.match(word):
        return ""
    w = word.lower()
    parts = w.split("-")
    if len(parts) > 1 and len(set(parts)) == 1:
        return "повтор слова"                                # «Да-да-да», «Раз-раз-раз», «Ток-ток»
    if all(_FILLER.fullmatch(p) for p in parts):
        return "междометие"
    if len(w.replace("-", "")) < 2:
        return "короткое слово"
    if w in _NOT_NAMES:
        return "рабочий термин"
    if len(parts) > 1:                                       # двойная фамилия — каждая часть похожа на имя; иначе составное слово («Бизнес-заказчик»)
        return "" if all(name_score(p) > 0 for p in parts) else "составное слово, не имя"
    ps = morph().parse(w)
    score = name_score(w)
    top = ps[0]
    if score == 0:
        if any("Orgn" in p.tag for p in ps):
            return "организация"
        if top.tag.POS in _NOT_NAME_POS:
            return _POS_TITLE[top.tag.POS]
        return "обычное слово" if morph().word_is_known(w) else "нет признаков имени"
    if top.tag.POS in _NOT_NAME_POS and top.score > score:
        return _POS_TITLE[top.tag.POS]
    return ""


def name_is_top(word: str) -> bool:
    """Самый вероятный разбор слова — имя, фамилия или отчество («Иванов», «Сергей»), а не обычное слово, у которого есть и такое значение."""
    if _INITIAL.match(word):
        return True
    p = morph().parse(word.lower())[0]
    return "sing" in p.tag and any(g in p.tag for g in ROLES)            # «Техлид» разбирается как «имя во мн. числе» — не имя


def _extend_people(text: str, spans: list[Span], m) -> list[Span]:
    words = [(t.start(), t.end(), t.group(0)) for t in _TOKEN.finditer(text)]
    taken = [(s.start, s.end) for s in spans]

    def inside(a: int, b: int) -> bool:
        return any(x <= a and b <= y for x, y in taken)

    res = list(spans)
    for ws, we, w in words:                                  # 1. одиночные фамилии/имена, пропущенные NER (до присоединения соседей)
        if inside(ws, we) or _INITIAL.match(w):
            continue
        prev = text[max(0, ws - 2):ws]
        if ws == 0 or re.search(r"[.!?\n:]\s*$", text[max(0, ws - 3):ws]) or prev.endswith("«"):
            if m.word_is_known(w.lower()) and name_is_top(w) and name_score(w) >= 0.4:
                res.append(Span(ws, we, "PER"))               # в начале предложения заглавная буква ничего не значит — только слово, которое прежде всего фамилия или имя
            continue
        if any(("Surn" in p.tag or "Name" in p.tag) and p.score >= 0.3 for p in m.parse(w)):
            res.append(Span(ws, we, "PER"))
    for i, s in enumerate(res):                              # 2. соседние слова-части имени присоединяются к лицу
        if s.kind != "PER":
            continue
        a, b = s.start, s.end
        for ws, we, w in words:                               # вправо
            if ws < b:
                continue
            if text[b:ws].strip(" ") == "" and "\n" not in text[b:ws] and _name_like(w, m):
                b = we
            else:
                break
        for ws, we, w in reversed(words):                     # влево
            if we > a:
                continue
            if text[we:a].strip(" ") == "" and "\n" not in text[we:a] and _name_like(w, m):
                a = ws
            else:
                break
        res[i] = Span(a, b, "PER")
    res.sort(key=lambda s: (s.start, -(s.end - s.start)))
    merged: list[Span] = []
    for s in res:
        if merged and s.start < merged[-1].end:
            last = merged[-1]
            if s.end > last.end:
                merged[-1] = Span(last.start, s.end, last.kind if last.kind == "PER" or s.kind != "PER" else "PER")
            continue
        merged.append(s)
    return merged


# --------------------------------------------------------------------------------------------- склонение замен
def inflect_word(word: str, case: str, gender: str = "", gram: str = "Name") -> str:
    """Слово (имя или «участник») в падеже упоминания; не склоняется — как есть."""
    if not case or case == "nomn":
        return word
    m = morph()
    cands = [p for p in m.parse(word) if (gram in p.tag if gram else True)]
    if gender:
        cands = [p for p in cands if p.tag.gender == gender] or cands
    for p in cands or m.parse(word):
        f = p.inflect({case, "sing"})
        if f:
            w = f.word
            return w[0].upper() + w[1:] if word[:1].isupper() else w
    return word
