package org.peregovorka.android

import android.content.Context
import android.content.Intent
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.test.core.app.ActivityScenario
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.uiautomator.By
import androidx.test.uiautomator.BySelector
import androidx.test.uiautomator.UiDevice
import androidx.test.uiautomator.UiObject2
import androidx.test.uiautomator.Until
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.peregovorka.android.data.Store

const val PKG = "org.peregovorka.android"

/** Подставной сервер Peregovorka на localhost: ровно столько API, сколько нужно экранам; страница чатов — как у сайта (панель на весь экран, поле ввода внизу). */
class FakePeregovorka {
    val web = MockWebServer()
    val requests = java.util.concurrent.CopyOnWriteArrayList<String>()
    // Настоящий LiveKit (если задан аргумент livekit_url): сервер разработки с ключом devkey; иначе адрес недоступен, и вход в конференцию завершится понятной ошибкой
    val args = InstrumentationRegistry.getArguments()
    val realLivekit: String? = args.getString("livekit_url")
    @Volatile var livekitUrl = realLivekit ?: "ws://127.0.0.1:1"

    private fun b64(b: ByteArray) = android.util.Base64.encodeToString(b, android.util.Base64.URL_SAFE or android.util.Base64.NO_PADDING or android.util.Base64.NO_WRAP)
    private fun livekitToken(): String {
        if (realLivekit == null) return "x"
        val key = args.getString("livekit_key", "devkey"); val secret = args.getString("livekit_secret", "secret")
        val now = System.currentTimeMillis() / 1000
        val head = b64("""{"alg":"HS256","typ":"JWT"}""".toByteArray())
        val body = b64("""{"iss":"$key","sub":"u1","name":"Тест Тестов","nbf":${now - 10},"exp":${now + 3600},"video":{"room":"test-room","roomJoin":true,"canPublish":true,"canSubscribe":true,"canPublishData":true}}""".toByteArray())
        val mac = javax.crypto.Mac.getInstance("HmacSHA256").apply { init(javax.crypto.spec.SecretKeySpec(secret.toByteArray(), "HmacSHA256")) }
        return "$head.$body.${b64(mac.doFinal("$head.$body".toByteArray()))}"
    }

    private fun json(body: String, code: Int = 200) = MockResponse().setResponseCode(code).setHeader("Content-Type", "application/json").setBody(body)

    private val chatPage = """<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover, interactive-widget=resizes-content">
        <style>html,body{margin:0;height:100%}body{font:16px sans-serif}
        .win{position:fixed;inset:0;display:flex;flex-direction:column;background:#fff}.msgs{flex:1;overflow:auto;padding:8px}
        .composer{border-top:1px solid #ccc;padding:8px;display:flex;gap:6px}.composer textarea{flex:1;font:16px sans-serif}</style></head>
        <body><div class="win"><div class="msgs">Переписка</div><div class="composer"><textarea id="t" rows="1" placeholder="Сообщение"></textarea><button>Отправить</button></div></div>
        <script>try{ (navigator.userAgent.indexOf('PeregovorkaApp/')>=0) && document.title }catch(e){}</script></body></html>"""

    init {
        web.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val path = request.path.orEmpty().substringBefore('?')
                requests += "${request.method} $path ua=${request.getHeader("User-Agent")?.substringAfter("PeregovorkaApp/", "-")?.take(10)}"
                return when {
                    path == "/api/v1/desktop/info" -> json("""{"product":"peregovorka","api":1,"server_version":"0.49.5","name":"Тестовая организация","cookie":"vm_session","trust_days":30,"features":[]}""")
                    path == "/api/v1/auth/me" -> json("""{"user":{"id":"u1","display_name":"Тест Тестов","is_admin":false},"csrf_token":"csrf1"}""")
                    path == "/api/v1/rooms" -> json("""[{"id":"r1","slug":"main","name":"Основная","has_password":false,"camera_allowed":true},
                        {"id":"r2","slug":"secret","name":"Закрытая","has_password":true,"camera_allowed":true}]""")
                    path == "/api/v1/rooms/upcoming" -> json("""{"items":[]}""")
                    path.startsWith("/api/v1/rooms/") && path.endsWith("/join") -> json(
                        """{"meeting_id":"m1","room":{"id":"r1","name":"Основная","camera_allowed":true},"livekit_url":"$livekitUrl","token":"${livekitToken()}","identity":"u1",
                        "client":{"sources":["microphone","camera"]}}""")
                    path.endsWith("/hands") -> json("""{"hands":[]}""")
                    path.endsWith("/chat") -> json("""{"messages":[],"me":"u1"}""")
                    path.endsWith("/leave") || path == "/api/v1/client/events" -> MockResponse().setResponseCode(204)
                    path == "/chats" || path == "/calendar" || path == "/history" || path == "/" -> MockResponse().setHeader("Content-Type", "text/html; charset=utf-8").setBody(chatPage)
                    else -> MockResponse().setResponseCode(404)
                }
            }
        }
        web.start()
    }

    val url: String get() = "http://127.0.0.1:${web.port}"
    fun close() = web.shutdown()
}

fun appContext(): Context = InstrumentationRegistry.getInstrumentation().targetContext
fun device(): UiDevice = UiDevice.getInstance(InstrumentationRegistry.getInstrumentation())

/** Чистое состояние приложения; при `server != null` — вход уже выполнен (сессия в защищённом хранилище), как после обычной авторизации. */
fun resetApp(server: FakePeregovorka? = null) {
    val ctx = appContext()
    // процесс общий для всех тестов: идущую или завершённую конференцию предыдущего теста сбрасываем
    InstrumentationRegistry.getInstrumentation().runOnMainSync { App.instance.calls.leave(); App.instance.calls.dismissEnded() }
    ctx.getSharedPreferences("peregovorka", 0).edit().clear().commit()
    if (server != null) {
        val store = Store(ctx)
        val s = store.add(server.url, "Тестовая организация")
        store.setCookieName(s.id, "vm_session"); store.setCookie(s.id, "session-1"); store.setCsrf(s.id, "csrf1")
    }
}

fun launchApp(): ActivityScenario<MainActivity> = ActivityScenario.launch(Intent(appContext(), MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))

fun UiDevice.waitFor(selector: BySelector, ms: Long = 15_000): UiObject2? = wait(Until.findObject(selector), ms)

/** Высота экранной клавиатуры по данным окна приложения (0 — клавиатуры нет). */
fun imeHeight(scenario: ActivityScenario<MainActivity>): Int {
    var h = 0
    scenario.onActivity { a -> h = ViewCompat.getRootWindowInsets(a.window.decorView)?.getInsets(WindowInsetsCompat.Type.ime())?.bottom ?: 0 }
    return h
}

/** Ждёт появления клавиатуры и возвращает её верхнюю границу в координатах экрана; null — клавиатура не появилась. */
fun waitForKeyboardTop(scenario: ActivityScenario<MainActivity>, ms: Long = 10_000): Int? {
    val end = System.currentTimeMillis() + ms
    while (System.currentTimeMillis() < end) {
        val h = imeHeight(scenario)
        if (h > 100) { Thread.sleep(700); return device().displayHeight - imeHeight(scenario).coerceAtLeast(h) }          // дать анимации закончиться
        Thread.sleep(150)
    }
    return null
}

/** Нажимает «разрешить» во всех системных запросах разрешений, пока они появляются (микрофон, камера, уведомления, Bluetooth — набор зависит от версии Android). */
fun acceptPermissionDialogs(maxMs: Long = 12_000): Int {
    val d = device()
    var clicked = 0
    val end = System.currentTimeMillis() + maxMs
    val allow = By.res(java.util.regex.Pattern.compile("com\\.android\\.permissioncontroller:id/permission_allow(_foreground_only)?_button|com\\.android\\.packageinstaller:id/permission_allow_button"))
    while (System.currentTimeMillis() < end) {
        val b = d.wait(Until.findObject(allow), 2_500)
        if (b == null) { if (clicked > 0) return clicked else continue }
        b.click(); clicked++
        Thread.sleep(600)
    }
    return clicked
}

fun logRequests(tag: String, f: FakePeregovorka) = android.util.Log.i("PGTEST", "$tag: ${f.requests.joinToString(" | ")}")
