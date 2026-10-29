package org.peregovorka.android.call

import android.app.Application
import io.livekit.android.LiveKit
import io.livekit.android.RoomOptions
import io.livekit.android.audio.AudioSwitchHandler
import io.livekit.android.e2ee.E2EEOptions
import io.livekit.android.events.RoomEvent
import io.livekit.android.room.Room
import io.livekit.android.room.participant.Participant
import io.livekit.android.room.track.LocalVideoTrack
import io.livekit.android.room.track.Track
import io.livekit.android.room.track.VideoTrack
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import io.livekit.android.e2ee.BaseKeyProvider
import org.peregovorka.android.data.*
import org.peregovorka.android.net.ServerApi
import org.peregovorka.android.net.SessionExpired

enum class Phase { Idle, Connecting, Connected, Reconnecting, Ended }

data class Tile(
    val key: String, val name: String, val isLocal: Boolean,
    val camera: VideoTrack?, val screen: VideoTrack?, val micOn: Boolean, val speaking: Boolean, val hand: Boolean,
)

data class CallUi(
    val phase: Phase = Phase.Idle,
    val roomName: String = "",
    val meetingId: String = "",
    val serverTitle: String = "",
    val tiles: List<Tile> = emptyList(),
    val micOn: Boolean = false,
    val camOn: Boolean = false,
    val canMic: Boolean = true,
    val canCamera: Boolean = true,
    val handUp: Boolean = false,
    val hands: List<HandDto> = emptyList(),
    val chat: List<ChatMessage> = emptyList(),
    val unread: Int = 0,
    val chatOpen: Boolean = false,
    val audioDevice: String = "",
    val endReason: String? = null,
    val e2ee: Boolean = false,
    val welcome: String? = null,
    val startedAt: Long = 0,
)

/**
 * Одна идущая конференция на всё приложение. Живёт в процессе, а не в Activity: поворот экрана и сворачивание её не прерывают.
 * Звук в фоне держит [CallService] (служба переднего плана с типом microphone); камера при сворачивании выключается — система всё равно не даёт к ней доступ.
 */
class CallManager(private val app: Application) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val _ui = MutableStateFlow(CallUi())
    val ui: StateFlow<CallUi> = _ui

    private var room: Room? = null
    private var api: ServerApi? = null
    private var jobs = mutableListOf<Job>()
    private var cameraWasOn = false
    private var myIdentity = ""
    private var myUserId = ""

    val active: Boolean get() = _ui.value.phase != Phase.Idle && _ui.value.phase != Phase.Ended

    fun start(api: ServerApi, serverTitle: String, join: JoinDto, wantMic: Boolean, wantCamera: Boolean) {
        if (active) return
        this.api = api; myIdentity = join.identity
        val sources = join.client.sources
        val canMic = "microphone" in sources
        val canCam = "camera" in sources && join.room.cameraAllowed
        val mic = wantMic && canMic && !join.client.muteOnJoin
        _ui.value = CallUi(
            phase = Phase.Connecting, roomName = join.room.name, meetingId = join.meetingId, serverTitle = serverTitle,
            canMic = canMic, canCamera = canCam, e2ee = join.e2ee != null, welcome = join.client.welcome, startedAt = System.currentTimeMillis(),
        )
        CallService.start(app, join.room.name, serverTitle)
        val r = LiveKit.create(
            app,
            options = RoomOptions(
                adaptiveStream = true, dynacast = true,
                e2eeOptions = join.e2ee?.takeIf { it.key.isNotEmpty() }?.let { E2EEOptions(keyProvider = BaseKeyProvider().also { kp -> kp.setSharedKey(it.key) }) },
            ),
        )
        room = r
        jobs += scope.launch { r.events.events.collect { onEvent(it) } }
        jobs += scope.launch {
            try {
                r.connect(join.livekitUrl, join.token)
                _ui.update { it.copy(phase = Phase.Connected) }
                if (mic) runCatching { r.localParticipant.setMicrophoneEnabled(true) }
                if (wantCamera && canCam) runCatching { r.localParticipant.setCameraEnabled(true) }
                refresh()
                api.reportEvent("join_ok", join.meetingId)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                api.reportEvent("connect_failed", join.meetingId, e.javaClass.simpleName)
                finish("Не удалось подключиться к конференции. Проверьте интернет и повторите.")
            }
        }
        jobs += scope.launch { while (true) { pollHands(); delay(6000) } }
    }

    private fun onEvent(e: RoomEvent) {
        when (e) {
            is RoomEvent.Disconnected -> finish(if (_ui.value.phase == Phase.Ended) null else "Соединение с конференцией завершено.")
            is RoomEvent.Reconnecting -> _ui.update { it.copy(phase = Phase.Reconnecting) }
            is RoomEvent.Reconnected -> _ui.update { it.copy(phase = Phase.Connected) }
            is RoomEvent.FailedToConnect -> finish("Не удалось подключиться к конференции.")
            else -> {}
        }
        refresh()
    }

    private fun refresh() {
        val r = room ?: return
        val hands = _ui.value.hands
        fun tile(p: Participant, local: Boolean): Tile {
            val id = p.identity?.value.orEmpty()
            val cam = p.getTrackPublication(Track.Source.CAMERA)
            val scr = p.getTrackPublication(Track.Source.SCREEN_SHARE)
            val camTrack = (cam?.track as? VideoTrack)?.takeIf { cam.muted.not() }
            return Tile(
                key = id.ifEmpty { p.sid.value }, name = p.name?.takeIf { it.isNotBlank() } ?: id, isLocal = local,
                camera = camTrack, screen = scr?.track as? VideoTrack, micOn = p.isMicrophoneEnabled, speaking = p.isSpeaking,
                hand = hands.any { it.identity == id },
            )
        }
        val list = listOf(tile(r.localParticipant, true)) + r.remoteParticipants.values.map { tile(it, false) }
        val me = r.localParticipant
        _ui.update {
            it.copy(
                tiles = list, micOn = me.isMicrophoneEnabled, camOn = me.isCameraEnabled,
                handUp = hands.any { h -> h.identity == myIdentity },
                audioDevice = (r.audioHandler as? AudioSwitchHandler)?.selectedAudioDevice?.name.orEmpty(),
            )
        }
    }

    fun toggleMic() { val r = room ?: return; scope.launch { runCatching { r.localParticipant.setMicrophoneEnabled(!r.localParticipant.isMicrophoneEnabled) }; refresh() } }
    fun toggleCamera() { val r = room ?: return; scope.launch { runCatching { r.localParticipant.setCameraEnabled(!r.localParticipant.isCameraEnabled) }; refresh() } }
    fun flipCamera() {
        val t = room?.localParticipant?.getTrackPublication(Track.Source.CAMERA)?.track as? LocalVideoTrack ?: return
        t.switchCamera()
    }

    /** Выход из конференции или потеря соединения: освобождает микрофон, камеру и службу переднего плана. */
    fun leave() { val mid = _ui.value.meetingId; val a = api; if (a != null && mid.isNotEmpty()) scope.launch { a.leave(mid) }; finish(null) }

    private fun finish(reason: String?) {
        if (_ui.value.phase == Phase.Idle) return
        jobs.forEach { it.cancel() }; jobs.clear()
        room?.disconnect(); room?.release(); room = null
        CallService.stop(app)
        _ui.update { it.copy(phase = Phase.Ended, endReason = reason, tiles = emptyList()) }
    }

    /** Экран закрыт после завершения: возвращаемся в исходное состояние. */
    fun dismissEnded() { if (_ui.value.phase == Phase.Ended) _ui.value = CallUi() }

    // ── сворачивание: камера в фоне недоступна ───────────────────────────────────────────
    fun onBackground() {
        val r = room ?: return
        if (r.localParticipant.isCameraEnabled) { cameraWasOn = true; scope.launch { runCatching { r.localParticipant.setCameraEnabled(false) }; refresh() } }
    }
    fun onForeground() {
        val r = room ?: return
        if (cameraWasOn) { cameraWasOn = false; scope.launch { runCatching { r.localParticipant.setCameraEnabled(true) }; refresh() } }
    }

    // ── рука, чат ────────────────────────────────────────────────────────────────────────
    private suspend fun pollHands() {
        val a = api ?: return
        val mid = _ui.value.meetingId.ifEmpty { return }
        runCatching { a.hands(mid) }.onSuccess { h -> _ui.update { it.copy(hands = h) }; refresh() }
            .onFailure { if (it is SessionExpired) { /* сессия истекла посреди встречи: звонок продолжается, чат и рука временно недоступны */ } }
    }

    fun toggleHand() {
        val a = api ?: return
        val up = !_ui.value.handUp
        scope.launch { runCatching { a.setHand(_ui.value.meetingId, up) }; pollHands() }
    }

    fun setChatOpen(open: Boolean) { _ui.update { it.copy(chatOpen = open, unread = if (open) 0 else it.unread) }; if (open) scope.launch { refreshChat() } }

    suspend fun refreshChat() {
        val a = api ?: return
        val mid = _ui.value.meetingId.ifEmpty { return }
        val last = _ui.value.chat.lastOrNull()?.id ?: 0
        runCatching { a.chat(mid, last) }.onSuccess { page ->
            if (page.messages.isEmpty()) return
            if (myUserId.isEmpty()) myUserId = page.me
            _ui.update { s ->
                val fresh = page.messages.filter { m -> s.chat.none { it.id == m.id } }
                s.copy(chat = s.chat + fresh, unread = if (s.chatOpen) 0 else s.unread + fresh.count { it.authorId != myUserId })
            }
        }
    }

    fun sendChat(text: String) {
        val a = api ?: return
        val t = text.trim().ifEmpty { return }
        scope.launch { runCatching { a.sendChat(_ui.value.meetingId, t) }; refreshChat() }
    }

    fun myId(): String = myUserId

    // ── аудиоустройства (динамик, наушники, Bluetooth) ────────────────────────────────────
    fun audioDevices(): List<com.twilio.audioswitch.AudioDevice> = (room?.audioHandler as? AudioSwitchHandler)?.availableAudioDevices.orEmpty()
    fun selectAudio(d: com.twilio.audioswitch.AudioDevice) { (room?.audioHandler as? AudioSwitchHandler)?.selectDevice(d); refresh() }

    /** Ссылка на комнату для показа в интерфейсе. */
    fun room(): Room? = room
}
