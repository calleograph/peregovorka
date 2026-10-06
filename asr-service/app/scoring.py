"""Оценка качества распознавания на эталонном тексте: WER, CER и сохранность пунктуации. Чистые функции, без зависимостей."""
from __future__ import annotations

import re

_PUNCT_MARKS = ".,!?;:"
_WORD_RE = re.compile(r"[\w]+", re.UNICODE)


def normalize_words(text: str) -> list[str]:
    """Слова в нижнем регистре, ё=е, без знаков препинания — так сравнивается «что сказано», а не «как записано»."""
    return _WORD_RE.findall(text.lower().replace("ё", "е"))


def _edit_ops(ref: list[str], hyp: list[str]) -> tuple[int, list[tuple[int, int]]]:
    """Расстояние Левенштейна и выравнивание (пары индексов совпавших/заменённых слов ref↔hyp)."""
    n, m = len(ref), len(hyp)
    d = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        d[i][0] = i
    for j in range(m + 1):
        d[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (ref[i - 1] != hyp[j - 1]))
    i, j, pairs = n, m, []
    while i > 0 and j > 0:
        if d[i][j] == d[i - 1][j - 1] + (ref[i - 1] != hyp[j - 1]):
            pairs.append((i - 1, j - 1)); i -= 1; j -= 1
        elif d[i][j] == d[i - 1][j] + 1:
            i -= 1
        else:
            j -= 1
    pairs.reverse()
    return d[n][m], pairs


def wer(reference: str, hypothesis: str) -> float:
    ref, hyp = normalize_words(reference), normalize_words(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    return _edit_ops(ref, hyp)[0] / len(ref)


def cer(reference: str, hypothesis: str) -> float:
    ref, hyp = list(" ".join(normalize_words(reference))), list(" ".join(normalize_words(hypothesis)))
    if not ref:
        return 0.0 if not hyp else 1.0
    return _edit_ops(ref, hyp)[0] / len(ref)


def _marks_after_words(text: str) -> dict[int, str]:
    """{индекс слова: знак после него} — для слов с . , ! ? ; : сразу после."""
    out: dict[int, str] = {}
    idx = -1
    for m in re.finditer(r"[\w]+|[.,!?;:]", text.lower().replace("ё", "е")):
        tok = m.group(0)
        if tok in _PUNCT_MARKS:
            if idx >= 0:
                out[idx] = tok
        else:
            idx += 1
    return out


def punctuation_scores(reference: str, hypothesis: str) -> dict:
    """Сколько знаков препинания модель поставила и насколько они в тех же местах, что в эталоне (по выравниванию слов).

    f1 — по парам (слово, знак); «.» «!» «?» считаются одним классом конца предложения, чтобы не наказывать за выбор между ними.
    """
    cls = lambda ch: "end" if ch in ".!?" else ch  # noqa: E731
    ref_w, hyp_w = normalize_words(reference), normalize_words(hypothesis)
    ref_m, hyp_m = _marks_after_words(reference), _marks_after_words(hypothesis)
    _, pairs = _edit_ops(ref_w, hyp_w)
    h2r = {h: r for r, h in pairs}
    mapped = {h2r[h]: cls(mark) for h, mark in hyp_m.items() if h in h2r}
    ref_cls = {r: cls(mark) for r, mark in ref_m.items()}
    tp = sum(1 for r, c in mapped.items() if ref_cls.get(r) == c)
    precision = tp / len(hyp_m) if hyp_m else (1.0 if not ref_m else 0.0)
    recall = tp / len(ref_m) if ref_m else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    sentences = lambda t: len(re.findall(r"[.!?]", t))  # noqa: E731
    return {"ref_marks": len(ref_m), "hyp_marks": len(hyp_m), "ref_sentences": sentences(reference), "hyp_sentences": sentences(hypothesis),
            "precision": round(precision, 3), "recall": round(recall, 3), "f1": round(f1, 3)}
