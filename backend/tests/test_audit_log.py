from __future__ import annotations

import base64

from sqlalchemy import select, update

from app.models import AuditLog
from app.services import audit as audit_svc

from .conftest import login, make_room


def _rows(client):
    async def run():
        async with client.app_obj.state.session_maker() as db:
            return (await db.execute(select(AuditLog).order_by(AuditLog.id))).scalars().all()
    return client.portal.call(run)


def test_login_events_have_context_and_result(client):
    login(client, "root")
    client.post("/api/v1/auth/login", json={"login": "root", "password": "wrong-password"}, headers={"User-Agent": "UnitTest/1.0"})
    client.post("/api/v1/auth/logout")
    rows = _rows(client)
    acts = [(r.action, r.result) for r in rows]
    assert ("auth.login", "success") in acts and ("auth.login_failed", "failure") in acts and ("auth.logout", "success") in acts
    failed = next(r for r in rows if r.action == "auth.login_failed")
    assert failed.user_agent == "UnitTest/1.0" and failed.ip and failed.actor_type == "user"
    assert "wrong-password" not in str([r.details for r in rows])
    ok = next(r for r in rows if r.action == "auth.login")
    assert ok.sig and len(ok.sig) == 64


def test_verify_detects_edit_and_deleted_middle_record(client):
    make_room(client)
    make_room(client)
    make_room(client)
    app = client.app_obj

    async def verify():
        async with app.state.session_maker() as db:
            return await audit_svc.verify(db)

    base = client.portal.call(verify)
    assert base["signed"] and base["intact"] and not base["bad"] and not base["gaps"]
    ids = [r.id for r in _rows(client)]
    assert len(ids) >= 4

    async def tamper():
        async with app.state.session_maker() as db:
            await db.execute(update(AuditLog).where(AuditLog.id == ids[1]).values(actor_name="Подделка"))
            await db.execute(AuditLog.__table__.delete().where(AuditLog.id == ids[2]))
            await db.commit()
    client.portal.call(tamper)
    out = client.portal.call(verify)
    assert ids[1] in out["bad"] and out["gaps"] and not out["intact"]


def test_filters_and_export_csv_formula_guard(client):
    make_room(client)
    r = client.get("/api/v1/admin/audit", params={"result": "success", "object_type": "room"})
    assert r.status_code == 200 and r.json() and all(x["result"] == "success" and x["target_type"] == "room" for x in r.json())
    assert client.get("/api/v1/admin/audit", params={"since": "2999-01-01"}).json() == []
    assert client.get("/api/v1/admin/audit", params={"since": "не дата"}).status_code == 422
    # %-символ в поиске — обычный текст, а не маска
    assert client.get("/api/v1/admin/audit", params={"q": "%"}).json() == []

    login(client, "root")
    csv = client.get("/api/v1/admin/audit/export", params={"format": "csv"})
    assert csv.status_code == 200 and "attachment" in csv.headers["content-disposition"]
    head = csv.content.decode("utf-8-sig").splitlines()[0]
    assert head.startswith("timestamp,event_type,actor_id,actor_type")
    js = client.get("/api/v1/admin/audit/export", params={"format": "json"})
    assert js.status_code == 200 and js.json()
    assert "audit.export" in [a["action"] for a in client.get("/api/v1/admin/audit").json()]
    assert audit_svc.ACTOR_TYPES and client.get("/api/v1/admin/audit/verify").json()["signed"] is True


def test_export_and_verify_forbidden_for_regular_user(client):
    login(client, "alice")
    assert client.get("/api/v1/admin/audit/export").status_code == 403
    assert client.get("/api/v1/admin/audit/verify").status_code == 403


def test_csv_cell_neutralises_formulas():
    from app.api.admin import _csv_cell
    assert _csv_cell("=1+1").startswith("'") and _csv_cell("@cmd").startswith("'") and _csv_cell("обычный") == "обычный" and _csv_cell(None) == ""


def test_no_key_means_unsigned_and_verify_reports_it():
    audit_svc.set_audit_key(base64.b64encode(b"k" * 32).decode())
    assert audit_svc._key is not None
    audit_svc.set_audit_key(None)
    assert audit_svc._key is None
    audit_svc.set_audit_key(base64.b64encode(b"k" * 32).decode())
