package org.peregovorka.android

import kotlinx.coroutines.test.runTest
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.peregovorka.android.data.Server
import org.peregovorka.android.data.Sessions
import org.peregovorka.android.net.ApiError
import org.peregovorka.android.net.ServerApi
import org.peregovorka.android.net.SessionExpired
import kotlin.test.*

private class MemSessions : Sessions {
    val m = mutableMapOf<String, String>()
    override fun cookie(id: String) = m["c$id"]
    override fun setCookie(id: String, v: String?) { if (v == null) m.remove("c$id") else m["c$id"] = v }
    override fun trust(id: String) = m["t$id"]
    override fun setTrust(id: String, v: String?) { if (v == null) m.remove("t$id") else m["t$id"] = v }
    override fun csrf(id: String) = m["s$id"]
    override fun setCsrf(id: String, v: String?) { if (v == null) m.remove("s$id") else m["s$id"] = v }
    override fun cookieName(id: String) = m["n$id"] ?: "vm_session"
    override fun setCookieName(id: String, v: String) { m["n$id"] = v }
    override fun clearSession(id: String) { setCookie(id, null); setTrust(id, null); setCsrf(id, null) }
}

class ServerApiTest {
    private lateinit var web: MockWebServer
    private lateinit var st: MemSessions
    private lateinit var api: ServerApi

    @BeforeTest fun up() {
        web = MockWebServer().apply { start() }
        st = MemSessions()
        api = ServerApi(Server("s1", web.url("/").toString().trimEnd('/'), "Тест"), st, "Test/1")
    }
    @AfterTest fun down() = web.shutdown()

    private fun json(body: String, code: Int = 200) = MockResponse().setResponseCode(code).setHeader("Content-Type", "application/json").setBody(body)

    @Test fun `info принимает совместимый сервер и запоминает имя cookie`() = runTest {
        web.enqueue(json("""{"product":"peregovorka","api":1,"server_version":"0.49.3","name":"Орг","cookie":"vm_x","trust_days":30,"features":["trust"]}"""))
        val i = api.info()
        assertEquals("Орг", i.name); assertEquals("vm_x", st.cookieName("s1"))
        assertEquals("/api/v1/desktop/info", web.takeRequest().path)
    }

    @Test fun `info отклоняет чужой сервер и несовместимые уровни протокола`() = runTest {
        web.enqueue(json("""{"detail":"nope"}""", 404))
        assertEquals("not_peregovorka", assertFailsWith<ApiError> { api.info() }.code)
        web.enqueue(json("""{"product":"other","api":1}"""))
        assertEquals("not_peregovorka", assertFailsWith<ApiError> { api.info() }.code)
        web.enqueue(json("""{"product":"peregovorka","api":2}"""))
        assertEquals("server_too_new", assertFailsWith<ApiError> { api.info() }.code)
        web.enqueue(json("""{"product":"peregovorka","api":0}"""))
        assertEquals("server_too_old", assertFailsWith<ApiError> { api.info() }.code)
    }

    @Test fun `ошибка сервера превращается в понятный текст и Retry-After`() = runTest {
        st.setCookie("s1", "ck")
        web.enqueue(json("""{"detail":{"code":"wrong_password","message":"Неверный пароль комнаты."}}""", 403).setHeader("Retry-After", "7"))
        val e = assertFailsWith<ApiError> { api.join("r1", "x") }
        assertEquals("wrong_password", e.code); assertEquals("Неверный пароль комнаты.", e.message); assertEquals(7, e.retryAfter)
    }

    @Test fun `запрос несёт cookie и CSRF, а пароль комнаты идёт только в теле`() = runTest {
        st.setCookie("s1", "ck"); st.setCsrf("s1", "tok")
        web.enqueue(json("""{"meeting_id":"m","room":{"id":"r","name":"R"},"livekit_url":"wss://lk.example.org","token":"jwt","identity":"i"}"""))
        val j = api.join("r1", "secret")
        assertEquals("m", j.meetingId)
        val r = web.takeRequest()
        assertEquals("vm_session=ck", r.getHeader("Cookie")); assertEquals("tok", r.getHeader("X-CSRF-Token"))
        assertTrue(r.body.readUtf8().contains("secret")); assertFalse(r.path!!.contains("secret"))
    }

    @Test fun `при 401 сессия обновляется по токену доверенного устройства и запрос повторяется`() = runTest {
        st.setCookie("s1", "old"); st.setTrust("s1", "trust-1")
        web.enqueue(json("""{"detail":"Требуется вход"}""", 401))
        web.enqueue(json("""{"csrf_token":"new-csrf","user":{"id":"u"}}""").addHeader("Set-Cookie", "vm_session=fresh; Path=/; HttpOnly"))
        web.enqueue(json("""[{"id":"r1","name":"Комната"}]"""))
        val rooms = api.rooms()
        assertEquals(1, rooms.size)
        assertEquals("fresh", st.cookie("s1")); assertEquals("new-csrf", st.csrf("s1"))
        web.takeRequest()
        val resume = web.takeRequest()
        assertEquals("/api/v1/desktop/resume", resume.path); assertEquals("1", resume.getHeader("X-Peregovorka-Desktop"))
        assertEquals("vm_session=fresh", web.takeRequest().getHeader("Cookie"))
    }

    @Test fun `отозванный токен стирается и приводит к повторному входу`() = runTest {
        st.setCookie("s1", "old"); st.setTrust("s1", "revoked")
        web.enqueue(json("""{"detail":"x"}""", 401))
        web.enqueue(json("""{"detail":{"code":"trust_invalid","message":"x"}}""", 401))
        assertFailsWith<SessionExpired> { api.rooms() }
        assertNull(st.trust("s1"))
    }

    @Test fun `без токена 401 сразу означает повторный вход`() = runTest {
        st.setCookie("s1", "old")
        web.enqueue(json("""{"detail":"x"}""", 401))
        assertFailsWith<SessionExpired> { api.rooms() }
        assertEquals(1, web.requestCount)
    }

    @Test fun `недоступный сервер даёт понятную сетевую ошибку`() = runTest {
        web.shutdown()
        val e = assertFailsWith<ApiError> { api.info() }
        assertEquals("network", e.code)
    }

    @Test fun `adoptCookie сохраняет сессию только если сервер её принял`() = runTest {
        web.enqueue(json("""{"detail":"x"}""", 401))
        assertFalse(api.adoptCookie("bad")); assertNull(st.cookie("s1"))
        web.enqueue(json("""{"user":{"id":"u","display_name":"Иван"},"csrf_token":"c1"}"""))
        assertTrue(api.adoptCookie("good")); assertEquals("good", st.cookie("s1")); assertEquals("c1", st.csrf("s1"))
    }

    @Test fun `выход отзывает токен на сервере и стирает локальные секреты`() = runTest {
        st.setCookie("s1", "ck"); st.setTrust("s1", "tr"); st.setCsrf("s1", "c")
        web.enqueue(MockResponse().setResponseCode(204)); web.enqueue(MockResponse().setResponseCode(204))
        api.logout()
        assertNull(st.cookie("s1")); assertNull(st.trust("s1"))
        assertEquals("/api/v1/auth/logout", web.takeRequest().path)
        assertEquals("/api/v1/desktop/forget", web.takeRequest().path)
    }
}
