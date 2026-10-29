package org.peregovorka.android.net

import java.net.URI

/** Адрес сервера → его начало (схема + хост + порт). Только HTTPS; http разрешён лишь для localhost/10.0.2.2 в отладочной сборке. null — адрес не годится. */
fun normalizeServerUrl(raw: String, allowLocalHttp: Boolean = false): String? {
    var t = raw.trim()
    if (t.isEmpty() || t.length > 300 || t.any { it.isWhitespace() }) return null
    if (!t.contains("://")) t = "https://$t"
    val u = try { URI(t) } catch (_: Exception) { return null }
    val host = u.host?.lowercase() ?: return null
    val named = host.contains('.') || host.contains(':') || (allowLocalHttp && host == "localhost")     // имя без точки (intranet, localhost) в рабочей сборке не принимается
    if (u.userInfo != null || host.isBlank() || !named) return null
    val local = host == "localhost" || host == "127.0.0.1" || host == "10.0.2.2"
    val scheme = u.scheme?.lowercase()
    if (scheme != "https" && !(scheme == "http" && allowLocalHttp && local)) return null
    val port = if (u.port == -1 || (scheme == "https" && u.port == 443) || (scheme == "http" && u.port == 80)) "" else ":${u.port}"
    val h = if (host.contains(':')) "[$host]" else host
    return "$scheme://$h$port"
}

/** Разделы сайта, которые приложение открывает внутри себя (ссылки из писем и календаря). Всё остальное уходит во внешний браузер. */
private val ROUTES = listOf(
    Regex("^/$"), Regex("^/history$"), Regex("^/calendar$"), Regex("^/profile$"),
    Regex("^/rooms/[A-Za-z0-9._~-]{1,120}$"), Regex("^/m/[A-Za-z0-9_-]{4,120}$"), Regex("^/p/[A-Za-z0-9_-]{4,120}$"),
    Regex("^/history/[0-9a-fA-F-]{8,64}$"),
)

data class LinkTarget(val origin: String, val path: String)

/** Ссылка на встречу (https://сервер/путь или peregovorka://open?url=...) → сервер и разрешённый путь, иначе null. */
fun parseMeetingLink(raw: String?, allowLocalHttp: Boolean = false): LinkTarget? {
    if (raw == null || raw.length > 2048) return null
    val u = try { URI(raw) } catch (_: Exception) { return null }
    val inner = if (u.scheme.equals("peregovorka", true)) {
        if (u.host != "open") return null
        val q = (u.rawQuery ?: return null).split("&")
        if (q.size != 1 || !q[0].startsWith("url=")) return null
        java.net.URLDecoder.decode(q[0].removePrefix("url="), "UTF-8")
    } else raw
    val t = try { URI(inner) } catch (_: Exception) { return null }
    val origin = normalizeServerUrl("${t.scheme}://${t.rawAuthority}", allowLocalHttp) ?: return null
    val path = (t.rawPath ?: "").trimEnd('/').ifEmpty { "/" }
    return if (ROUTES.any { it.matches(path) }) LinkTarget(origin, path) else null
}


/** Сравнение версий вида 0.49.6 (без суффиксов); нераспознанное считается «старым». */
fun versionAtLeast(actual: String, required: String): Boolean {
    fun parts(v: String) = v.trim().removePrefix("v").split('.', '-', '+').take(3).map { it.takeWhile(Char::isDigit).toIntOrNull() ?: return@map -1 }
    val a = parts(actual); val r = parts(required)
    if (a.size < 3 || a.any { it < 0 }) return false
    for (i in 0 until 3) { if (a[i] != r[i]) return a[i] > r[i] }
    return true
}

/** С этой версии сервера у сайта есть раздел `/chats` и скрытие собственной навигации внутри приложения (метка PeregovorkaApp в User-Agent). */
const val SERVER_WITH_APP_PAGES = "0.49.6"
