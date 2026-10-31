"""Обезличивание текста перед отправкой во внешнюю языковую модель.

Одна сессия (Anonymizer) — одна встреча или один документ лаборатории: замены согласованы внутри сессии (одно лицо — одно обозначение,
один сервер — «[сервер 1]» во всех фрагментах) и нигде не сохраняются; между встречами соответствие не переносится.
Секреты маскируются всегда, независимо от профиля. После замены текст проверяется повторно: найденный секрет — ошибка (fail closed).
В журнал попадают только категории и числа; исходный текст, найденные значения и таблица замен не пишутся.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from . import names as nm
from .patterns import find_structured
from .secrets import Found, find_secrets

# ---------------------------------------------------------------------------------------------------- категории
CATEGORIES: dict[str, str] = {
    "person": "Люди: ФИО и имена",
    "organization": "Организации и контрагенты",
    "location": "Населённые пункты и места",
    "address": "Адреса",
    "phone": "Телефоны",
    "email": "Электронная почта",
    "ip": "IP- и MAC-адреса",
    "host": "Серверы, домены, сетевые пути",
    "url": "Внутренние ссылки",
    "login": "Учётные записи",
    "id_number": "Документы и идентификаторы: ИНН, СНИЛС, паспорт, ОГРН",
    "bank": "Банковские реквизиты и карты",
    "doc_number": "Номера договоров, счетов, заявок",
    "money": "Суммы и финансовые показатели",
    "project": "Внутренние проекты и системы (словарь)",
    "custom": "Свои термины (словарь)",
    "secret": "Пароли, токены, ключи (обязательно)",
}
ACTIONS = ("keep", "mask", "pseudonym")           # оставить | заменить меткой | заменить согласованным обозначением с номером
PERSON_FORMATS = ("short", "role")                # «Иван И.» | «Участник 2»

LABEL = {"organization": "Организация", "location": "Место", "address": "адрес", "phone": "телефон", "email": "почта", "ip": "IP", "host": "сервер",
         "url": "ссылка", "login": "учётная запись", "id_number": "идентификатор", "bank": "реквизит", "doc_number": "номер", "money": "сумма",
         "project": "Проект", "custom": "скрыто", "secret": "секрет удалён"}
_BARE = {"organization", "location", "project"}      # обозначения без скобок: «Организация 2», «Проект 1» — читаются как имена собственные

PROFILES: dict[str, dict] = {
    "basic": {"title": "Базовый", "person_format": "short", "org_scope": "legal", "actions": {
        "person": "pseudonym", "organization": "pseudonym", "location": "keep", "address": "mask", "phone": "mask", "email": "pseudonym", "ip": "pseudonym",
        "host": "pseudonym", "url": "pseudonym", "login": "pseudonym", "id_number": "mask", "bank": "mask", "doc_number": "pseudonym", "money": "mask",
        "project": "pseudonym", "custom": "pseudonym"}},
    "strict": {"title": "Строгий", "person_format": "role", "org_scope": "all", "actions": {
        "person": "pseudonym", "organization": "pseudonym", "location": "pseudonym", "address": "mask", "phone": "mask", "email": "mask", "ip": "mask",
        "host": "pseudonym", "url": "mask", "login": "mask", "id_number": "mask", "bank": "mask", "doc_number": "mask", "money": "mask",
        "project": "pseudonym", "custom": "pseudonym"}},
}
PRIORITY = {"secret": 100, "url": 90, "email": 88, "bank": 86, "id_number": 85, "login": 84, "ip": 82, "host": 80, "phone": 78, "address": 76,
            "doc_number": 74, "money": 72, "custom": 70, "project": 69, "organization": 60, "person": 55, "location": 40}
_LEGAL = re.compile(r"(?i)\b(?:ООО|ОАО|ЗАО|ПАО|АО|ИП|НКО|ФГУП|ГУП|МУП|ГК|ТОО|LLC|Ltd|Inc|GmbH)\b")


class PrivacyError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message

    def describe(self) -> str:
        return self.message


@dataclass
class Term:
    term: str
    category: str = "custom"           # project | organization | custom | host | person | location
    replacement: str = ""              # своя замена; пусто — обозначение категории


@dataclass
class Policy:
    actions: dict[str, str]
    person_format: str = "short"
    org_scope: str = "legal"           # legal — только с правовой формой или из словаря; all — все организации, найденные NER
    terms: list[Term] = field(default_factory=list)
    exceptions: list[str] = field(default_factory=list)
    internal_domains: list[str] = field(default_factory=list)
    public_domains: list[str] = field(default_factory=list)
    use_ner: bool = True

    @classmethod
    def from_profile(cls, name: str, **kw) -> "Policy":
        p = PROFILES.get(name) or PROFILES["basic"]
        return cls(actions=dict(p["actions"]), person_format=p["person_format"], org_scope=p["org_scope"], **kw)

    def action(self, cat: str) -> str:
        return "mask" if cat == "secret" else self.actions.get(cat, "keep")


@dataclass
class Replacement:
    start: int
    end: int
    category: str
    rule: str
    original: str        # только для лаборатории (ответ администратору); в журнал не попадает
    replacement: str


@dataclass
class Result:
    text: str
    replacements: list[Replacement]
    warnings: list[str] = field(default_factory=list)

    def stats(self) -> dict[str, int]:
        return dict(Counter(r.category for r in self.replacements))


_LEMMA_TOKEN = re.compile(r"[A-Za-zА-Яа-яЁё0-9]+(?:[-.][A-Za-zА-Яа-яЁё0-9]+)*")


def _lemmas(text: str, use_morph: bool) -> list[tuple[int, int, str]]:
    out = []
    for t in _LEMMA_TOKEN.finditer(text):
        w = t.group(0).lower()
        if use_morph and re.fullmatch(r"[а-яё-]+", w):
            w = nm.morph().parse(w)[0].normal_form
        out.append((t.start(), t.end(), w))
    return out


def _forms(text: str, use_morph: bool) -> list[tuple[int, int, frozenset[str]]]:
    """Слова с множеством возможных начальных форм («Альфу» → {альфа, альф}): словарь узнаёт слово в любой форме."""
    out = []
    for t in _LEMMA_TOKEN.finditer(text):
        w = t.group(0).lower()
        forms = {w}
        if use_morph and re.fullmatch(r"[а-яё-]+", w):
            forms |= {p.normal_form for p in nm.morph().parse(w)}
        out.append((t.start(), t.end(), frozenset(forms)))
    return out


class Anonymizer:
    def __init__(self, policy: Policy, participants: list[str] | tuple[str, ...] = ()):
        self.policy = policy
        self.roster = nm.Roster()
        self._numbers: dict[tuple[str, str], int] = {}
        self._counts: Counter = Counter()
        self._aliases: dict[int, str] = {}
        if policy.use_ner and policy.action("person") != "keep":
            for p in participants:
                self.roster.add_participant(p)
        # слова ФИО участников, которых нет в словаре (латиница, редкие фамилии): повторная проверка дозаменяет их, даже если разбор имени их не узнал
        self._odd_words = {w.lower() for p in participants for w in nm._WORD.findall(p)
                           if len(w) > 2 and w[:1].isupper() and not nm.morph().word_is_known(w.lower())} if policy.use_ner and policy.action("person") != "keep" else set()
        self._term_lemmas = [(t, tuple(w for _, _, w in _lemmas(t.term, policy.use_ner))) for t in policy.terms if t.term.strip()]
        self._exceptions = [tuple(w for _, _, w in _lemmas(e, policy.use_ner)) for e in policy.exceptions if e.strip()]

    # --------------------------------------------------------------------------------------- поиск
    @staticmethod
    def _seq(toks, i: int, lem: tuple[str, ...]) -> bool:
        return all(lem[k] in toks[i + k][2] for k in range(len(lem)))

    def _term_of(self, text: str):
        toks = _forms(text, self.policy.use_ner)
        return next((t for t, lem in self._term_lemmas if len(lem) == len(toks) and self._seq(toks, 0, lem)), None)

    def _dictionary(self, text: str, toks) -> list[Found]:
        out = []
        for term, lem in self._term_lemmas:
            n = len(lem)
            if not n:
                continue
            for i in range(len(toks) - n + 1):
                if self._seq(toks, i, lem):
                    out.append(Found(toks[i][0], toks[i + n - 1][1], term.category if term.category in CATEGORIES else "custom", "dictionary"))
        return out

    def _is_exception(self, text: str, s: int, e: int) -> bool:
        if not self._exceptions:
            return False
        toks = _forms(text[s:e], self.policy.use_ner)
        return any(len(lem) == len(toks) and self._seq(toks, 0, lem) for lem in self._exceptions)

    def find(self, text: str) -> list[tuple[Found, str]]:
        """Непересекающиеся находки (с приоритетом категорий) и их тип для NER: [(находка, PER|ORG|LOC|'')]."""
        cands: list[tuple[Found, str]] = [(f, "") for f in find_secrets(text)]
        cands += [(f, "") for f in find_structured(text, internal_domains=self.policy.internal_domains, public_domains=self.policy.public_domains)]
        toks = _forms(text, self.policy.use_ner) if self._term_lemmas else []
        cands += [(f, "") for f in self._dictionary(text, toks)]
        if self.policy.use_ner and any(self.policy.action(c) != "keep" for c in ("person", "organization", "location")):
            known = {p.parts.surname for p in self.roster.people if p.parts.surname}
            for s in nm.ner_spans(text, known):
                cat = {"PER": "person", "ORG": "organization", "LOC": "location"}[s.kind]
                if cat == "organization" and self.policy.org_scope == "legal" and not _LEGAL.search(text[max(0, s.start - 6):s.end]):
                    continue
                cands.append((Found(s.start, s.end, cat, "ner"), s.kind))
        cands.sort(key=lambda c: (-PRIORITY.get(c[0].category, 0), -(c[0].end - c[0].start), c[0].start))
        taken: list[tuple[Found, str]] = []
        for f, k in cands:
            if f.start >= f.end or self.policy.action(f.category) == "keep":
                continue
            if f.category != "secret" and self._is_exception(text, f.start, f.end):
                continue
            if any(f.start < g.end and g.start < f.end for g, _ in taken):
                continue
            taken.append((f, k))
        return sorted(taken, key=lambda c: c[0].start)

    # --------------------------------------------------------------------------------------- замены
    def _number(self, cat: str, key: str) -> int:
        k = (cat, key)
        if k not in self._numbers:
            self._counts[cat] += 1
            self._numbers[k] = self._counts[cat]
        return self._numbers[k]

    def _label(self, cat: str, value: str) -> str:
        act = self.policy.action(cat)
        base = LABEL.get(cat, "скрыто")
        if act == "pseudonym" and cat != "secret":
            key = " ".join(w for _, _, w in _lemmas(value, True)) if cat in ("organization", "location") else re.sub(r"\s+", " ", value.strip().lower())
            n = self._number(cat, key)                       # «в Нижнем Новгороде» и «Нижний Новгород» — одно место, один номер
            return f"{base} {n}" if cat in _BARE else f"[{base} {n}]"
        return base if cat in _BARE else f"[{base}]"

    def _short_alias(self, person: nm.Person) -> tuple[str, str]:
        """(имя в им. падеже, хвост «И.») — уникально в пределах сессии."""
        if person.number in self._aliases:
            first, tail = self._aliases[person.number].split("|")
            return first, tail
        p = person.parts
        first = p.first.capitalize() if p.first else ""
        sur = p.surname.capitalize()
        for k in (1, 2, 3):
            tail = (sur[:k] + ".") if sur else ""
            alias = f"{first}|{tail}"
            if alias not in self._aliases.values():
                break
        else:
            tail = f"{tail} ({person.number})"
            alias = f"{first}|{tail}"
        if not first:                                     # имени нет («Локальный администратор», одна фамилия): инициал без имени выглядит обрывком
            word = "Участник" if person.participant else "Лицо"
            first, alias = f"{word} {person.number}", f"{word} {person.number}|"
        self._aliases[person.number] = alias
        return alias.split("|")[0], alias.split("|")[1]

    def _person(self, text: str, prev: str = "") -> str | None:
        parts = nm.parse_name(nm._WORD.findall(text), prev)
        if not (parts.first or parts.surname or parts.patronymic):
            return None
        person = self.roster.resolve(parts)
        if person is None:                                # неоднозначное одиночное имя («Сергей» при двух Сергеях)
            return text if self.policy.person_format == "short" else nm.inflect_word("участник", parts.case, gram="")
        if self.policy.person_format == "role":
            word = "Участник" if person.participant else "Лицо"
            return f"{nm.inflect_word(word, parts.case, gram='')} {person.number}"
        first, tail = self._short_alias(person)
        if first.startswith(("Лицо ", "Участник ")):
            word, num = first.split(" ")
            return f"{nm.inflect_word(word, parts.case, gram='')} {num}"
        return f"{nm.inflect_word(first, parts.case, person.parts.gender)} {tail}".strip()

    def process(self, text: str) -> Result:
        found = self.find(text)
        out, pos, reps = [], 0, []
        last_end, last_prev = -10, ""
        for f, kind in found:
            orig = text[f.start:f.end]
            if f.category == "person":
                pw = re.findall(r"[А-Яа-яЁё]+", text[max(0, f.start - 12):f.start])
                prev = pw[-1] if pw else ""
                if re.fullmatch(r",?\s*(?:и|или|либо)\s+", text[last_end:f.start]) and last_prev:
                    prev = last_prev                         # «с Петровым и Сидоровой» — падеж второго, как у первого
                last_end, last_prev = f.end, prev
                rep = self._person(orig, prev)
                if rep is None or rep == orig:
                    continue
            elif f.rule == "dictionary":
                term = self._term_of(orig)
                rep = term.replacement if term is not None and term.replacement else self._label(f.category, term.term if term is not None else orig)
            else:
                rep = self._label(f.category, orig)
            if f.category in _BARE and rep.split(" ")[0] in ("Организация", "Место", "Проект"):     # «с Организацией 1», «по Проекту 2»
                pw = re.findall(r"[А-Яа-яЁё]+", text[max(0, f.start - 14):f.start])
                head, _, rest = rep.partition(" ")
                words = re.findall(r"[А-Яа-яЁё]+", orig)
                if pw and rest and text[f.start - 1:f.start] == " " and nm.morph().parse(pw[-1])[0].normal_form == head.lower():
                    rep = rest                              # «Проект Альбатрос» → «Проект 1», а не «Проект Проект 1»
                elif pw and text[f.start - 1:f.start] == " ":
                    case = nm.prep_case(pw[-1], nm.word_cases(words[0]) if words else set())
                    if case:
                        rep = f"{nm.inflect_word(head, case, gram='')} {rest}".strip()
            if rep.endswith(".") and text[f.end:f.end + 1] == ".":
                rep = rep[:-1]                              # «… Сергей К.» в конце предложения — одна точка
            out.append(text[pos:f.start])
            out.append(rep)
            reps.append(Replacement(f.start, f.end, f.category, f.rule, orig, rep))
            pos = f.end
        out.append(text[pos:])
        res = Result("".join(out), reps)
        self._verify(res)
        return res

    def _verify(self, res: Result) -> None:
        """Повторная проверка результата: секрет после замены — ошибка; фамилия участника, оставшаяся в тексте, — дозаменяется с предупреждением."""
        left = find_secrets(res.text)
        if left:
            raise PrivacyError("verification_failed", "После обезличивания в тексте остался секрет (правило " + left[0].rule + "): отправка остановлена.")
        if not self.policy.use_ner or self.policy.action("person") == "keep":
            return
        surnames = {p.parts.surname for p in self.roster.people if p.participant and p.parts.surname and len(p.parts.surname) > 2}
        if not surnames and not self._odd_words:
            return
        hits = [(s, e) for s, e, w in _lemmas(res.text, True)
                if w in surnames or res.text[s:e].lower() in self._odd_words or nm.parse_name([res.text[s:e]]).surname in surnames and res.text[s:s + 1].isupper()]
        if hits:
            buf, pos = [], 0
            for s, e in hits:
                buf += [res.text[pos:s], "[участник]"]
                pos = e
            buf.append(res.text[pos:])
            res.text = "".join(buf)
            res.warnings.append(f"Дозаменено упоминаний фамилий участников, пропущенных при разборе: {len(hits)}")
