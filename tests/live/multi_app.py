"""Один экземпляр backend для проверки нескольких экземпляров (tests/live/chats_multi_instance.py): настоящие PostgreSQL и Redis из окружения
(DATABASE_URL, REDIS_URL), каталог — подставной (alice, bob). Запуск: python tests/live/multi_app.py PORT (из каталога backend)."""
import base64, os, sys, tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "backend"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "backend", "tests"))
import uvicorn  # noqa: E402
from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from conftest import ADMIN_GROUP, FakeDirectory  # noqa: E402

port = int(sys.argv[1])
d = FakeDirectory()
d.add("alice", "alice-pass", name="Алиса Крылова", guid="11111111-1111-4111-8111-111111111111")
d.add("bob", "bob-pass", name="Борис Мартынов", guid="22222222-2222-4222-8222-222222222222")
d.add("root", "root-pass", groups=(ADMIN_GROUP,), name="Администратор")
s = Settings(database_url=os.environ["DATABASE_URL"], redis_url=os.environ["REDIS_URL"], app_public_url="http://localhost:5173", cookie_secure=False,
             livekit_api_key="devkey", livekit_api_secret="s" * 40, livekit_public_url="ws://127.0.0.1:7880", livekit_internal_url="http://127.0.0.1:7880",
             app_master_key=os.environ.get("APP_MASTER_KEY") or base64.b64encode(b"k" * 32).decode(), internal_api_token="t", ldap_admin_group_dn=ADMIN_GROUP,
             data_dir=os.environ.get("DATA_DIR") or tempfile.mkdtemp(prefix=f"pg-multi-{port}-"), docs_enabled=False, log_level="WARNING")
app = create_app(s, directory_factory=lambda _: d)
uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
