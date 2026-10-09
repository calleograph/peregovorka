"""Структурное извлечение протокола для небольшой локальной модели (Qwen3 1.7B).

Принцип: маленькая модель не собирает протокол и не «угадывает итог» по заметкам — она отвечает на несколько простых вопросов по небольшому фрагменту, а всё остальное
(проверка, объединение, дедупликация, сортировка, оформление) делает код:

  стенограмма → фрагменты (~6 000 знаков)
  → по КАЖДОМУ фрагменту отдельные узкие проходы, ответ строго JSON по схеме:
        темы · решения и предложения (status) · поручения (ответственный + что + срок — ОДНИМ объектом) · открытые вопросы
  → проверка каждого пункта по тексту (время реплики существует; ответственный — id из списка участников и подтверждён репликой; срок — это срок и назван в репликах;
    адреса, версии, числа, слова «завтра/августа/до конца недели» должны быть в репликах; «решение» с признаками отказа или вопроса не остаётся решением)
  → слияние и удаление повторов кодом (разные ответственные никогда не склеиваются) → детерминированный Markdown с источником у каждого пункта.

LLM нужна только для краткого резюме — и то по уже проверенным пунктам. Все проходы по одному фрагменту начинаются с одинакового системного промпта и текста фрагмента (отличается
только задание в конце), поэтому llama.cpp переиспользует уже обработанный текст, и каждый следующий проход стоит почти одну генерацию.
Оборвавшийся по лимиту длины или не разобравшийся ответ не принимается: проход повторяется по половинам фрагмента.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from ..integrations.llm import LlmClient, LlmError
from .deadlines import is_deadline_phrase, meeting_date, resolve
from .doc_render import build_document, document_to_markdown

log = logging.getLogger("app.extraction")

TS_RE = re.compile(r"^\[(\d{1,2}):(\d{2}):(\d{2})\]\s+([^:\[\]]{1,80}?):\s?(.*)$")
GUEST_RE = re.compile(r"\s*\((?:гость|guest)\)\s*$", re.I)
SECTION_MARK = {"СТЕНОГРАММА": "", "ЧАТ ВСТРЕЧИ": "чат", "СХЕМА НА ОБЩЕЙ ДОСКЕ": "схема"}

NONE_ID = "none"
NOT_SET = "не указан"
MAX_SPLIT_DEPTH = 2              # оборвавшийся/битый проход делится пополам не более двух раз подряд
MIN_SPLIT_LINES = 4

COMMON_SYSTEM = (
    "Ты — секретарь совещания. Работай только по тексту фрагмента стенограммы, ничего не выдумывай: нет данных — пустой список. Ответ — JSON строго по схеме. "
    "Каждый пункт сопровождай ts — временем реплики [ЧЧ:ММ:СС], где это сказано. Приветствия, шум и организационные реплики не включай.")
TASKS = {
    "topics": ("ЗАДАНИЕ: перечисли вопросы (темы), которые обсуждались в этом фрагменте. title — название темы, 2–6 слов. summary — одно-два законченных предложения: что именно обсуждали "
                "(только по тексту фрагмента; технические значения — точно как в репликах).", 700),
    "decisions": ("ЗАДАНИЕ: выпиши решения и предложения из этого фрагмента. status = decision — только если участники явно договорились (сказано «решили», «принимаем», «делаем», "
                  "«согласны», «утверждаем»). status = proposal — предложение, которое высказали, но не приняли, отклонили или отложили. Не включай поручения конкретным людям "
                  "и вопросы без ответа. text — суть решения одной фразой, без слов «Решение:», «Задача:», «Когда:».", 700),
    "tasks": ("ЗАДАНИЕ: выпиши поручения — кто, что и к какому сроку должен сделать. assignee — id участника, которому поручили или который сам взял задачу; "
              "если ответственный не назван — none. Поручение — это будущее действие, которое кто-то должен выполнить; уже случившееся, объявления и согласованные даты поручениями не являются. task — что сделать, коротко, без срока. deadline — срок слово в слово из реплики («в четверг к обеду», «до 23 октября», «завтра»); "
              "если срок не назван — пустая строка. Не подставляй «логичного» человека или срок.", 800),
    "questions": ("ЗАДАНИЕ: выпиши вопросы, на которые в этом фрагменте не получили ответа или решение по которым отложили. text — сам вопрос одной фразой.", 450),
}
SUMMARY_SYSTEM = ("Ты — секретарь совещания. Ниже проверенные пункты встречи. Напиши по ним краткое резюме (не более 8 строк): о чём говорили, какие решения приняты, "
                  "кто что делает и к какому сроку. Используй ТОЛЬКО эти пункты, ничего не добавляй и не меняй имена, числа и сроки.")


@dataclass
class Line:
    idx: int
    ts: str
    sec: int
    speaker: str
    text: str
    section: str = ""          # "" — стенограмма, "чат" / "схема" — другие источники смешанных материалов
    extra: bool = False        # строка без метки времени (заголовок, продолжение): время унаследовано

    def render(self) -> str:
        return f"[{self.ts}] {self.speaker}: {self.text}" if not self.extra else f"[{self.ts}] {self.text}"


@dataclass
class Person:
    pid: str
    full: str
    first: str
    last: str
    first_stem: str
    last_l: str


@dataclass
class Result:
    text: str = ""
    structured: dict = field(default_factory=dict)
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    parts: int = 0
    truncated: bool = False
    failed: int = 0                 # проходы по фрагментам, которые не удалось выполнить даже после деления
    ok_fragments: int = 0           # успешные проходы (0 — вызывающий код вернётся к прежнему текстовому режиму)
    warnings: list[str] = field(default_factory=list)


def norm(s: str) -> str:
    return s.lower().replace("ё", "е")


def _sec(h: str, m: str, s: str) -> int:
    return int(h) * 3600 + int(m) * 60 + int(s)


# --------------------------------------------------------------------------------------------- разбор стенограммы
def parse_transcript(text: str) -> tuple[list[str], list[Line]]:
    """(строки шапки, реплики). Шапка — до первой реплики; строки без метки времени после неё становятся продолжением с унаследованным временем."""
    header: list[str] = []
    lines: list[Line] = []
    last_ts, last_sec, section = "00:00:00", 0, ""
    for raw in text.splitlines():
        m = TS_RE.match(raw)
        if m:
            h, mi, s, who, body = m.groups()
            last_ts, last_sec = f"{int(h):02d}:{mi}:{s}", _sec(h, mi, s)
            lines.append(Line(len(lines), last_ts, last_sec, who.strip(), body.strip(), section))
        elif raw.strip():
            mark = next((v for k, v in SECTION_MARK.items() if k in raw and raw.strip().startswith("===")), None)
            if mark is not None:
                section = mark
            if not lines:
                header.append(raw.strip())
            elif not raw.strip().startswith("==="):
                lines.append(Line(len(lines), last_ts, last_sec, "", raw.strip(), section, True))
    return header, lines


def participants_of(header: list[str], lines: list[Line]) -> list[str]:
    names: list[str] = []

    def add(n: str) -> None:
        n = GUEST_RE.sub("", n).strip(" .—-")
        if n and n not in names and norm(n) not in ("неизвестный участник", "-", "—"):
            names.append(n)

    for h in header:
        if h.lower().startswith("участвовали:"):
            for n in h.split(":", 1)[1].split(","):
                add(n)
    for ln in lines:
        if not ln.extra and ln.section == "":
            add(ln.speaker)
    return names


def people_of(names: list[str]) -> list[Person]:
    """Участники со стабильными id (p01, p02 …): модель отвечает идентификатором из списка, а не именем «от себя»."""
    out = []
    for n, name in enumerate(names, 1):
        parts = name.split()
        first, last = parts[0], (parts[-1] if len(parts) > 1 else "")
        fl = norm(first)
        out.append(Person(f"p{n:02d}", name, first, last, fl[:-1] if len(fl) >= 4 else fl, norm(last)))
    return out


def similar_pairs(people: list[Person]) -> list[tuple[str, str]]:
    """Пары похожих ФИО (по id): одинаковое имя или фамилии с общим началом (Иван Петров / Иван Петренко, Алексей Мороз / Алексей Морозов, Игорь Соколов / Ирина Соколова)."""
    pairs = []
    for i, a in enumerate(people):
        for b in people[i + 1:]:
            same_first = bool(a.first_stem) and a.first_stem == b.first_stem
            close_last = bool(a.last_l and b.last_l) and (a.last_l.startswith(b.last_l[:4]) or b.last_l.startswith(a.last_l[:4]))
            first_vs_last = bool(a.last_l and b.last_l) and (norm(a.first)[:4] == b.last_l[:4] or norm(b.first)[:4] == a.last_l[:4])      # Иван Петров / Пётр Иванов
            if same_first or close_last or first_vs_last:
                pairs.append((a.pid, b.pid))
    return pairs


_MALE_END = ("", "а", "у", "ом", "ым", "ем", "е", "ых")
_FEM_END = ("а", "ой", "у", "ую", "ы", "е", "ою", "и")


def _surname_form(wl: str, last_l: str) -> bool:
    """Слово — форма этой фамилии? Мужская: Мороз, Морозу, Морозом; женская (на -а): Соколова, Соколовой, Соколову(ю). Так «Соколовой» — Ирина, «Морозова» — Морозов."""
    if not last_l:
        return False
    if last_l.endswith("а"):
        return wl.startswith(last_l[:-1]) and wl[len(last_l) - 1:] in _FEM_END
    return wl.startswith(last_l) and wl[len(last_l):] in _MALE_END


def mentions_direct(text: str, people: list[Person]) -> set[str]:
    """Кого текст называет в именительном падеже или звательной форме («Борис, отдаёшь…», «делает Виктор»): имя или фамилия слово в слово. Формы
    других падежей («предложение Бориса», «отдать Галине») не считаются: так называют того, о ком говорят, а не того, кто делает."""
    found: set[str] = set()
    for w in re.findall(r"[А-Яа-яЁёA-Za-z-]{3,}", text):
        wl = w.lower().replace("ё", "е")
        hits = [p for p in people if wl == p.first.lower().replace("ё", "е") or wl == p.last.lower().replace("ё", "е")]
        if len(hits) == 1:
            found.add(hits[0].full)
    return found


def mentions(text: str, people: list[Person]) -> tuple[set[str], bool]:
    """Каких участников называет текст (множество полных имён, есть ли двусмысленное упоминание). Фамилия определяет человека однозначно, имя без фамилии — только если оно
    у одного участника; «Соколову» (дательный Соколова или винительный Соколовой) и «Иван» при двух Иванах — двусмысленны и подтверждением не считаются."""
    found: set[str] = set()
    ambiguous = False
    for w in re.findall(r"[А-Яа-яЁёA-Za-z-]{3,}", text):
        wl = norm(w)
        hits = [p for p in people if _surname_form(wl, p.last_l)]
        if len(hits) == 1:
            found.add(hits[0].full)
            continue
        if len(hits) > 1:
            ambiguous = True
            continue
        cands = [p for p in people if p.first_stem and wl.startswith(p.first_stem) and 0 <= len(wl) - len(p.first_stem) <= 3]
        if len(cands) == 1:
            found.add(cands[0].full)
        elif len(cands) > 1:
            ambiguous = True
    return found, ambiguous


# --------------------------------------------------------------------------------------------- фрагменты, схемы, промпты
def chunk_lines(lines: list[Line], limit: int) -> list[list[Line]]:
    out: list[list[Line]] = []
    cur: list[Line] = []
    size = 0
    for ln in lines:
        n = len(ln.render()) + 1
        if size + n > limit and cur:
            out.append(cur)
            cur, size = [], 0
        cur.append(ln)
        size += n
    if cur:
        out.append(cur)
    return out


def _arr(props: dict, req: list[str]) -> dict:
    return {"type": "array", "items": {"type": "object", "properties": props, "required": req}}


def schema_for(kind: str, ids: list[str]) -> dict:
    s = {"type": "string"}
    if kind == "topics":
        body = {"topics": _arr({"title": s, "summary": s, "ts": s}, ["title", "summary", "ts"])}
    elif kind == "decisions":
        body = {"items": _arr({"status": {"type": "string", "enum": ["decision", "proposal"]}, "text": s, "ts": s}, ["status", "text", "ts"])}
    elif kind == "tasks":
        body = {"tasks": _arr({"assignee": {"type": "string", "enum": ids + [NONE_ID]}, "task": s, "deadline": s, "ts": s}, ["assignee", "task", "deadline", "ts"])}
    else:
        body = {"questions": _arr({"text": s, "ts": s}, ["text", "ts"])}
    return {"type": "object", "properties": body, "required": list(body)}


def system_for(people: list[Person]) -> str:
    roster = "\n".join(f"{p.pid} — {p.full}" for p in people)
    sim = similar_pairs(people)
    byid = {p.pid: p.full for p in people}
    hint = ("\nПохожие имена — это РАЗНЫЕ люди, не путай: " + "; ".join(f"{byid[a]} ≠ {byid[b]}" for a, b in sim) + ".") if sim else ""
    return f"{COMMON_SYSTEM}\nУчастники (id — имя):\n{roster}{hint}"


# --------------------------------------------------------------------------------------------- проверка пунктов
REJ_CUE = re.compile(r"не берём|не берем|не принима|отклон|против|не будем|не нужн|категорически|не согласн|слишком долго|не вариант", re.I)
OPEN_CUE = re.compile(r"открыт\w+ вопрос|не знаем|пока нет ответа|решение отклад|отложим|не определен|не определён|вопрос не решен|вопрос не решён|не решаем", re.I)
DEC_CUE = re.compile(r"решени|решили|договорились|(?<!не )согласн|утвержда|(?<!не )делаем|переносим|(?<!не )берём|(?<!не )берем|остаёмся|остаемся|не отключа|принимается|(?<!не )принято|вводим", re.I)
DEC_STRONG = re.compile(r"\bрешили\b|\bрешени[ея]\b|\bрешаем\b|\bдоговорились\b|\bпереносим\b|не отключа|\bутвержда\w+|\bостаёмся\b|\bостаемся\b|\bпринято\b", re.I)
# Глагол первого лица («сделаю», «проверю»). Прежнее правило «любое слово на -у/-ю» принимало существительные («оценку», «сборку») за обязательство:
# теперь только известные основы сразу перед окончанием -ю/-у (поэтому «проверку» не подходит, а «проверю» подходит).
FIRST_PERSON = re.compile(
    r"\b(?:подам|отдам|дам|создам|возьму|займусь|выложу|"
    r"(?:сдела|подготовл|провер|согласу|отправл|уточн|напиш|настро|добавл|исправл|запущ|внес|оформл|обнов|перезапущ|организ|позвон|свяж|"
    r"принес|пришл|договор|выясн|посмотр|разбер|изуч|протестир|развер|подключ|перенес|закаж|заверш|подпиш|утвержд|опубликов|найд|реш|устран|почин)"
    r"(?:ю|у|юсь|усь))\b", re.I)
PREPOSITIONS = ("в", "во", "на", "за", "через", "под", "про", "по", "к", "до", "с", "со", "о", "об", "для", "при", "над", "от", "из")
BIGVAL_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){1,3}(?:/\d+)?\b|\b\d{3,}\b")
TIMEWORD_RE = re.compile(
    r"(?:\b(?:до|в|во|на|к|с|по)\s+)?(?:конц[аеу]\s+(?:недели|месяца|года)|\bзавтра\w*|\bсегодня\b|\bпослезавтра\b|следующ\w+\s+(?:неделе|встрече|месяце)|"
    r"\b(?:понедельник|вторник|четверг|пятниц|суббот|воскресень)\w*|\bсред(?:а|ы|у|е|ой)\b|"
    r"\b(?:январ|феврал|апрел|июн|июл|август|сентябр|октябр|ноябр|декабр)\w+|\bмарта?\b|\bма(?:я|й|е)\b)", re.I)
QUESTION_SIGNAL = re.compile(r"открыт\w+ вопрос|не знаем|не знаю|не уверен|надо уточнить|нужно уточнить|уточню|уточнит|уточним|выясн|пока нет ответа|отложим|не решаем|вопрос не|не определ|решение отклад", re.I)
TIME_STEMS = {"завтр", "сегод", "послез", "понед", "вторн", "среды", "среду", "четве", "пятни", "суббо", "воскр", "недел", "месяц", "вечер", "утром", "обеда", "конца", "ночь ", "ночью", "следу"}
PROPOSAL_CUE = re.compile(r"предлаг|предложени|предложу|давайте|можно|может\b|можем|стоит|нужно|надо|а если|лучше|я бы|мы бы|хорошо бы|было бы|вариант|не берем|не берём|отказ|вместо", re.I)
LEAK_RE = re.compile(r"\s*[.;,]?\s*(?:время реплики|реплика|источник)\s*[:\-—].*$|,?\s*если не утвержден\w*\.?|,?\s*с согласия всех участников\.?", re.I)
LEAD_RE = re.compile(r"^\s*(?:решение|решили|принято решение|задача|поручение|открытый вопрос|вопрос)\s*[:\-—]\s*", re.I)
TAIL_RE = re.compile(r"\s*[.;,]?\s*(?:задача|когда|срок|ответственный|поручение)\s*:\s*.*$", re.I)


_ORD1 = {1: "перв", 2: "втор", 3: "трет", 4: "четв", 5: "пят", 6: "шест", 7: "седь", 8: "вось", 9: "девя"}
_ORD10 = {10: "деся", 11: "один", 12: "двен", 13: "трин", 14: "четы", 15: "пятн", 16: "шест", 17: "семн", 18: "восе", 19: "девя"}


def _day_stems(n: int) -> set[str]:
    if n in _ORD1:
        return {_ORD1[n]}
    if n in _ORD10:
        return {_ORD10[n]}
    if n == 20:
        return {"двад"}
    if n == 30:
        return {"трид"}
    if 21 <= n <= 29:
        return {"двад", _ORD1[n - 20]}
    if n == 31:
        return {"трид", "перв"}
    return set()


def _stems(s: str) -> set[str]:
    """Основы слов (4 знака) + порядковые слова для чисел 1–31, чтобы «23» и «двадцать третьего» считались одним и тем же."""
    out = {norm(w)[:4] for w in re.findall(r"[А-Яа-яЁёA-Za-z]{3,}", s)}
    for n in re.findall(r"(?<![\d.:])\d{1,2}(?![\d.:])", s):
        out |= _day_stems(int(n))
    return out | {w for w in re.findall(r"[A-Za-z]*\d[A-Za-z0-9]{2,}", norm(s))}


def _support(item_text: str, line_text: str) -> float:
    """Какая доля слов пункта есть в реплике (насколько реплика «содержит» пункт)."""
    x, y = _stems(item_text), _stems(line_text)
    return len(x & y) / len(x) if x else 0.0


def _jaccard(a: str, b: str) -> float:
    x, y = _stems(a), _stems(b)
    return len(x & y) / len(x | y) if x and y else 0.0


def snap(lines: list[Line], ts: str) -> Line | None:
    """Строка фрагмента с этим временем (или ближайшая по времени): модель указывает ts, а цитату берёт код — выдумать цитату нельзя."""
    if not lines:
        return None
    m = re.match(r"^\[?(\d{1,2}):(\d{2}):(\d{2})\]?$", (ts or "").strip())
    if not m:
        return None
    target = _sec(*m.groups())
    best = min(lines, key=lambda ln: abs(ln.sec - target))
    return best if abs(best.sec - target) <= 120 else None


def best_line(frag: list[Line], line: Line, text: str) -> Line:
    """Реплика-источник: та, что сильнее всего совпадает словами с пунктом. Время, названное моделью, — лишь подсказка: небольшая модель может промахнуться на десяток реплик,
    поэтому ищем по всему фрагменту, а к названной строке нужен заметный выигрыш, чтобы уйти от неё."""
    pos = next((i for i, x in enumerate(frag) if x.idx == line.idx), 0)
    best, best_score = line, _support(text, line.text)
    for i, x in enumerate(frag):
        sc = _support(text, x.text) - 0.01 * abs(i - pos)
        if sc > best_score + 0.12:
            best, best_score = x, sc
    return best


def window(frag: list[Line], line: Line, r: int = 2) -> list[Line]:
    pos = next((i for i, x in enumerate(frag) if x.idx == line.idx), 0)
    return frag[max(0, pos - r): pos + r + 1]


def source_of(line: Line, frag_no: int) -> dict:
    return {"fragment": frag_no, "ts": line.ts, "speaker": line.speaker, "section": line.section, "quote": line.text[:160]}


@dataclass
class Item:
    kind: str                      # topic | decision | proposal | task | open
    text: str
    ts: str
    sec: int
    assignee: str = ""             # полное имя участника ("" — не назначен)
    assignee_id: str = ""
    assignee_verified: bool = True
    assignee_doubtful: bool = False
    assignee_guess: str = ""       # кого назвала модель, если реплики этого не подтверждают (в протокол как ответственный не попадает)
    due: str = ""                  # фраза срока из реплики (проверенная)
    due_date: str = ""             # ISO-дата, если фраза однозначно переводится
    sources: list = field(default_factory=list)
    detail: str = ""               # для темы: что обсуждали (1–2 предложения)


def _clean_time_words(text: str, support: str, stats: dict) -> str:
    """Слова срока в формулировке пункта («до конца недели», «августа», «завтра») должны быть в ближайших репликах — иначе они убираются: модель их достроила сама."""
    sup = norm(support)

    def repl(m: re.Match) -> str:
        words = [norm(w) for w in re.findall(r"[А-Яа-яЁё]{4,}", m.group(0)) if norm(w) not in ("следующей", "следующ")]
        if all(w[:5] in sup for w in words):
            return m.group(0)
        stats["timewords_removed"] += 1
        return ""

    out = TIMEWORD_RE.sub(repl, text)
    return " ".join(out.split()).strip(" ,;—-")


def _first_person_commitment(line: str) -> bool:
    """В реплике говорящий берёт дело на себя: глагол первого лица («сделаю», «подам», «согласую»). Существительные после предлога («в пятницу», «на неделю») не считаются."""
    words = re.findall(r"[А-Яа-яЁё]+", line)
    for i, w in enumerate(words):
        if FIRST_PERSON.fullmatch(w) and not (i and words[i - 1].lower() in PREPOSITIONS):
            return True
    return False


def association_distances(text: str, task: str, people: list[Person]) -> dict[str, int | None]:
    """Насколько близко к словам задачи стоит каждый названный в реплике участник (в словах). В итоговой реплике «Галина чинит тесты, Дмитрий
    помогает …» названы несколько человек, и модель легко приписывает задачу не тому: берётся тот, чьё имя стоит рядом с глаголом/существительным задачи.
    None — слов задачи в реплике не нашли (по близости судить нельзя)."""
    # слова срока («до среды», «завтра») стоят рядом с любым именем и ничего не говорят о том, кто делает; поэтому в расчёт не берутся
    stems = {norm(w)[:5] for w in re.findall(r"[А-Яа-яЁё]{5,}", task)} - TIME_STEMS
    best: tuple[int, list[str], dict[str, list[int]], list[int]] | None = None
    for sent in re.split(r"(?<=[.!?])\s+", text):
        low = [norm(w) for w in re.findall(r"[А-Яа-яЁёA-Za-z0-9-]+", sent)]
        name_idx: dict[str, list[int]] = {}
        for i, wl in enumerate(low):
            hits = [p for p in people if wl == norm(p.first) or wl == norm(p.last)]
            if len(hits) == 1:
                name_idx.setdefault(hits[0].full, []).append(i)
        taken = {i for v in name_idx.values() for i in v}
        pos = [i for i, wl in enumerate(low) if i not in taken and len(wl) >= 5 and wl[:5] in stems]
        if pos and (best is None or len(pos) > best[0]):
            best = (len(pos), low, name_idx, pos)
    everyone: dict[str, int | None] = {full: None for full in mentions_direct(text, people)}
    if best is None:
        return everyone                       # слов задачи в реплике не нашли — по близости судить нельзя
    _n, _low, name_idx, pos = best
    out: dict[str, int | None] = {full: 999 for full in everyone}      # назван в другом предложении — к этой задаче отношения не имеет
    for full, idxs in name_idx.items():
        # имя обычно стоит перед глаголом («Галина чинит …»): имя после слов задачи считается чуть дальше
        out[full] = min((j - i) if j >= i else (i - j + 1) for i in idxs for j in pos)
    return out


def _assignee_confirmed(person: Person, frag: list[Line], src: Line, people: list[Person]) -> bool:
    """Ответственный подтверждён текстом: его называют в самой реплике-источнике (для короткого ответа вроде «Да, отдам» — в предыдущей реплике), либо он сам говорит
    в источнике и берёт дело на себя (глагол первого лица). Соседние чужие реплики и просто «говорящий» подтверждением не считаются."""
    if person.full in mentions_direct(src.text, people):
        return True
    pos = next((i for i, x in enumerate(frag) if x.idx == src.idx), 0)
    if len(src.text.split()) <= 10 and pos > 0:
        if person.full in mentions_direct(frag[pos - 1].text, people) and src.speaker == person.full:
            return True
    return src.speaker == person.full and _first_person_commitment(src.text)


def verify_pass(kind: str, raw: dict, frag: list[Line], frag_no: int, people: list[Person], stats: dict, base_date) -> list[Item]:
    """Сырые пункты одного прохода → проверенные Item. Всё, что нельзя подтвердить текстом, не принимается или помечается."""
    byid = {p.pid: p for p in people}
    all_text = " ".join(x.text for x in frag)
    items: list[Item] = []
    rows = raw.get({"topics": "topics", "decisions": "items", "tasks": "tasks", "questions": "questions"}[kind]) or []
    for it in rows:
        if not isinstance(it, dict):
            continue
        field_name = {"topics": "title", "decisions": "text", "tasks": "task", "questions": "text"}[kind]
        text = " ".join(str(it.get(field_name) or "").split())
        ln = snap(frag, str(it.get("ts") or ""))
        if len(text) < 3 or ln is None:
            stats["bad_ts" if ln is None and len(text) >= 3 else "empty"] += 1
            continue
        is_open_text = bool(re.match(r"^\s*открытый вопрос", text, re.I))
        text = LEAK_RE.sub("", TAIL_RE.sub("", LEAD_RE.sub("", text))).strip()
        damaged = False
        for v in BIGVAL_RE.findall(text):           # IP, версии, большие числа, которых нет в тексте фрагмента, — выдумка: значение убирается из пункта
            if v not in all_text:
                stats["fabricated"] += 1
                text = " ".join(text.replace(v, "").split())
                damaged = True
        if len(text.split()) < 2 or (damaged and re.search(r"\s[,.;)]|\b(?:на|в|до|с|к|по)\s*[,.;]", text)):
            if damaged:
                stats["damaged_dropped"] = stats.get("damaged_dropped", 0) + 1        # после удаления выдуманного значения остался обрывок фразы
            continue
        src = best_line(frag, ln, text)
        near = " ".join(x.text for x in window(frag, src))
        text = _clean_time_words(text, near, stats) if kind != "topics" else text
        if len(text.split()) < 2:
            continue
        pos = next((i for i, x in enumerate(frag) if x.idx == src.idx), 0)
        nxt = frag[pos + 1].text if pos + 1 < len(frag) else ""
        if kind == "topics":
            detail = " ".join(str(it.get("summary") or "").split())
            if any(v not in all_text for v in BIGVAL_RE.findall(detail)):
                detail = ""                       # описание с выдуманным значением (IP, версия, число) не принимается целиком
            items.append(Item("topic", text, src.ts, src.sec, detail=detail, sources=[source_of(src, frag_no)]))
        elif kind == "questions":
            pos = next((i for i, x in enumerate(frag) if x.idx == src.idx), 0)
            around = src.text + " " + (frag[pos + 1].text if pos + 1 < len(frag) else "")
            if not QUESTION_SIGNAL.search(around):
                stats["questions_unsupported"] += 1      # в репликах нет ни вопроса, ни «пока не знаем / надо уточнить» — вопрос выдуман
                continue
            items.append(Item("open", text, src.ts, src.sec, sources=[source_of(src, frag_no)]))
        elif kind == "decisions":
            said = "decision" if str(it.get("status")) == "decision" else "proposal"
            k = said
            own = src.text
            if is_open_text or text.rstrip().endswith("?") or (OPEN_CUE.search(own) and not DEC_CUE.search(own)):
                if not QUESTION_SIGNAL.search(own + " " + nxt):
                    stats["questions_unsupported"] += 1      # «открытым вопросом» названо то, что в репликах вопросом не является
                    continue
                k = "open"
            elif k == "decision" and ((REJ_CUE.search(own) and not DEC_CUE.search(own)) or (not DEC_CUE.search(own) and REJ_CUE.search(nxt) and not DEC_CUE.search(nxt))):
                k = "proposal"
            elif k == "decision" and not DEC_CUE.search(" ".join(x.text for x in window(frag, src, 3))):
                k = "proposal"           # явного «решили/принимаем/делаем» рядом нет — это обсуждение или предложение, а не решение
            elif k == "proposal" and DEC_STRONG.search(own) and not REJ_CUE.search(own) and not REJ_CUE.search(nxt):
                k = "decision"           # в самой реплике явно сказано «решили / решение / не отключаем …» — модель назвала решение предложением
            if k != said:
                stats["reclassified"] += 1
            if k == "proposal" and not PROPOSAL_CUE.search(own):
                stats["proposals_unsupported"] = stats.get("proposals_unsupported", 0) + 1      # в репликах нет ни предложения, ни обсуждения варианта
                continue
            items.append(Item(k, text, src.ts, src.sec, sources=[source_of(src, frag_no)]))
        else:  # tasks
            if text.rstrip().endswith("?"):
                stats["reclassified"] += 1
                items.append(Item("open", text, src.ts, src.sec, sources=[source_of(src, frag_no)]))
                continue
            aid = str(it.get("assignee") or NONE_ID)
            person = byid.get(aid)
            doubtful = aid != NONE_ID and person is None
            if doubtful:
                stats["assignee_unknown"] += 1
            verified = True
            guess = ""
            if person is not None:
                verified = _assignee_confirmed(person, frag, src, people)
                if not verified:
                    stats["assignee_unverified"] += 1
                    guess, person = person.full, None          # неподтверждённого ответственного не назначаем: «не определён» лучше неверного
            if person is not None and verified:
                direct = mentions_direct(src.text, people)
                if len(direct) >= 2 and person.full in direct:
                    dist = association_distances(src.text, text, people)
                    mine = dist.get(person.full)
                    closer = sorted((d, n) for n, d in dist.items() if d is not None and n != person.full)
                    if mine is not None and closer and closer[0][0] <= 3 and closer[0][0] + 2 <= mine:
                        stats["assignee_reassigned"] = stats.get("assignee_reassigned", 0) + 1
                        person = next(p for p in people if p.full == closer[0][1])     # задача стоит рядом с другим названным участником
                    elif mine is not None and mine > 4:
                        stats["assignee_unverified"] += 1
                        guess, person = person.full, None                               # названы несколько человек, а к этой задаче имя не примыкает
            if person is None and not doubtful:
                # модель не назвала или назвала неподтверждённо: если в реплике-источнике однозначно назван ровно один участник — это и есть ответственный (по тексту, а не по угадыванию)
                named = mentions_direct(src.text, people)
                if len(named) == 1:
                    only = next(p for p in people if p.full in named)
                    person, guess, verified = only, "", True
                    stats["assignee_from_text"] += 1
            due = " ".join(str(it.get("deadline") or "").split())
            due_date = ""
            if due:
                wl = norm(" ".join(x.text for x in window(frag, src, 1)))
                toks = list(_stems(due))
                nums = re.findall(r"\d+", due)
                ok = is_deadline_phrase(due) and (all(t in wl for t in toks) if toks else bool(nums) and all(n in wl for n in nums))
                if not ok:
                    stats["due_dropped"] += 1       # не срок или слов срока нет в ближайших репликах: не придумываем и не переносим со стороны
                    due = ""
                else:
                    d = resolve(due, base_date)
                    due_date = d.isoformat() if d else ""
            items.append(Item("task", text, src.ts, src.sec, person.full if person else "", person.pid if person else "", True, doubtful, guess, due, due_date,
                              [source_of(src, frag_no)]))
    return _one_assignee_per_task(items, stats)


def _one_assignee_per_task(items: list[Item], stats: dict) -> list[Item]:
    """Модель любит перечислить одну задачу на нескольких человек. Одна и та же задача в одной реплике — один ответственный: тот, кто сам её берёт (говорит в источнике),
    иначе тот, кого называют; остальные копии отбрасываются."""
    keep: list[Item] = []
    for it in items:
        if it.kind != "task":
            keep.append(it)
            continue
        dup = next((k for k in keep if k.kind == "task" and k.sources[0]["ts"] == it.sources[0]["ts"] and _jaccard(k.text, it.text) >= 0.7), None)
        if dup is None:
            keep.append(it)
            continue
        stats["duplicate_assignee"] += 1
        mine = lambda x: bool(x.assignee) and x.assignee == x.sources[0].get("speaker")      # noqa: E731
        if (mine(it) and not mine(dup)) or (it.assignee and not dup.assignee):
            keep[keep.index(dup)] = it
    return keep


# --------------------------------------------------------------------------------------------- слияние кодом
def merge_items(items: list[Item]) -> list[Item]:
    """Объединение повторов между фрагментами. Задачи с разными ответственными не склеиваются никогда; решение, повторяющее задачу, отбрасывается."""
    merged: list[Item] = []
    for it in sorted(items, key=lambda x: (x.sec, x.kind)):
        dup = None
        for m in merged:
            if m.kind != it.kind:
                continue
            if it.kind == "task" and m.assignee_id != it.assignee_id:
                continue
            if _jaccard(m.text, it.text) >= (0.5 if it.kind == "topic" else 0.6):
                dup = m
                break
        if dup is None:
            merged.append(it)
            continue
        dup.sources.extend(it.sources)
        if it.kind == "topic" and len(it.detail) > len(dup.detail):
            dup.detail = it.detail
        if not dup.due and it.due:
            dup.due, dup.due_date = it.due, it.due_date
        if it.kind == "task" and it.assignee_verified and not dup.assignee_verified:
            dup.assignee_verified = True
    tasks = [m for m in merged if m.kind == "task"]
    out = []
    for m in merged:
        if m.kind in ("decision", "proposal") and any(_jaccard(m.text, t.text) >= 0.6 for t in tasks):
            continue
        out.append(m)
    return out


# --------------------------------------------------------------------------------------------- вывод
def _cell(s: str) -> str:
    return " ".join(str(s).replace("|", "/").split())


def _src(it: Item) -> str:
    s = it.sources[0]
    who = f" {s['speaker']}" if s.get("speaker") else ""
    tag = f" ({s['section']})" if s.get("section") else ""
    quote = f" «{s['quote'][:110].rstrip()}{'…' if len(s['quote']) > 110 else ''}»" if s.get("quote") else ""
    more = f" (+{len(it.sources) - 1})" if len(it.sources) > 1 else ""
    return _cell(f"[{s['ts']}]{who}{tag}{quote}{more}")


def to_structured(items: list[Item]) -> dict:
    """Проверенная структура (она же — основа Markdown и резюме): у каждого пункта фрагмент, время и цитата-основание."""
    def pack(kinds: tuple[str, ...]):
        out = []
        for it in items:
            if it.kind in kinds:
                s = it.sources[0]
                d = {"text": it.text, **({"detail": it.detail} if it.kind == "topic" else {}), "source": {"chunk": s["fragment"], "ts": it.ts, "speaker": s.get("speaker"), "quote": s.get("quote")}, "also_in": [x["ts"] for x in it.sources[1:4]]}
                if it.kind == "task":
                    d.update(task=it.text, assignee=it.assignee or None, assignee_id=it.assignee_id or None, assignee_verified=it.assignee_verified,
                             deadline=it.due_date or None, deadline_phrase=it.due or None, assignee_guess=it.assignee_guess or None)
                out.append(d)
        return out

    return {"topics": pack(("topic",)), "decisions": pack(("decision",)), "tasks": pack(("task",)), "proposals": pack(("proposal",)), "open_questions": pack(("open",))}


def render_markdown(header: list[str], items: list[Item]) -> str:
    """Markdown документа из проверенных пунктов (оформление — services/doc_render.py)."""
    return document_to_markdown(build_document(header, items))


def facts_text(items: list[Item]) -> str:
    out = []
    for it in items:
        if it.kind == "decision":
            out.append(f"Решение: {it.text}")
        elif it.kind == "task":
            out.append(f"Поручение: {it.assignee or 'ответственный не назначен'} — {it.text} (срок: {it.due or 'не указан'})")
        elif it.kind == "proposal":
            out.append(f"Не принято: {it.text}")
        elif it.kind == "open":
            out.append(f"Открытый вопрос: {it.text}")
        elif it.kind == "topic":
            out.append(f"Тема: {it.text}")
    return "\n".join(out)


def fallback_summary(items: list[Item]) -> str:
    L = []
    dec = [i for i in items if i.kind == "decision"][:4]
    tasks = [i for i in items if i.kind == "task"][:4]
    op = [i for i in items if i.kind == "open"][:2]
    if dec:
        L.append("Решения: " + "; ".join(d.text for d in dec) + ".")
    if tasks:
        L.append("Поручения: " + "; ".join(f"{t.assignee or 'ответственный не назначен'} — {t.text}" + (f" ({t.due})" if t.due else "") for t in tasks) + ".")
    if op:
        L.append("Открытые вопросы: " + "; ".join(o.text for o in op) + ".")
    return "\n".join(L) or "Пунктов для резюме не выделено."


# --------------------------------------------------------------------------------------------- вызовы модели
def _parse(text: str) -> dict | None:
    try:
        d = json.loads(text)
    except ValueError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            return None
        try:
            d = json.loads(m.group(0))
        except ValueError:
            return None
    return d if isinstance(d, dict) else None


async def _run_pass(llm: LlmClient, kind: str, frag: list[Line], frag_no: int, system: str, schema: dict, people: list[Person], res: Result, stats: dict, base_date,
                    label: str, depth: int = 0) -> list[Item]:
    task, cap = TASKS[kind]
    # Первые сообщения всех проходов одинаковы (системный промпт + текст фрагмента): llama.cpp не обрабатывает фрагмент заново, различается только задание в конце.
    user = f"Фрагмент {label}:\n\n" + "\n".join(x.render() for x in frag) + f"\n\n=== {task}"
    raw, truncated = None, False
    try:
        r = await llm.complete(system, user, max_tokens=cap, json_schema=schema, temperature=0.0)
        res.calls += 1
        res.prompt_tokens += r.prompt_tokens or 0
        res.completion_tokens += r.completion_tokens or 0
        truncated = r.truncated
        raw = None if truncated else _parse(r.text)
    except LlmError as exc:
        if exc.code in ("not_configured", "unavailable", "unauthorized", "forbidden"):
            raise
        log.warning("Извлечение: ошибка вызова модели", extra={"code": exc.code})
    if raw is not None:
        res.ok_fragments += 1
        return verify_pass(kind, raw, frag, frag_no, people, stats, base_date)
    if truncated:
        res.truncated = True
        stats["length_retries"] += 1
    else:
        stats["json_retries"] += 1
    if depth < MAX_SPLIT_DEPTH and len(frag) >= MIN_SPLIT_LINES:      # оборвалось или не разобралось → делим пополам и повторяем
        mid = len(frag) // 2
        a = await _run_pass(llm, kind, frag[:mid], frag_no, system, schema, people, res, stats, base_date, f"{label}а", depth + 1)
        b = await _run_pass(llm, kind, frag[mid:], frag_no, system, schema, people, res, stats, base_date, f"{label}б", depth + 1)
        return a + b
    res.failed += 1
    return []


async def structured_pipeline(llm: LlmClient, *, kind: str, instruction: str, text: str, limit: int, attendees: dict | None = None) -> Result:
    res = Result()
    header, lines = parse_transcript(text)
    if not lines:
        return res
    names = participants_of(header, lines)
    people = people_of(names)
    ids = [p.pid for p in people]
    system = system_for(people)
    schemas = {k: schema_for(k, ids) for k in TASKS}
    base_date = meeting_date(header)
    frags = chunk_lines(lines, limit)
    res.parts = len(frags)
    stats = {"bad_ts": 0, "empty": 0, "fabricated": 0, "reclassified": 0, "assignee_unverified": 0, "assignee_unknown": 0, "due_dropped": 0, "timewords_removed": 0,
             "length_retries": 0, "json_retries": 0, "questions_unsupported": 0, "duplicate_assignee": 0, "assignee_from_text": 0}
    items: list[Item] = []
    for i, frag in enumerate(frags, 1):
        for pk in TASKS:
            items += await _run_pass(llm, pk, frag, i, system, schemas[pk], people, res, stats, base_date, f"{i} из {len(frags)}")
            if i == 1 and pk == "topics" and res.ok_fragments == 0:
                return res                          # первый же проход не разобрался даже после деления — модель или сервер не поддерживают формат; остальные не мучаем
    if res.ok_fragments == 0:
        return res
    merged = merge_items(items)
    res.structured = to_structured(merged)
    res.structured["participants"] = [{"id": p.pid, "name": p.full} for p in people]
    res.structured["stats"] = {**stats, "fragments": len(frags), "failed_passes": res.failed}
    if res.failed:
        res.warnings.append(f"Не удалось выполнить {res.failed} проходов по фрагментам стенограммы даже после деления на части — в протоколе могут быть пропуски. "
                            "Проверьте результат или сформируйте документ более сильной (внешней) моделью.")
    retries = stats["length_retries"] + stats["json_retries"]
    if retries:
        res.warnings.append(f"Проходов, которые пришлось повторить по меньшим частям (ответ оборван по лимиту длины — {stats['length_retries']}, не по формату — {stats['json_retries']}): {retries}.")
    if stats["assignee_unverified"]:
        res.warnings.append(f"У {stats['assignee_unverified']} поручений названный моделью ответственный не подтверждается репликами — ответственный не назначен (предположение модели показано в таблице).")
    if stats["assignee_unknown"]:
        res.warnings.append(f"У {stats['assignee_unknown']} поручений модель назвала человека, которого нет среди участников, — ответственный не назначен.")
    if stats["due_dropped"]:
        res.warnings.append(f"У {stats['due_dropped']} поручений названный моделью срок не подтверждается репликами — срок не указан (придумывать сроки запрещено).")
    if stats["timewords_removed"] or stats["fabricated"]:
        res.warnings.append(f"Из формулировок убрано выдуманных слов срока ({stats['timewords_removed']}) и значений, которых нет в стенограмме ({stats['fabricated']}).")
    # Итог: один абзац по уже проверенным пунктам (модель не пересказывает встречу заново и не переписывает таблицы — их собирает код)
    facts = facts_text(merged)
    wish = ""
    ins = (instruction or "").strip()
    if kind == "summary" and ins and len(ins) <= 400:
        wish = f"\nПожелания пользователя: {ins}"
    summary = ""
    summary_ok = False
    try:
        r = await llm.complete((SUMMARY_SYSTEM if kind == "summary" else SUMMARY_SYSTEM.replace("не более 8 строк", "не более 5 предложений")) + wish,
                               "Проверенные пункты встречи:\n\n" + facts)
        res.calls += 1
        res.prompt_tokens += r.prompt_tokens or 0
        res.completion_tokens += r.completion_tokens or 0
        if r.truncated or len(r.text.strip()) < 20:
            res.truncated = res.truncated or r.truncated
            if kind == "summary":
                res.warnings.append("Краткое резюме модель оборвала или не написала — показан перечень проверенных пунктов.")
        else:
            summary, summary_ok = r.text.strip(), True
    except LlmError:
        if kind == "summary":
            res.warnings.append("Краткое резюме не удалось получить от модели — показан перечень проверенных пунктов.")
    doc = build_document(header, merged, summary=summary if kind == "protocol" else "", incomplete=bool(res.failed), attendees=attendees if kind == "protocol" else None)
    res.structured["document"] = doc
    res.text = (summary if summary_ok else fallback_summary(merged)) if kind == "summary" else document_to_markdown(doc)
    return res
