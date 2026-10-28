"""Подставная языковая модель для живых проверок интерфейса документов и карты (без настоящего провайдера): отвечает по схеме запроса.
Подключается стендом `devserver_live.py` при LIVE_MOCK_LLM=1 как транспорт httpx: backend ходит к «модели» по обычному пути, но сети нет.
Модель «ИДЁТ В СЕБЯ» только по обработке запроса: темы режутся по меткам времени из текста, решения/вопросы берутся из реплик с ключевыми словами.
Модель с именем `bad` всегда отвечает 401 (проверка «модель недоступна»), `empty` — пустым ответом с finish_reason=length (проверка причины пустого ответа)."""
from __future__ import annotations

import json
import re

import httpx

TOPICS = [("Замена сертификатов", "infra", "Обсудили замену сертификатов сервера до конца октября."),
          ("Бюджет на следующий год", "finance", "Согласовали рамки расходов на следующий год."),
          ("Доступ подрядчиков", "security", "Говорили про доступ подрядчиков к тестовому стенду.")]
STAMP = re.compile(r"\[(\d\d:\d\d:\d\d)\]\s*([^:\n]+):\s*(.+)")


def _reply(content: str, finish: str = "stop") -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": finish}], "usage": {"prompt_tokens": 400, "completion_tokens": 120}})


def handler(req: httpx.Request) -> httpx.Response:
    body = json.loads(req.content)
    model = body.get("model", "")
    if model == "bad":
        return httpx.Response(401, json={})
    if model == "empty":
        return _reply("", "length")
    user = body["messages"][-1]["content"]
    rows = STAMP.findall(user)
    if "response_format" in body and rows:
        n = len(rows)
        cuts = [0, max(1, n // 3), max(2, 2 * n // 3), n]
        topics = []
        for i, (title, cat, summary) in enumerate(TOPICS):
            a, b = cuts[i], max(cuts[i], cuts[i + 1] - 1)
            if a < n:
                topics.append({"title": title, "summary": summary, "category": cat, "ts": rows[a][0], "end": rows[min(b, n - 1)][0]})
        items = [{"status": "decision", "text": r[2].strip()[:190], "ts": r[0]} for r in rows if r[2].lower().startswith("решили")][:3]
        qs = [{"text": r[2].strip()[:190], "ts": r[0]} for r in rows if "?" in r[2]][:2]
        return _reply(json.dumps({"topics": topics, "items": items, "tasks": [], "questions": qs}, ensure_ascii=False))
    if rows:                                                   # свободный режим протокола: Markdown по реальным репликам
        dec = [r for r in rows if r[2].lower().startswith("решили")][:3]
        md = ["# Протокол встречи", "", "## Краткие итоги", "", "Обсудили сертификаты, бюджет и доступ подрядчиков.", "", "## Решения", ""]
        md += [f"{i}. {r[2].strip()} — _[{r[0]}] {r[1].strip()}_" for i, r in enumerate(dec, 1)] or ["1. Решений не принято."]
        return _reply("\n".join(md))
    return _reply("Обсудили сертификаты, бюджет и доступ подрядчиков. Решения приняты по первым двум вопросам.")
