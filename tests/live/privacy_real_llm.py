"""Полный цикл обезличивания с НАСТОЯЩЕЙ языковой моделью по API (OpenAI, Anthropic, OpenAI-совместимый шлюз): синтетическая стенограмма →
обезличивание тем же кодом, что в продукте → протокол тем же конвейером и подсказками (run_llm_pipeline) → проверка того, что ушло в сеть и что вернулось.

Данные только синтетические. Ключ берётся из файла (PG_LLM_KEY_FILE) или переменной PG_LLM_KEY и нигде не печатается. Каждый исходящий запрос
перехватывается на уровне HTTP-транспорта, поэтому проверяется именно то, что получил провайдер, а не промежуточный текст.

Запуск на сервере (образ backend содержит модели распознавания имён):
    docker compose exec -T -e PG_LLM_TYPE=openai -e PG_LLM_MODEL=<модель> -e PG_LLM_KEY_FILE=/run/key backend python - < tests/live/privacy_real_llm.py
Локально: PYTHONPATH=backend PRIVACY_NLP_DIR=~/.cache/peregovorka-nlp python tests/live/privacy_real_llm.py
Переменные: PG_LLM_TYPE (openai | anthropic | openai_compatible | custom), PG_LLM_MODEL, PG_LLM_BASE_URL (для шлюза), PG_LLM_PROFILE (basic | strict),
PG_LLM_SHOW=1 — показать обезличенный запрос и ответ (синтетические), PG_LLM_OUT — файл с итогом в JSON (без ключа).
Итог: строки PASS/FAIL и «ИТОГ: PASS|FAIL»; код выхода 0 только при полном успехе.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import httpx

from app.integrations.llm import LlmClient
from app.privacy.engine import Anonymizer, Policy
from app.services.protocols import run_llm_pipeline
from app.services.settings import LlmSettings

PEOPLE = ["Иванов Иван Петрович", "Петрова Мария Сергеевна", "Кузнецов Сергей Андреевич"]
TRANSCRIPT = """[00:00:05] Иванов Иван Петрович: Коллеги, начинаем. Сервер crm-app-02.corp.example.org, адрес 192.0.2.17, ночью дважды падал.
[00:00:40] Петрова Мария Сергеевна: Бюджет на перенос — 2,4 млн руб. по договору № ДП-2026/118 с ООО «СтройИнвест», ИНН 7707083893.
[00:01:10] Кузнецов Сергей Андреевич: Пароль администратора стенда P@ssw0rd-Crm-2026 надо сменить, токен ghp_abcdefghijklmnopqrstuvwxyz0123456789 отозвать.
[00:01:35] Иванов Иван Петрович: Мария Сергеевна, почта для согласования m.petrova@example.org, телефон +7 916 123-45-67.
[00:02:00] Петрова Мария Сергеевна: У подрядчика Смирнова диагноз — сахарный диабет 2 типа, график надо щадящий. СНИЛС его 112-233-445 95.
[00:02:30] Иванов Иван Петрович: Решили: перенос CRM на новый кластер в пятницу, ответственный Кузнецов, срок — до пятницы.
"""
SENSITIVE = ["Иванов", "Петрова", "Петровой", "Кузнецов", "Смирнов", "crm-app-02", "192.0.2.17", "2,4 млн", "ДП-2026/118", "СтройИнвест", "7707083893",
             "P@ssw0rd", "ghp_abcdef", "m.petrova@", "123-45-67", "диабет", "112-233-445 95"]


class Recorder(httpx.AsyncHTTPTransport):
    """Настоящий транспорт, который запоминает тело каждого исходящего запроса (только в памяти)."""
    def __init__(self, **kw):
        super().__init__(**kw)
        self.bodies: list[str] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        raw = request.content.decode("utf-8", "replace")
        try:
            raw = json.dumps(json.loads(raw), ensure_ascii=False)        # \\u-экранирование не должно прятать утечку от проверки
        except ValueError:
            pass
        self.bodies.append(raw)
        return await super().handle_async_request(request)


def settings() -> LlmSettings:
    key = os.environ.get("PG_LLM_KEY") or (Path(os.environ["PG_LLM_KEY_FILE"]).read_text().strip() if os.environ.get("PG_LLM_KEY_FILE") else "")
    if not key or not os.environ.get("PG_LLM_MODEL"):
        sys.exit("Нужны PG_LLM_MODEL и ключ (PG_LLM_KEY_FILE или PG_LLM_KEY).")
    base = os.environ.get("PG_LLM_BASE_URL", "")
    return LlmSettings(enabled=True, provider="external", type=os.environ.get("PG_LLM_TYPE", "openai"), base_url=base, model=os.environ["PG_LLM_MODEL"],
                       api_key=key, allow_http=base.startswith("http://"), use_corporate_ca=False, stream_response=False, timeout=300)


async def main() -> int:
    ok = True
    report: dict = {"type": os.environ.get("PG_LLM_TYPE", "openai"), "model": os.environ.get("PG_LLM_MODEL"), "checks": {}}

    def check(name: str, cond: bool, extra: str = "") -> None:
        nonlocal ok
        ok &= bool(cond)
        report["checks"][name] = bool(cond)
        print(("PASS " if cond else "FAIL ") + name + (f" — {extra}" if extra and not cond else ""))

    cfg = settings()
    rec = Recorder()
    llm = LlmClient(cfg, transport=rec)
    res = await asyncio.to_thread(Anonymizer(Policy.from_profile(os.environ.get("PG_LLM_PROFILE", "basic")), PEOPLE).process, TRANSCRIPT)
    check("обезличивание выполнено", len(res.replacements) >= 12, str(res.stats()))
    started = time.monotonic()
    try:
        out = await run_llm_pipeline(llm, kind="protocol", instruction="Протокол: решения и поручения с ответственными и сроками.", text=res.text,
                                     limit=20000, anonymized=True, external_structured=True, model=cfg.model)
    except Exception as exc:  # noqa: BLE001
        check("модель ответила", False, f"{type(exc).__name__}: {str(exc)[:200]}")
        out = None
    report["seconds"] = round(time.monotonic() - started, 1)
    sent = "\n".join(rec.bodies)
    check("запрос дошёл до провайдера", bool(rec.bodies))
    leaks = [s for s in SENSITIVE if s in sent]
    check("в исходящих запросах нет исходных имён, контактов, реквизитов, секретов и диагноза", not leaks, ", ".join(x[:4] + "…" for x in leaks))
    if out is not None:
        text = out.text or ""
        report.update(calls=out.calls, prompt_tokens=out.prompt_tokens, completion_tokens=out.completion_tokens, structured=bool(out.structured), warnings=out.warnings)
        check("протокол получен и не обрезан", bool(text.strip()) and not out.truncated, f"обрезан={out.truncated}")
        check("в протоколе обозначения участников, а не имена", ("Иван И." in text or "Сергей К." in text or "Мария П." in text)
              and not any(s in text for s in SENSITIVE), text[:200].replace("\n", " "))
        check("решение и срок из стенограммы сохранились", "пятниц" in text.lower() and "перенос" in text.lower(), text[:200].replace("\n", " "))
        if os.environ.get("PG_LLM_SHOW"):
            print("--- ОТПРАВЛЕНО (синтетика, обезличено) ---\n" + res.text + "\n--- ОТВЕТ ---\n" + text)
    # обязательная маскировка секретов без обезличивания (комната «выключено»)
    rec.bodies.clear()
    try:
        await llm.complete("Ответь одним словом: да.", "Пароль стенда P@ssw0rd-Crm-2026, токен ghp_abcdefghijklmnopqrstuvwxyz0123456789. Понятно?", max_tokens=64)
    except Exception as exc:  # noqa: BLE001
        print(f"INFO прямой запрос: {type(exc).__name__}")
    sent = "\n".join(rec.bodies)
    check("без обезличивания пароль и токен всё равно не уходят", bool(sent) and "P@ssw0rd" not in sent and "ghp_abcdef" not in sent and "[секрет удалён]" in sent)
    report["ok"] = ok
    if os.environ.get("PG_LLM_OUT"):
        Path(os.environ["PG_LLM_OUT"]).write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print("ИТОГ:", "PASS" if ok else "FAIL", f"({report['type']} {report['model']}, {report.get('seconds')} с, обращений {report.get('calls')})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
