package org.peregovorka.android.ui

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import org.peregovorka.android.App
import org.peregovorka.android.BuildConfig
import org.peregovorka.android.data.*
import org.peregovorka.android.net.*

enum class Step { Boot, Welcome, AddServer, Login, Main }
enum class Tab { Home, Calendar, Chats, History, Profile }

data class UiState(
    val step: Step = Step.Boot,
    val servers: List<Server> = emptyList(),
    val server: Server? = null,
    val tab: Tab = Tab.Home,
    val me: MeDto? = null,
    val rooms: List<RoomDto> = emptyList(),
    val upcoming: List<UpcomingItem> = emptyList(),
    val loading: Boolean = false,
    val error: String? = null,
    val added: ServerInfo? = null,          // результат проверки адреса: показываем название организации до входа
    val adding: Boolean = false,
    val joining: Boolean = false,
    val joinError: String? = null,
    val serverVersion: String = "",         // версия сервера (из /desktop/info): от неё зависит, есть ли у сайта страница /chats
    val webPath: String? = null,            // раздел сайта, который нужно открыть во вкладке (ссылка из письма)
    val joinTarget: JoinTarget? = null,     // комната, в которую входит человек (лист «Подключиться»); хранится здесь, а не в экране — не теряется при пересоздании Activity
    val permissionsAsking: Boolean = false, // идёт системный запрос разрешений
)

data class JoinTarget(val roomId: String, val name: String, val hasPassword: Boolean, val cameraAllowed: Boolean)

class AppViewModel(app: Application, private val saved: SavedStateHandle) : AndroidViewModel(app) {
    private val store = (app as App).store
    private val joinFlow = JoinPermissionFlow(android.os.Build.VERSION.SDK_INT)
    private val calls = (app as App).calls
    private val _s = MutableStateFlow(UiState())
    val state: StateFlow<UiState> = _s
    private val apis = mutableMapOf<String, ServerApi>()

    private val ua = "PeregovorkaAndroid/${BuildConfig.VERSION_NAME} (Android ${android.os.Build.VERSION.RELEASE})"
    fun api(s: Server): ServerApi = apis.getOrPut(s.id) { ServerApi(s, store, ua) }

    init { boot(); restorePendingJoin() }

    // ── вход в конференцию и разрешения ─────────────────────────────────────────────────────
    // Системное окно разрешений на части устройств пересоздаёт Activity, а иногда и процесс: Compose-переменные экрана при этом пропадали, и после «Разрешить»
    // вход не продолжался (приходилось перезапускать приложение). Теперь прерванный вход живёт в ViewModel (пересоздание Activity) и в SavedStateHandle (процесс).

    fun openJoin(t: JoinTarget) { clearJoinError(); _s.update { it.copy(joinTarget = t) } }
    fun closeJoin() { joinFlow.clear(); saved["join"] = null; _s.update { it.copy(joinTarget = null, joinError = null, permissionsAsking = false) } }

    /** Нажато «Подключиться»: если разрешений хватает — входим сразу, иначе просим [launch] показать системный запрос; повторное нажатие во время запроса игнорируется. */
    fun startJoin(pwd: String?, mic: Boolean, cam: Boolean, perms: (Perm) -> Boolean, launch: (List<Perm>) -> Unit) {
        val t = _s.value.joinTarget ?: return
        val req = JoinRequest(t.roomId, t.name, t.hasPassword, t.cameraAllowed, pwd, mic, cam)
        when (val st = joinFlow.begin(req, store.permissionsAsked, perms)) {
            JoinStep.Busy -> {}
            is JoinStep.Ask -> {
                saved["join"] = encode(req)                              // пароль в сохранённое состояние не попадает
                if (st.perms.any { it == Perm.Bluetooth || it == Perm.Notifications }) store.permissionsAsked = true
                _s.update { it.copy(permissionsAsking = true) }
                launch(st.perms)
            }
            is JoinStep.Join -> proceed(st)
        }
    }

    /** Система ответила на запрос (или человек вернулся из настроек): вход продолжается с тем, что выдано. */
    fun permissionsResult(perms: (Perm) -> Boolean) {
        _s.update { it.copy(permissionsAsking = false) }
        joinFlow.result(perms)?.let { proceed(it) }
    }

    private fun proceed(st: JoinStep.Join) {
        saved["join"] = null
        join(st.request.roomId, st.request.password, st.mic, st.cam, { ok -> if (ok) { joinFlow.clear(); _s.update { it.copy(joinTarget = null) } } }, st.request.mic, st.request.cam)
    }

    private fun encode(r: JoinRequest) = listOf(r.roomId, r.name, r.hasPassword, r.cameraAllowed, r.mic, r.cam).joinToString("\u0001")

    /** Процесс был завершён во время запроса: запрос разрешений уже показан системой, ответ придёт новому экземпляру. Пароль не сохранялся — для комнаты с паролем лист входа откроется заново. */
    private fun restorePendingJoin() {
        val raw = saved.get<String>("join") ?: return
        val f = raw.split("\u0001")
        if (f.size != 6) { saved["join"] = null; return }
        val t = JoinTarget(f[0], f[1], f[2].toBoolean(), f[3].toBoolean())
        _s.update { it.copy(joinTarget = t) }
        if (!t.hasPassword) joinFlow.restore(JoinRequest(t.roomId, t.name, false, t.cameraAllowed, null, f[4].toBoolean(), f[5].toBoolean())) else saved["join"] = null
    }

    private fun boot() {
        val list = store.servers()
        val cur = store.current()
        _s.update { it.copy(servers = list, server = cur) }
        if (cur == null) { _s.update { it.copy(step = Step.Welcome) }; return }
        enter(cur)
    }

    /** Вход на сервер: есть сессия или токен доверенного устройства — главная, иначе страница входа. */
    private fun enter(s: Server) {
        _s.update { it.copy(server = s, servers = store.servers(), tab = Tab.Home, rooms = emptyList(), upcoming = emptyList(), me = null, error = null) }
        val a = api(s)
        if (!a.hasCredentials()) { _s.update { it.copy(step = Step.Login) }; return }
        viewModelScope.launch {
            try {
                val me = a.me()
                _s.update { it.copy(step = Step.Main, me = me) }
                runCatching { a.info() }.onSuccess { i -> _s.update { it.copy(serverVersion = i.serverVersion) } }
                refresh()
            } catch (_: SessionExpired) {
                _s.update { it.copy(step = Step.Login) }
            } catch (e: ApiError) {
                // сети нет: показываем главную с сообщением, чтобы не выбрасывать человека на вход из-за метро
                _s.update { it.copy(step = Step.Main, error = e.message) }
            }
        }
    }

    // ── добавление сервера ───────────────────────────────────────────────────────────────
    fun startAdd() = _s.update { it.copy(step = Step.AddServer, added = null, error = null) }
    fun cancelAdd() = _s.update { it.copy(step = if (it.server == null) Step.Welcome else Step.Main, error = null) }

    fun checkServer(raw: String) {
        val url = normalizeServerUrl(raw, BuildConfig.DEBUG)
        if (url == null) { _s.update { it.copy(error = "Введите адрес в виде https://meeting.example.org") }; return }
        _s.update { it.copy(adding = true, error = null, added = null) }
        viewModelScope.launch {
            try {
                val probe = ServerApi(Server("probe", url, ""), store, ua)
                val info = probe.info()
                val s = store.add(url, info.name)
                store.setCookieName(s.id, info.cookie.ifBlank { "vm_session" })
                _s.update { it.copy(adding = false, added = info, server = s, servers = store.servers()) }
            } catch (e: ApiError) {
                _s.update { it.copy(adding = false, error = e.message) }
            }
        }
    }

    fun continueToLogin() { _s.value.server?.let { enter(it) } }

    // ── вход (страница сервера в WebView) ────────────────────────────────────────────────
    suspend fun adoptCookie(cookie: String): Boolean {
        val s = _s.value.server ?: return false
        val a = api(s)
        if (!a.adoptCookie(cookie)) return false
        a.enableTrust()
        _s.update { it.copy(step = Step.Main, me = runCatching { a.me() }.getOrNull()) }
        runCatching { a.info() }.onSuccess { i -> _s.update { it.copy(serverVersion = i.serverVersion) } }
        refresh()
        return true
    }

    fun refresh() {
        val s = _s.value.server ?: return
        val a = api(s)
        _s.update { it.copy(loading = true) }
        viewModelScope.launch {
            try {
                val rooms = a.rooms()
                val up = runCatching { a.upcoming() }.getOrDefault(emptyList())
                _s.update { it.copy(rooms = rooms, upcoming = up, loading = false, error = null) }
            } catch (_: SessionExpired) {
                _s.update { it.copy(loading = false, step = Step.Login) }
            } catch (e: ApiError) {
                _s.update { it.copy(loading = false, error = e.message) }
            }
        }
    }

    fun selectTab(t: Tab) = _s.update { it.copy(tab = t, webPath = null) }
    fun openWeb(path: String) = _s.update { it.copy(webPath = path) }
    fun closeWeb() = _s.update { it.copy(webPath = null) }

    // ── серверы ──────────────────────────────────────────────────────────────────────────
    /** false — идёт конференция: переключение её прервёт, интерфейс сначала спрашивает подтверждение. */
    fun canSwitchWithoutAsking(): Boolean = !calls.active

    fun switchTo(s: Server) { if (calls.active) calls.leave(); calls.dismissEnded(); enter(s) }
    fun rename(s: Server, alias: String) { store.rename(s.id, alias); _s.update { it.copy(servers = store.servers(), server = store.current()) } }

    fun remove(s: Server) {
        viewModelScope.launch {
            if (calls.active && _s.value.server?.id == s.id) calls.leave()
            runCatching { api(s).logout() }
            apis.remove(s.id); store.remove(s.id)
            android.webkit.CookieManager.getInstance().removeAllCookies(null)
            val left = store.servers()
            _s.update { it.copy(servers = left, server = store.current()) }
            val next = store.current()
            if (next == null) _s.update { it.copy(step = Step.Welcome) } else enter(next)
        }
    }

    fun logout() {
        val s = _s.value.server ?: return
        viewModelScope.launch {
            if (calls.active) calls.leave()
            runCatching { api(s).logout() }
            android.webkit.CookieManager.getInstance().removeAllCookies(null)
            _s.update { it.copy(step = Step.Login, me = null, rooms = emptyList(), upcoming = emptyList()) }
        }
    }

    // ── подключение к комнате ────────────────────────────────────────────────────────────
    fun join(roomId: String, password: String?, mic: Boolean, camera: Boolean, onResult: (Boolean) -> Unit, intentMic: Boolean = mic, intentCamera: Boolean = camera) {
        val s = _s.value.server ?: return
        val a = api(s)
        _s.update { it.copy(joining = true, joinError = null) }
        viewModelScope.launch {
            try {
                val j = a.join(roomId, password)
                calls.start(a, s.title, j, mic, camera, intentMic, intentCamera)
                _s.update { it.copy(joining = false) }
                onResult(true)
            } catch (e: SessionExpired) {
                _s.update { it.copy(joining = false, step = Step.Login) }; onResult(false)
            } catch (e: ApiError) {
                _s.update { it.copy(joining = false, joinError = e.message) }; onResult(false)
            }
        }
    }

    fun clearJoinError() = _s.update { it.copy(joinError = null) }

    /** Ссылка на встречу (из письма): известный сервер — открываем, неизвестный — предлагаем добавить. */
    fun openLink(raw: String?): LinkResult {
        val t = parseMeetingLink(raw, BuildConfig.DEBUG) ?: return LinkResult.Invalid
        val s = store.servers().firstOrNull { it.url == t.origin } ?: return LinkResult.UnknownServer(t.origin)
        if (_s.value.server?.id != s.id) enter(s)
        val m = Regex("^/rooms/([A-Za-z0-9._~-]+)$").find(t.path)
        return if (m != null) LinkResult.Room(m.groupValues[1]) else LinkResult.External(t.origin + t.path)
    }

    suspend fun resolveRoom(ref: String): RoomDto? {
        val s = _s.value.server ?: return null
        return rooms().firstOrNull { it.slug == ref || it.id == ref }
    }
    private fun rooms() = _s.value.rooms
}

sealed interface LinkResult {
    data object Invalid : LinkResult
    data class UnknownServer(val origin: String) : LinkResult
    data class Room(val ref: String) : LinkResult
    data class External(val url: String) : LinkResult
}
