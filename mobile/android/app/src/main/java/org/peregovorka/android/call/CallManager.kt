package org.peregovorka.android.call

import android.app.Application
import io.livekit.android.LiveKit
import io.livekit.android.RoomOptions
import io.livekit.android.audio.AudioSwitchHandler
import io.livekit.android.e2ee.E2EEOptions
import io.livekit.android.events.DisconnectReason
import io.livekit.android.events.RoomEvent
import io.livekit.android.room.Room
import io.livekit.android.room.participant.Participant
import io.livekit.android.room.track.LocalVideoTrack
import io.livekit.android.room.track.Track
import io.livekit.android.room.track.VideoTrack
import io.livekit.android.util.flow
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import io.livekit.android.e2ee.BaseKeyProvider
import org.peregovorka.android.data.*
import org.peregovorka.android.net.ApiError
import org.peregovorka.android.net.ServerApi
import org.peregovorka.android.net.SessionExpired

enum class Phase { Idle, Connecting, Connected, Reconnecting, Ended }

/** Итог подключения: NoMedia — сигнал прошёл (вход в комнату принят), а голос и видео до сервера звонков не дошли (ICE). */
enum class Connect { Ok, Failed, NoMedia }

data class Tile(
    val key: String, val name: String, val isLocal: Boolean,
    val camera: VideoTrack?, val screen: VideoTrack?, val micOn: Boolean, val speaking: Boolean, val hand: Boolean,
    /** Размер картинки демонстрации у источника (пиксели) — по нему считается вписывание и предел приближения; null — ещё не известен. */
    val screenW: Int = 0, val screenH: Int = 0,
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
    /** Человек хотел включить микрофон/камеру, но разрешения нет: рядом с кнопкой — пояснение и «Разрешить»; после выдачи (в окне запроса или в настройках) включается само. */
    val micWanted: Boolean = false,
    val camWanted: Boolean = false,
    /** Показ своего экрана (MediaProjection): разрешён ли в этой комнате, идёт ли сейчас, что сказать человеку (отказ системы, остановка). */
    val canScreen: Boolean = false,
    val screenOn: Boolean = false,
    val screenNote: String? = null,
)

/**
 * Одна идущая конференция на всё приложение. Живёт в процессе, а не в Activity: поворот экрана и сворачивание её не прерывают.
 * Звук в фоне держит [CallService] (служба переднего плана с типом microphone); камера при сворачивании выключается — система всё равно не даёт к ней доступ.
 */
/** Почему показ экрана не начался — по-человечески. Отдельные производители ограничивают службы переднего плана и захват экрана. */
fun screenFailText(e: Throwable?): String = when (e) {
    is SecurityException -> "Android не разрешил показ экрана (ограничение системы или производителя телефона). Разрешите приложению работу в фоне в настройках и повторите."
    null -> "Показ экрана не начался. Повторите; если не получится — покажите экран с компьютера."
    else -> if ((e.message ?: "").contains("permission", ignoreCase = true) || (e.message ?: "").contains("denied", ignoreCase = true))
        "Сервер не разрешил вам показывать экран в этой комнате (права руководителя или предел одновременных показов)."
    else "Показ экрана не начался: ${e.javaClass.simpleName}. Повторите; если не получится — покажите экран с компьютера."
}

class CallManager(private val app: Application) {
    companion object {
        private const val RELEASE_DELAY_MS = 1500L
        private val RECOVER_PAUSES_MS = longArrayOf(1_000, 2_000, 4_000, 8_000, 15_000, 30_000, 30_000, 30_000)
        /** Сколько ждать медиасоединения после входа: Room.connect() SDK возвращается сразу после сигнала, не дожидаясь ICE (браузерная библиотека ждёт). */
        private const val MEDIA_TIMEOUT_MS = 25_000L
        /** Подряд столько повторных входов без медиа — дальше не пробуем: сеть телефона не пропускает голос и видео, повторы ничего не изменят. */
        const val NO_MEDIA_LIMIT = 2
        const val NO_MEDIA_MESSAGE = "Голос и видео не проходят: сервер звонков недоступен из сети, к которой подключён телефон. Подключитесь к рабочей Wi‑Fi или VPN и войдите снова."

        /** Повторять ли вход после очередной попытки: да — пока не набралось [NO_MEDIA_LIMIT] попыток подряд без медиа. */
        fun keepTrying(noMediaInRow: Int): Boolean = noMediaInRow < NO_MEDIA_LIMIT

        /** Сообщение на экране завершения по причине отключения: null — обрыв связи, входим заново; "" — человек вышел сам. */
        fun endMessage(reason: DisconnectReason?): String? = when (reason) {
            DisconnectReason.CLIENT_INITIATED -> ""
            DisconnectReason.ROOM_DELETED, DisconnectReason.ROOM_CLOSED -> "Встреча завершена."
            DisconnectReason.PARTICIPANT_REMOVED -> "Вас отключили от встречи."
            DisconnectReason.DUPLICATE_IDENTITY -> "В эту встречу вошли с другого устройства под тем же подключением."
            DisconnectReason.USER_REJECTED, DisconnectReason.USER_UNAVAILABLE, DisconnectReason.SIP_TRUNK_FAILURE -> "Соединение с конференцией завершено."
            else -> null
        }
    }
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val _ui = MutableStateFlow(CallUi())
    val ui: StateFlow<CallUi> = _ui

    private var room: Room? = null
    private var api: ServerApi? = null
    private var jobs = mutableListOf<Job>()
    private var cameraWasOn = false
    private var myIdentity = ""
    private var myUserId = ""
    /** Медиасоединение текущей комнаты установлено (после этого события переподключения SDK управляют экраном). */
    private var mediaUp = false

    val active: Boolean get() = _ui.value.phase != Phase.Idle && _ui.value.phase != Phase.Ended

    /** Новый вход в ту же комнату (свежий токен от сервера) — для восстановления после обрыва; null — восстановление невозможно. */
    private var rejoin: (suspend () -> JoinDto)? = null
    private val roomJobs = mutableListOf<Job>()
    private var recovering: Job? = null

    /**
     * wantMic/wantCamera — включить сейчас (есть разрешения); intentMic/intentCamera — чего человек хотел: если разрешения не было, устройство включится само после выдачи доступа.
     * [rejoin] — повторный вход в комнату: после обрыва связи (сеть, сервер звонков перезапущен) приложение входит заново само, а не выбрасывает человека из встречи.
     */
    fun start(api: ServerApi, serverTitle: String, join: JoinDto, wantMic: Boolean, wantCamera: Boolean, intentMic: Boolean = wantMic, intentCamera: Boolean = wantCamera,
              rejoin: (suspend () -> JoinDto)? = null) {
        if (active) return
        this.api = api; this.rejoin = rejoin
        val sources = join.client.sources
        val canMic = "microphone" in sources
        val canCam = "camera" in sources && join.room.cameraAllowed
        val mic = wantMic && canMic && !join.client.muteOnJoin
        _ui.value = CallUi(
            phase = Phase.Connecting, roomName = join.room.name, meetingId = join.meetingId, serverTitle = serverTitle,
            canMic = canMic, canCamera = canCam, canScreen = "screen_share" in sources, e2ee = join.e2ee != null, welcome = join.client.welcome, startedAt = System.currentTimeMillis(),
            micWanted = intentMic && canMic && !join.client.muteOnJoin, camWanted = intentCamera && canCam,
        )
        // Служба переднего плана нужна, чтобы звук не прерывался в фоне; если система в этот момент её не разрешает (запуск из состояния «не на переднем плане»,
        // сразу после окна разрешений), конференция всё равно идёт — приложение не должно из-за этого завершаться
        runCatching { CallService.start(app, join.room.name, serverTitle) }.onFailure { android.util.Log.w("PGCALL", "fgs_start_failed ${it.javaClass.simpleName}: ${it.message?.take(200)}") }
        jobs += scope.launch {
            when (connectRoom(join, mic, wantCamera && canCam)) {
                Connect.Ok -> api.reportEvent("join_ok", join.meetingId)
                Connect.NoMedia -> finish(NO_MEDIA_MESSAGE)
                Connect.Failed -> finish("Не удалось подключиться к конференции. Проверьте интернет и повторите.")
            }
        }
        jobs += scope.launch { while (true) { pollHands(); delay(6000) } }
        jobs += scope.launch { watchScreen() }
    }

    /**
     * Сторож чужой демонстрации экрана: демонстрация подписана, а новые кадры не декодируются 15 с — в журнал устройства (PGCALL) и на сервер
     * («Диагностика клиентов», событие screen_frozen) уходит, что именно происходит: кодек, декодер (аппаратный или программный), сколько кадров пришло
     * и сколько декодировано. Один раз на дорожку; звонок не трогается.
     */
    private suspend fun watchScreen() {
        val reported = HashSet<String>()
        var lastDecoded = -1L; var stillFor = 0; var track = ""
        while (true) {
            delay(5_000)
            val r = room ?: continue
            if (_ui.value.phase != Phase.Connected) { stillFor = 0; continue }
            val pub = r.remoteParticipants.values.firstNotNullOfOrNull { it.getTrackPublication(Track.Source.SCREEN_SHARE) }
            val sid = pub?.sid ?: run { stillFor = 0; lastDecoded = -1; track = ""; continue }
            if (sid != track) { track = sid; stillFor = 0; lastDecoded = -1 }
            val st = screenStats(r)
            if (st != null && st.decoded > lastDecoded) { lastDecoded = st.decoded; stillFor = 0; continue }
            stillFor += 5
            if (stillFor >= 15 && reported.add(sid)) {
                val why = st?.let { "${it.codec} dec=${it.decoder} recv=${it.received} decd=${it.decoded} ${it.width}x${it.height}" } ?: "no inbound video"
                android.util.Log.w("PGCALL", "screen_frozen $why")
                val a = api; val mid = _ui.value.meetingId
                if (a != null && mid.isNotEmpty()) scope.launch { a.reportEvent("screen_frozen", mid, why) }
            }
        }
    }

    private data class ScreenStats(val codec: String, val decoder: String, val received: Long, val decoded: Long, val width: Long, val height: Long)

    /** Входящее видео с наибольшим кадром (демонстрация крупнее камер) из статистики WebRTC; null — статистики нет. */
    private suspend fun screenStats(r: Room): ScreenStats? = suspendCancellableCoroutine { c ->
        r.getSubscriberRTCStats { report ->
            val codecs = report.statsMap.values.filter { it.type == "codec" }.associate { it.id to (it.members["mimeType"]?.toString() ?: "?") }
            fun n(v: Any?) = (v as? Number)?.toLong() ?: 0L
            val v = report.statsMap.values.filter { it.type == "inbound-rtp" && it.members["kind"] == "video" }.maxByOrNull { n(it.members["frameWidth"]) }
            val out = v?.members?.let { m -> ScreenStats(codecs[m["codecId"]] ?: "?", m["decoderImplementation"]?.toString() ?: "?", n(m["framesReceived"]), n(m["framesDecoded"]), n(m["frameWidth"]), n(m["frameHeight"])) }
            if (c.isActive) c.resume(out) {}
        }
    }

    /**
     * Создаёт комнату LiveKit и подключается. Подключено — только когда установилось медиасоединение (Room.State.CONNECTED ставится по ICE): иначе приложение
     * показывало «подключено», SDK минуту переподключался, отключался, повторный вход снова «удавался» по сигналу — и так по кругу («Связь потеряна, восстанавливаем»).
     * События прежней (отключённой) комнаты дальше не обрабатываются.
     */
    private suspend fun connectRoom(join: JoinDto, mic: Boolean, camera: Boolean): Connect {
        myIdentity = join.identity
        val r = LiveKit.create(
            app,
            options = RoomOptions(
                adaptiveStream = true, dynacast = true,
                e2eeOptions = join.e2ee?.takeIf { it.key.isNotEmpty() }?.let { E2EEOptions(keyProvider = BaseKeyProvider().also { kp -> kp.setSharedKey(it.key) }) },
            ),
        )
        room = r
        roomJobs += scope.launch { r.events.events.collect { if (room === r) onEvent(it) } }
        // «микрофон/камера включены» в SDK — производные состояния, обновляются асинхронно: refresh() сразу после переключения читал прежнее значение,
        // и кнопка оставалась «Выключить микрофон», пока не придёт какое-нибудь другое событие. Подписка на сами состояния обновляет кнопки сразу.
        roomJobs += scope.launch { r.localParticipant::isMicrophoneEnabled.flow.collect { if (room === r) refresh() } }
        roomJobs += scope.launch { r.localParticipant::isCameraEnabled.flow.collect { if (room === r) refresh() } }
        return try {
            mediaUp = false
            r.connect(join.livekitUrl, join.token)
            if (withTimeoutOrNull(MEDIA_TIMEOUT_MS) { r::state.flow.first { it == Room.State.CONNECTED } } == null) {
                val host = runCatching { java.net.URI(join.livekitUrl).host }.getOrNull().orEmpty()
                android.util.Log.w("PGCALL", "media_unreachable state=${r.state} host=$host")
                api?.let { a -> scope.launch { a.reportEvent("ice_failed", join.meetingId, "no_media_${MEDIA_TIMEOUT_MS / 1000}s state=${r.state}") } }
                releaseRoom()
                return Connect.NoMedia
            }
            mediaUp = true
            _ui.update { it.copy(phase = Phase.Connected, meetingId = join.meetingId) }
            scope.launch { delay(3_000); if (room === r) icePath(r)?.let { p -> api?.reportEvent("ice_stats", join.meetingId, p) } }
            if (mic) runCatching { r.localParticipant.setMicrophoneEnabled(true) }
            if (camera) runCatching { r.localParticipant.setCameraEnabled(true) }
            refresh()
            Connect.Ok
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            // причина — в журнал устройства (тег PGCALL); токен доступа из текста вырезается
            android.util.Log.w("PGCALL", "connect_failed ${e.javaClass.name}: ${e.message?.replace(Regex("access_token=[^&\\s\"]+"), "access_token=<скрыто>")?.take(300)}")
            api?.let { a -> scope.launch { a.reportEvent("connect_failed", join.meetingId, e.javaClass.simpleName) } }
            releaseRoom()
            Connect.Failed
        }
    }

    /** Каким путём идёт медиа (для «Диагностики клиентов»): протокол и типы кандидатов выбранной пары, например «udp host→host» или «tcp host→host». */
    private suspend fun icePath(r: Room): String? = suspendCancellableCoroutine { c ->
        r.getSubscriberRTCStats { report ->
            val all = report.statsMap
            val pair = all.values.firstOrNull { it.type == "candidate-pair" && it.members["state"] == "succeeded" && it.members["nominated"] == true }
            val loc = pair?.members?.get("localCandidateId")?.let { all[it] }?.members
            val rem = pair?.members?.get("remoteCandidateId")?.let { all[it] }?.members
            val out = if (loc == null || rem == null) null else "${loc["protocol"]} ${loc["candidateType"]}→${rem["candidateType"]}"
            if (c.isActive) c.resume(out) {}
        }
    }

    private fun onEvent(e: RoomEvent) {
        when (e) {
            // пока медиасоединение не установилось, исход решает connectRoom (ожидание с пределом), а не события переподключения SDK
            is RoomEvent.Disconnected -> if (mediaUp) onDisconnected(e.reason, e.error)
            is RoomEvent.Reconnecting -> if (mediaUp) _ui.update { it.copy(phase = Phase.Reconnecting) }
            is RoomEvent.Reconnected -> if (mediaUp) _ui.update { it.copy(phase = Phase.Connected) }
            else -> {}
        }
        refresh()
    }

    /**
     * Сервер звонков закрыл подключение. Причина пишется в журнал устройства (PGCALL) и уходит на сервер («Диагностика клиентов»): по ней видно, что случилось —
     * встречу завершили, участника удалили или пропала связь. Встреча завершена или человека удалили — экран завершения с понятной причиной; обрыв связи
     * (сеть, перезапуск сервера звонков, сбой сигнального канала) — приложение входит в комнату заново само, не выбрасывая человека.
     */
    private fun onDisconnected(reason: DisconnectReason?, error: Exception?) {
        val ph = _ui.value.phase
        if (ph == Phase.Ended || ph == Phase.Idle) return
        android.util.Log.w("PGCALL", "disconnected reason=${reason?.name} error=${error?.javaClass?.simpleName}")
        val a = api; val mid = _ui.value.meetingId
        if (a != null && mid.isNotEmpty()) scope.launch { a.reportEvent("disconnected", mid, reason?.name ?: "UNKNOWN") }
        val msg = endMessage(reason)
        when {
            msg == null -> recover(reason?.name ?: "UNKNOWN")
            msg.isEmpty() -> finish(null)
            else -> finish(msg)
        }
    }

    /** Повторный вход после обрыва: свежий токен от сервера и новое подключение; паузы растут, всего около двух минут. Встреча закончилась — экран завершения. */
    private fun recover(why: String) {
        if (recovering?.isActive == true) return
        val rj = rejoin ?: run { finish("Соединение с конференцией завершено."); return }
        val mic = _ui.value.micOn || room?.localParticipant?.let { sourceOn(it, Track.Source.MICROPHONE) } == true
        val camera = cameraWasOn || room?.localParticipant?.let { sourceOn(it, Track.Source.CAMERA) } == true
        releaseRoom()
        _ui.update { it.copy(phase = Phase.Reconnecting, tiles = emptyList()) }
        val a = api; val mid = _ui.value.meetingId
        recovering = scope.launch {
            if (a != null && mid.isNotEmpty()) launch { a.reportEvent("rejoin_started", mid, why) }      // отдельно: без сети отправка ждёт таймаута и не должна задерживать попытки
            var noMedia = 0
            for (pause in RECOVER_PAUSES_MS) {
                delay(pause)
                val j = try { rj() } catch (e: CancellationException) { throw e } catch (e: SessionExpired) {
                    finish("Сессия истекла. Войдите снова."); return@launch
                } catch (e: ApiError) {
                    // 4xx (кроме «слишком часто») — входить больше некуда: встреча закончилась, доступ закрыт; связь и 5xx — пробуем ещё
                    if (e.status in 400..499 && e.status != 429 && e.status != 408) { finish(e.message); return@launch }
                    null
                } catch (e: Exception) { null }
                when (if (j == null) Connect.Failed else connectRoom(j, mic, camera && _ui.value.canCamera)) {
                    Connect.Ok -> {
                        android.util.Log.i("PGCALL", "rejoin_ok after=$why")
                        api?.let { x -> scope.launch { x.reportEvent("reconnected", j?.meetingId ?: mid, why) } }
                        return@launch
                    }
                    Connect.NoMedia -> if (!keepTrying(++noMedia)) {
                        if (a != null && mid.isNotEmpty()) scope.launch { a.reportEvent("rejoin_failed", mid, "no_media") }
                        finish(NO_MEDIA_MESSAGE); return@launch
                    }
                    Connect.Failed -> noMedia = 0
                }
            }
            if (a != null && mid.isNotEmpty()) scope.launch { a.reportEvent("rejoin_failed", mid, why) }
            finish("Связь с конференцией потеряна. Проверьте интернет и войдите снова.")
        }
    }

    /**
     * Включён ли источник: публикация есть и не заглушена. Читается прямо из публикации (поле muted меняется сразу при переключении), а не из производного
     * состояния SDK isMicrophoneEnabled — оно обновляется асинхронно, и кнопка после выключения микрофона оставалась «Выключить микрофон».
     */
    private fun sourceOn(p: Participant, source: Track.Source): Boolean = p.getTrackPublication(source)?.let { !it.muted && it.track != null } ?: false

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
                camera = camTrack, screen = scr?.track as? VideoTrack, micOn = sourceOn(p, Track.Source.MICROPHONE), speaking = p.isSpeaking,
                hand = hands.any { it.identity == id }, screenW = scr?.dimensions?.width ?: 0, screenH = scr?.dimensions?.height ?: 0,
            )
        }
        val list = listOf(tile(r.localParticipant, true)) + r.remoteParticipants.values.map { tile(it, false) }
        val me = r.localParticipant
        val micNow = sourceOn(me, Track.Source.MICROPHONE)
        if (micNow != _ui.value.micOn) android.util.Log.i("PGCALL", "ui_mic $micNow pub=${me.getTrackPublication(Track.Source.MICROPHONE)?.let { "muted=${it.muted} track=${it.track != null}" }}")
        _ui.update {
            it.copy(
                tiles = list, micOn = sourceOn(me, Track.Source.MICROPHONE), camOn = sourceOn(me, Track.Source.CAMERA), screenOn = sourceOn(me, Track.Source.SCREEN_SHARE),
                handUp = hands.any { h -> h.identity == myIdentity },
                audioDevice = (r.audioHandler as? AudioSwitchHandler)?.selectedAudioDevice?.name.orEmpty(),
            )
        }
    }

    /**
     * Качество чужой демонстрации экрана. По умолчанию сервер выбирает слой по размеру окна (адаптивный поток); при увеличении жестами окно не меняется, и без этого
     * приближенный текст был бы размытым. [w]×[h] > 0 — запросить слой не меньше этого разрешения (переподключения и разрыва звука нет: меняется только выбор слоя);
     * 0×0 — вернуть выбор по размеру окна ([windowW]×[windowH] в пикселях).
     */
    fun setScreenResolution(identity: String, w: Int, h: Int, windowW: Int = 0, windowH: Int = 0) {
        val p = room?.remoteParticipants?.values?.firstOrNull { it.identity?.value == identity } ?: return
        val pub = p.getTrackPublication(Track.Source.SCREEN_SHARE) as? io.livekit.android.room.track.RemoteTrackPublication ?: return
        runCatching {
            if (w > 0 && h > 0) { pub.setVideoQuality(io.livekit.android.room.track.VideoQuality.HIGH); pub.setVideoDimensions(Track.Dimensions(w, h)) }
            else if (windowW > 0 && windowH > 0) pub.setVideoDimensions(Track.Dimensions(windowW, windowH))
        }.onFailure { android.util.Log.w("PGCALL", "screen_quality_failed ${it.javaClass.simpleName}") }
    }

    fun toggleMic() {
        val r = room ?: return
        val on = !sourceOn(r.localParticipant, Track.Source.MICROPHONE)
        _ui.update { it.copy(micWanted = on) }
        scope.launch {
            runCatching { r.localParticipant.setMicrophoneEnabled(on) }.onFailure { android.util.Log.w("PGCALL", "mic_toggle_failed ${it.javaClass.simpleName}") }
            android.util.Log.i("PGCALL", "mic_toggle want=$on now=${sourceOn(r.localParticipant, Track.Source.MICROPHONE)} sdk=${r.localParticipant.isMicrophoneEnabled}")
            refresh()
        }
    }
    fun toggleCamera() {
        val r = room ?: return
        val on = !sourceOn(r.localParticipant, Track.Source.CAMERA)
        _ui.update { it.copy(camWanted = on) }
        scope.launch { runCatching { r.localParticipant.setCameraEnabled(on) }.onFailure { android.util.Log.w("PGCALL", "camera_toggle_failed ${it.javaClass.simpleName}") }; refresh() }
    }
    // ── показ своего экрана ───────────────────────────────────────────────────────────────
    /**
     * [data] — ответ системного окна MediaProjection («Начать трансляцию»). Захват и служба переднего плана с типом mediaProjection — в LiveKit SDK
     * (ScreenCaptureService): Android 14+ разрешает её только после согласия человека, а согласие действует на один показ. Показ идёт и в свёрнутом
     * приложении; остановить можно здесь, из шторки системы или кнопкой «Остановить» Android 15 — тогда приходит onStop.
     */
    fun startScreenShare(data: android.content.Intent) {
        val r = room ?: return
        _ui.update { it.copy(screenNote = null) }
        scope.launch {
            val res = runCatching {
                r.localParticipant.setScreenShareEnabled(true, io.livekit.android.room.track.screencapture.ScreenCaptureParams(data, onStop = {
                    scope.launch { refresh(); if (!_ui.value.screenOn) _ui.update { it.copy(screenNote = "Показ экрана остановлен.") } }
                }))
            }
            val ok = res.getOrDefault(false)
            android.util.Log.i("PGCALL", "screen_share_start ok=$ok ${res.exceptionOrNull()?.javaClass?.simpleName.orEmpty()}")
            if (!ok) _ui.update { it.copy(screenNote = screenFailText(res.exceptionOrNull())) }
            val a = api; val mid = _ui.value.meetingId
            if (a != null && mid.isNotEmpty()) launch { runCatching { a.reportEvent(if (ok) "screen_share_ok" else "screen_share_failed", mid, res.exceptionOrNull()?.javaClass?.simpleName.orEmpty()) } }
            refresh()
        }
    }
    fun stopScreenShare() {
        val r = room ?: return
        scope.launch { runCatching { r.localParticipant.setScreenShareEnabled(false) }; refresh() }
    }
    fun screenConsentDenied() { _ui.update { it.copy(screenNote = "Показ экрана отменён: без разрешения Android изображение экрана не передаётся.") } }
    fun dismissScreenNote() { _ui.update { it.copy(screenNote = null) } }

    /** Человек нажал «включить», а разрешения нет: запоминаем намерение — как только доступ выдан, устройство включится само. */
    fun wantMic() { _ui.update { it.copy(micWanted = true) } }
    fun wantCamera() { _ui.update { it.copy(camWanted = true) } }

    /** Разрешения могли измениться (ответ на запрос или возврат из настроек Android): включить то, чего человек хотел и что теперь доступно. Перезапуск приложения не нужен. */
    fun permissionsChanged(granted: (org.peregovorka.android.ui.Perm) -> Boolean) {
        val r = room ?: return
        val s = _ui.value
        if (s.phase != Phase.Connected && s.phase != Phase.Reconnecting) return
        if (s.micWanted && s.canMic && granted(org.peregovorka.android.ui.Perm.Mic) && !sourceOn(r.localParticipant, Track.Source.MICROPHONE))
            scope.launch { runCatching { r.localParticipant.setMicrophoneEnabled(true) }; refresh() }
        if (s.camWanted && s.canCamera && granted(org.peregovorka.android.ui.Perm.Camera) && !sourceOn(r.localParticipant, Track.Source.CAMERA) && !cameraWasOn)
            scope.launch { runCatching { r.localParticipant.setCameraEnabled(true) }; refresh() }
    }
    fun flipCamera() {
        val t = room?.localParticipant?.getTrackPublication(Track.Source.CAMERA)?.track as? LocalVideoTrack ?: return
        t.switchCamera()
    }

    /** Выход из конференции или потеря соединения: освобождает микрофон, камеру и службу переднего плана. */
    fun leave() { val mid = _ui.value.meetingId; val a = api; if (a != null && mid.isNotEmpty()) scope.launch { a.leave(mid) }; finish(null) }

    private fun finish(reason: String?) {
        if (_ui.value.phase == Phase.Idle) return
        recovering?.cancel(); recovering = null
        jobs.forEach { it.cancel() }; jobs.clear()
        releaseRoom()
        CallService.stop(app)
        _ui.update { it.copy(phase = Phase.Ended, endReason = reason, tiles = emptyList()) }
    }

    private fun releaseRoom() {
        roomJobs.forEach { it.cancel() }; roomJobs.clear()
        val r = room; room = null
        if (r != null) scope.launch(NonCancellable) {
            // Освобождение WebRTC сразу после disconnect() иногда завершает процесс нативным сбоем (SIGSEGV в потоке AudioDeviceBuffer, пойман на эмуляторе при частых входах и выходах):
            // захват звука и видео ещё работает, а к трекам ещё подключены отрисовщики. Поэтому сначала останавливаем захват, отключаемся и освобождаем ресурсы с паузой,
            // когда интерфейс уже отпустил треки (список участников пуст).
            runCatching { r.localParticipant.setMicrophoneEnabled(false); r.localParticipant.setCameraEnabled(false) }
            runCatching { r.localParticipant.setScreenShareEnabled(false) }
            runCatching { r.disconnect() }
            delay(RELEASE_DELAY_MS)
            runCatching { r.release() }
        }
    }

    /** Экран закрыт после завершения: возвращаемся в исходное состояние. */
    fun dismissEnded() { if (_ui.value.phase == Phase.Ended) _ui.value = CallUi() }

    // ── сворачивание: камера в фоне недоступна ───────────────────────────────────────────
    fun onBackground() {
        val r = room ?: return
        if (sourceOn(r.localParticipant, Track.Source.CAMERA)) { cameraWasOn = true; scope.launch { runCatching { r.localParticipant.setCameraEnabled(false) }; refresh() } }
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
