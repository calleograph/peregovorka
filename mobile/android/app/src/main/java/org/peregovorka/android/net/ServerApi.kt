package org.peregovorka.android.net

import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.Call
import okhttp3.Callback
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import org.peregovorka.android.BuildConfig
import org.peregovorka.android.data.*
import java.io.IOException
import java.util.concurrent.TimeUnit
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

/** Ошибка запроса к серверу. `code` — машинный код из ответа (например, `wrong_password`), `message` — готовый текст для человека. */
class ApiError(val status: Int, val code: String?, override val message: String, val retryAfter: Int? = null) : Exception(message)

/** Сессия истекла и обновить её без пароля нельзя: показать вход. */
class SessionExpired : Exception("Сессия истекла. Войдите снова.")

private val client: OkHttpClient by lazy {
    OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS).readTimeout(20, TimeUnit.SECONDS).writeTimeout(20, TimeUnit.SECONDS)
        .followRedirects(false)       // сессионные cookie не должны уходить на чужой адрес по редиректу
        .build()
}

private suspend fun Call.await(): Response = suspendCancellableCoroutine { c ->
    c.invokeOnCancellation { cancel() }
    enqueue(object : Callback {
        override fun onFailure(call: Call, e: IOException) { if (!c.isCancelled) c.resumeWithException(e) }
        override fun onResponse(call: Call, response: Response) { c.resume(response) }
    })
}

private val JSON_MT = "application/json".toMediaType()

/** Клиент API одного сервера. Cookie сессии и CSRF-токен берёт из [Store]; при 401 один раз пробует войти по токену доверенного устройства. */
class ServerApi(val server: Server, private val store: Sessions, private val userAgent: String) {
    private val base get() = server.url + "/api/v1"
    private val cookieName: String get() = store.cookieName(server.id)

    private fun builder(path: String, auth: Boolean): Request.Builder {
        val b = Request.Builder().url(base + path).header("User-Agent", userAgent).header("Accept", "application/json")
        store.clientId().takeIf { it.isNotEmpty() }?.let { b.header("X-Client-Id", it) }
        if (auth) {
            store.cookie(server.id)?.let { b.header("Cookie", "$cookieName=$it") }
            store.csrf(server.id)?.let { b.header("X-CSRF-Token", it) }
        }
        return b
    }

    private suspend fun error(r: Response): ApiError {
        val text = withContext(Dispatchers.IO) { r.body?.string().orEmpty() }
        var code: String? = null
        var msg = ""
        runCatching {
            when (val d = AppJson.parseToJsonElement(text).jsonObject["detail"]) {
                is JsonPrimitive -> msg = d.contentOrNull.orEmpty()
                is JsonObject -> { code = d["code"]?.jsonPrimitive?.contentOrNull; msg = d["message"]?.jsonPrimitive?.contentOrNull.orEmpty() }
                else -> {}
            }
        }
        if (msg.isBlank()) msg = when (r.code) {
            403 -> "Доступ запрещён."; 404 -> "Не найдено."; 429 -> "Слишком много попыток. Повторите позже."
            in 500..599 -> "Сервер временно недоступен."; else -> "Ошибка ${r.code}."
        }
        return ApiError(r.code, code, msg, r.header("Retry-After")?.toIntOrNull())
    }

    private suspend fun exec(req: Request): Response = try { client.newCall(req).await() } catch (e: IOException) {
        throw ApiError(0, "network", "Нет связи с сервером. Проверьте интернет и адрес.")
    }

    /** Запрос с сессией. 401 → вход по токену доверенного устройства → повтор; не вышло → [SessionExpired]. */
    private suspend fun authed(method: String, path: String, body: String? = null, desktop: Boolean = false): String {
        suspend fun once(): Response {
            val b = builder(path, true)
            if (desktop) b.header("X-Peregovorka-Desktop", "1")
            b.method(method, body?.toRequestBody(JSON_MT) ?: if (method == "GET") null else "".toRequestBody(JSON_MT))
            return exec(b.build())
        }
        var r = once()
        if (r.code == 401) {
            r.close()
            if (!resume()) throw SessionExpired()
            r = once()
            if (r.code == 401) { r.close(); throw SessionExpired() }
        }
        r.use {
            if (!it.isSuccessful) throw error(it)
            return withContext(Dispatchers.IO) { it.body?.string().orEmpty() }
        }
    }

    // ── без входа ────────────────────────────────────────────────────────────────────────
    suspend fun info(): ServerInfo {
        val r = exec(builder("/desktop/info", false).get().build())
        r.use {
            if (!it.isSuccessful) throw ApiError(it.code, "not_peregovorka", "По этому адресу нет сервера Peregovorka.")
            val text = withContext(Dispatchers.IO) { it.body?.string().orEmpty() }
            val i = runCatching { AppJson.decodeFromString(ServerInfo.serializer(), text) }.getOrNull()
            if (i == null || i.product != "peregovorka") throw ApiError(200, "not_peregovorka", "По этому адресу нет сервера Peregovorka.")
            if (i.api > SUPPORTED_API) throw ApiError(200, "server_too_new", "Сервер использует более новую версию протокола. Обновите приложение.")
            if (i.api < SUPPORTED_API) throw ApiError(200, "server_too_old", "Версия сервера слишком старая для мобильного приложения. Обратитесь к администратору.")
            if (i.cookie.isNotBlank()) store.setCookieName(server.id, i.cookie)
            return i
        }
    }

    // ── сессия ───────────────────────────────────────────────────────────────────────────
    fun cookieName(): String = cookieName

    /** Проверка cookie, полученной страницей входа. true — вход состоялся; CSRF и cookie сохранены. */
    suspend fun adoptCookie(cookie: String): Boolean {
        val r = exec(builder("/auth/me", false).header("Cookie", "$cookieName=$cookie").get().build())
        r.use {
            if (!it.isSuccessful) return false
            val me = runCatching { AppJson.decodeFromString(MeDto.serializer(), withContext(Dispatchers.IO) { it.body?.string().orEmpty() }) }.getOrNull() ?: return false
            store.setCookie(server.id, cookie); store.setCsrf(server.id, me.csrf)
            return true
        }
    }

    suspend fun me(): MeDto {
        val me = AppJson.decodeFromString(MeDto.serializer(), authed("GET", "/auth/me"))
        store.setCsrf(server.id, me.csrf)
        return me
    }

    /** Доверенное устройство: токен для входа без пароля после истечения сессии (срок задаёт администратор). Отказ сервера — не ошибка: просто придётся входить заново. */
    suspend fun enableTrust() {
        runCatching {
            val t = AppJson.parseToJsonElement(authed("POST", "/desktop/trust", desktop = true)).jsonObject["token"]?.jsonPrimitive?.contentOrNull
            if (t != null) store.setTrust(server.id, t)
        }
    }

    private suspend fun resume(): Boolean {
        val token = store.trust(server.id) ?: return false
        val body = AppJson.encodeToString(JsonObject.serializer(), JsonObject(mapOf("token" to JsonPrimitive(token))))
        val req = builder("/desktop/resume", false).header("X-Peregovorka-Desktop", "1").post(body.toRequestBody(JSON_MT)).build()
        val r = try { exec(req) } catch (_: ApiError) { return false }     // сеть недоступна: токен сохраняем, пробуем позже
        r.use {
            if (it.code == 401) { store.setTrust(server.id, null); return false }
            if (!it.isSuccessful) return false
            val cookie = setCookieValue(it, cookieName) ?: return false
            store.setCookie(server.id, cookie)
            runCatching {
                val csrf = AppJson.parseToJsonElement(withContext(Dispatchers.IO) { it.body?.string().orEmpty() }).jsonObject["csrf_token"]?.jsonPrimitive?.contentOrNull
                if (csrf != null) store.setCsrf(server.id, csrf)
            }
            return true
        }
    }

    /** Есть ли что-то для входа без пароля (cookie или токен доверенного устройства). */
    fun hasCredentials(): Boolean = store.cookie(server.id) != null || store.trust(server.id) != null

    suspend fun logout() {
        val token = store.trust(server.id)
        runCatching { authed("POST", "/auth/logout") }
        if (token != null) runCatching {
            val body = AppJson.encodeToString(JsonObject.serializer(), JsonObject(mapOf("token" to JsonPrimitive(token))))
            exec(builder("/desktop/forget", false).header("X-Peregovorka-Desktop", "1").post(body.toRequestBody(JSON_MT)).build()).close()
        }
        store.clearSession(server.id)
    }

    // ── данные ───────────────────────────────────────────────────────────────────────────
    suspend fun rooms(): List<RoomDto> = AppJson.decodeFromString(kotlinx.serialization.builtins.ListSerializer(RoomDto.serializer()), authed("GET", "/rooms"))
    suspend fun upcoming(): List<UpcomingItem> = AppJson.decodeFromString(UpcomingDto.serializer(), authed("GET", "/rooms/upcoming")).items

    suspend fun join(roomId: String, password: String?): JoinDto {
        val body = AppJson.encodeToString(JsonObject.serializer(), JsonObject(if (password.isNullOrEmpty()) emptyMap() else mapOf("password" to JsonPrimitive(password))))
        return AppJson.decodeFromString(JoinDto.serializer(), authed("POST", "/rooms/$roomId/join", body))
    }

    suspend fun leave(meetingId: String) { runCatching { authed("POST", "/meetings/$meetingId/leave") } }

    suspend fun hands(meetingId: String): List<HandDto> = AppJson.decodeFromString(HandsDto.serializer(), authed("GET", "/meetings/$meetingId/hands")).hands
    suspend fun setHand(meetingId: String, raised: Boolean) {
        authed("POST", "/meetings/$meetingId/hand", """{"raised":$raised}""")
    }

    suspend fun chat(meetingId: String, afterId: Long = 0): ChatPage =
        AppJson.decodeFromString(ChatPage.serializer(), authed("GET", "/meetings/$meetingId/chat" + if (afterId > 0) "?after_id=$afterId" else ""))
    suspend fun sendChat(meetingId: String, text: String) {
        val body = AppJson.encodeToString(JsonObject.serializer(), JsonObject(mapOf("text" to JsonPrimitive(text))))
        authed("POST", "/meetings/$meetingId/chat", body)
    }

    suspend fun reportEvent(event: String, meetingId: String?, reason: String? = null) {
        val m = mutableMapOf<String, kotlinx.serialization.json.JsonElement>("event" to JsonPrimitive(event))
        meetingId?.let { m["meeting_id"] = JsonPrimitive(it) }
        reason?.let { m["reason"] = JsonPrimitive(it.take(80)) }
        runCatching { authed("POST", "/client/events", AppJson.encodeToString(JsonObject.serializer(), JsonObject(m))) }
    }

    companion object {
        const val SUPPORTED_API = 1
        fun setCookieValue(r: Response, name: String): String? =
            r.headers("Set-Cookie").firstNotNullOfOrNull { h -> h.substringBefore(';').takeIf { it.startsWith("$name=") }?.substringAfter('=') }
        val debug get() = BuildConfig.DEBUG
    }
}
