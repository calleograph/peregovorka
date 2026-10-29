package org.peregovorka.android.data

import android.content.Context
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.Json
import java.util.UUID

val AppJson = Json { ignoreUnknownKeys = true; coerceInputValues = true; explicitNulls = false }

/** Секреты сессии одного сервера: интерфейс отделён от Android, чтобы клиент API проверялся обычными JVM-тестами. */
interface Sessions {
    fun cookie(id: String): String?
    fun setCookie(id: String, v: String?)
    fun trust(id: String): String?
    fun setTrust(id: String, v: String?)
    fun csrf(id: String): String?
    fun setCsrf(id: String, v: String?)
    fun cookieName(id: String): String
    fun setCookieName(id: String, v: String)
    fun clearSession(id: String)
}

/** Список серверов, выбранный сервер и секреты каждого сервера. Секреты разных организаций не пересекаются: ключ записи содержит id сервера, он же входит в AAD шифрования. */
class Store(ctx: Context, private val box: SecretBox = SecretBox()) : Sessions {
    private val sp = ctx.getSharedPreferences("peregovorka", Context.MODE_PRIVATE)
    private val ser = ListSerializer(Server.serializer())

    fun servers(): List<Server> = runCatching { AppJson.decodeFromString(ser, sp.getString("servers", "[]")!!) }.getOrDefault(emptyList())
    private fun save(list: List<Server>) = sp.edit().putString("servers", AppJson.encodeToString(ser, list)).apply()

    var currentId: String?
        get() = sp.getString("current", null)?.takeIf { id -> servers().any { it.id == id } }
        set(v) { sp.edit().putString("current", v).apply() }

    fun current(): Server? = servers().firstOrNull { it.id == currentId }

    fun add(url: String, name: String): Server {
        servers().firstOrNull { it.url == url }?.let { currentId = it.id; return it }
        val s = Server(UUID.randomUUID().toString(), url, name)
        save(servers() + s); currentId = s.id
        return s
    }

    fun rename(id: String, alias: String) = save(servers().map { if (it.id == id) it.copy(alias = alias.trim().take(60)) else it })

    fun remove(id: String) {
        save(servers().filterNot { it.id == id })
        sp.edit().remove("cookie.$id").remove("trust.$id").remove("csrf.$id").remove("cname.$id").apply()
        if (sp.getString("current", null) == id) currentId = servers().firstOrNull()?.id
    }

    private fun put(kind: String, id: String, v: String?) {
        if (v == null) sp.edit().remove("$kind.$id").apply() else sp.edit().putString("$kind.$id", box.seal(v, "$kind.$id")).apply()
    }
    private fun get(kind: String, id: String): String? = sp.getString("$kind.$id", null)?.let { box.open(it, "$kind.$id") }

    override fun cookie(id: String) = get("cookie", id)
    override fun setCookie(id: String, v: String?) = put("cookie", id, v)
    override fun trust(id: String) = get("trust", id)
    override fun setTrust(id: String, v: String?) = put("trust", id, v)
    override fun cookieName(id: String): String = sp.getString("cname.$id", null) ?: "vm_session"
    override fun setCookieName(id: String, v: String) { sp.edit().putString("cname.$id", v).apply() }
    override fun csrf(id: String) = sp.getString("csrf.$id", null)
    override fun setCsrf(id: String, v: String?) { sp.edit().apply { if (v == null) remove("csrf.$id") else putString("csrf.$id", v) }.apply() }

    /** Выход: сессия и токен доверенного устройства удаляются локально (сервер отзывает токен отдельным запросом). */
    override fun clearSession(id: String) { setCookie(id, null); setTrust(id, null); setCsrf(id, null) }
}
