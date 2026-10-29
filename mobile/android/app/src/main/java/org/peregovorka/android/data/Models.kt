package org.peregovorka.android.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/** Сервер организации в списке приложения. Секреты (сессия, токен доверенного устройства) хранятся отдельно, в Keystore. */
@Serializable
data class Server(val id: String, val url: String, val name: String, val alias: String = "") {
    val title: String get() = alias.ifBlank { name.ifBlank { url.removePrefix("https://") } }
}

@Serializable
data class ServerInfo(
    val product: String = "",
    val api: Int = 0,
    @SerialName("server_version") val serverVersion: String = "",
    val name: String = "",
    val cookie: String = "",
    @SerialName("trust_days") val trustDays: Int = 0,
)

@Serializable data class UserDto(val id: String = "", @SerialName("display_name") val displayName: String = "", @SerialName("is_admin") val isAdmin: Boolean = false)
@Serializable data class MeDto(val user: UserDto = UserDto(), @SerialName("csrf_token") val csrf: String = "")

@Serializable data class ActiveDto(val id: String = "", val participants: Int = 0)

@Serializable
data class RoomDto(
    val id: String,
    val slug: String = "",
    val name: String,
    val description: String? = null,
    @SerialName("has_password") val hasPassword: Boolean = false,
    @SerialName("room_type") val roomType: String = "regular",
    @SerialName("camera_allowed") val cameraAllowed: Boolean = true,
    @SerialName("e2ee_mode") val e2eeMode: String = "off",
    val lifetime: String = "permanent",
    @SerialName("active_meeting") val active: ActiveDto? = null,
    val live: Boolean = false,
)

@Serializable data class LiveNow(@SerialName("started_at") val startedAt: String = "", val participants: Int = 0)

@Serializable
data class UpcomingItem(
    val id: String,
    val slug: String = "",
    val name: String,
    val kind: String = "",
    val start: String? = null,
    val end: String? = null,
    @SerialName("opens_at") val opensAt: String? = null,
    val organizer: String? = null,
    val cancelled: Boolean = false,
    val live: LiveNow? = null,
    @SerialName("can_join") val canJoin: Boolean = true,
    @SerialName("has_password") val hasPassword: Boolean = false,
)

@Serializable data class UpcomingDto(val items: List<UpcomingItem> = emptyList())

@Serializable data class E2eeDto(val key: String = "", val mode: String = "")

@Serializable
data class ClientCfg(
    val sources: List<String> = emptyList(),
    @SerialName("can_moderate") val canModerate: Boolean = false,
    @SerialName("can_manage") val canManage: Boolean = false,
    @SerialName("mute_on_join") val muteOnJoin: Boolean = false,
    val presentation: Boolean = false,
    val floor: Boolean = false,
    @SerialName("welcome_message") val welcome: String? = null,
)

@Serializable
data class JoinDto(
    @SerialName("meeting_id") val meetingId: String,
    val room: RoomDto,
    @SerialName("livekit_url") val livekitUrl: String,
    val token: String,
    val identity: String = "",
    val client: ClientCfg = ClientCfg(),
    val e2ee: E2eeDto? = null,
    val recording: Boolean = false,
)

@Serializable data class HandDto(val identity: String = "", val name: String = "", val at: Double = 0.0)
@Serializable data class HandsDto(val hands: List<HandDto> = emptyList())

@Serializable
data class ChatMessage(
    val id: Long,
    @SerialName("author_name") val author: String = "",
    @SerialName("author_id") val authorId: String? = null,
    val text: String = "",
    @SerialName("created_at") val createdAt: String = "",
)

@Serializable data class ChatPage(val messages: List<ChatMessage> = emptyList(), val me: String = "")
