package org.peregovorka.android.ui

import android.app.Application
import androidx.lifecycle.AndroidViewModel
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
    val webPath: String? = null,            // раздел сайта, который нужно открыть во вкладке (ссылка из письма)
)

class AppViewModel(app: Application) : AndroidViewModel(app) {
    private val store = (app as App).store
    private val calls = (app as App).calls
    private val _s = MutableStateFlow(UiState())
    val state: StateFlow<UiState> = _s
    private val apis = mutableMapOf<String, ServerApi>()

    private val ua = "PeregovorkaAndroid/${BuildConfig.VERSION_NAME} (Android ${android.os.Build.VERSION.RELEASE})"
    fun api(s: Server): ServerApi = apis.getOrPut(s.id) { ServerApi(s, store, ua) }

    init { boot() }

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
    fun join(roomId: String, password: String?, mic: Boolean, camera: Boolean, onResult: (Boolean) -> Unit) {
        val s = _s.value.server ?: return
        val a = api(s)
        _s.update { it.copy(joining = true, joinError = null) }
        viewModelScope.launch {
            try {
                val j = a.join(roomId, password)
                calls.start(a, s.title, j, mic, camera)
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
