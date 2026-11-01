package org.peregovorka.android

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.uiautomator.By
import io.livekit.android.room.track.RemoteAudioTrack
import io.livekit.android.room.track.Track
import io.livekit.android.room.track.VideoTrack
import livekit.org.webrtc.AudioTrackSink
import livekit.org.webrtc.VideoSink
import org.junit.After
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.peregovorka.android.call.Phase
import java.net.HttpURLConnection
import java.net.URL
import java.util.Collections
import java.util.IdentityHashMap
import java.util.concurrent.atomic.AtomicLong

/**
 * Длинный звонок с настоящим LiveKit: второй участник («компьютер», mobile/android/ci/peer.py) показывает экран кодеком VP9, как веб-клиент, и говорит.
 * Проверяется на каждом этапе, что звук идёт в обе стороны и кадры демонстрации приходят и отрисовываются: несколько минут подряд, поворот экрана,
 * сворачивание, блокировка экрана, пропадание сети (приложение должно восстановиться само, а не выбросить человека из встречи).
 * Реальные устройства производителей, аппаратные декодеры и сотовые сети здесь не проверяются.
 */
@RunWith(AndroidJUnit4::class)
class LongCallTest {
    private lateinit var fake: FakePeregovorka
    private val peer = InstrumentationRegistry.getArguments().getString("peer_url", "http://10.0.2.2:7999")
    private val calls get() = App.instance.calls

    private val frames = AtomicLong()      // кадры демонстрации, дошедшие до отрисовки
    private val heard = AtomicLong()       // звуковые кадры собеседника, пришедшие в приложение
    private val watched = Collections.newSetFromMap(IdentityHashMap<Any, Boolean>())

    @Before fun up() { fake = FakePeregovorka().also { it.livekitRoom = "long-room" } }

    /** Что происходит с видео демонстрации внутри WebRTC: пришло ли, декодировано ли и каким декодером (аппаратный MediaCodec или программный libvpx). */
    private fun videoStats(): String {
        val r = calls.room() ?: return "нет комнаты"
        val done = java.util.concurrent.CountDownLatch(1)
        var out = "нет статистики"
        r.getSubscriberRTCStats { report ->
            val codecs = report.statsMap.values.filter { it.type == "codec" }.associate { it.id to (it.members["mimeType"]?.toString() ?: "?") }
            out = report.statsMap.values.filter { it.type == "inbound-rtp" && it.members["kind"] == "video" }.joinToString("; ") { s ->
                val m = s.members
                "${codecs[m["codecId"]] ?: "?"} получено=${m["framesReceived"]} декодировано=${m["framesDecoded"]} ключевых=${m["keyFramesDecoded"]} " +
                    "декодер=${m["decoderImplementation"]} ${m["frameWidth"]}x${m["frameHeight"]} pli=${m["pliCount"]} потеряно=${m["packetsLost"]}"
            }.ifEmpty { "входящего видео нет" }
            done.countDown()
        }
        done.await(5, java.util.concurrent.TimeUnit.SECONDS)
        return out
    }

    private fun micOn() = InstrumentationRegistry.getInstrumentation().runOnMainSync { if (!calls.ui.value.micOn) calls.toggleMic() }
    @After fun down() { logRequests("запросы", fake); fake.close() }

    /** Сколько звуковых кадров приложения услышал «компьютер» (по его счётчику). */
    private fun peerHeard(): Long = runCatching {
        val c = URL("$peer/stats").openConnection() as HttpURLConnection
        c.connectTimeout = 3_000; c.readTimeout = 3_000
        val body = c.inputStream.bufferedReader().readText()
        Regex("\"u1[^\"]*\":\\s*(\\d+)").findAll(body).sumOf { it.groupValues[1].toLong() }
    }.getOrDefault(-1L)

    /** Подключает счётчики к текущим дорожкам собеседника (после повторного входа дорожки новые — подключаются заново). */
    private fun watch() {
        InstrumentationRegistry.getInstrumentation().runOnMainSync {
            val r = calls.room() ?: return@runOnMainSync
            for (p in r.remoteParticipants.values) {
                (p.getTrackPublication(Track.Source.SCREEN_SHARE)?.track as? VideoTrack)?.takeIf { watched.add(it) }?.addRenderer(VideoSink { frames.incrementAndGet() })
                (p.getTrackPublication(Track.Source.MICROPHONE)?.track as? RemoteAudioTrack)?.takeIf { watched.add(it) }
                    ?.addSink(AudioTrackSink { _, _, _, _, _, _ -> heard.incrementAndGet() })
            }
        }
    }

    private fun waitUntil(ms: Long, cond: () -> Boolean): Boolean {
        val end = System.currentTimeMillis() + ms
        while (System.currentTimeMillis() < end) { if (cond()) return true; Thread.sleep(500) }
        return cond()
    }

    private fun assertNotEnded(what: String) {
        val s = calls.ui.value
        assertNotEquals("$what: приложение вышло из встречи (${s.endReason})", Phase.Ended, s.phase)
    }

    /** На этом этапе звонок жив: подключено, звук в обе стороны идёт, кадры демонстрации (если [screen]) приходят. */
    private fun alive(what: String, screen: Boolean = true, ms: Long = 30_000) {
        assertTrue("$what: нет подключения (${calls.ui.value.phase})", waitUntil(ms + 90_000) { calls.ui.value.phase == Phase.Connected || calls.ui.value.phase == Phase.Ended })
        assertNotEnded(what)
        watch()
        val f0 = frames.get(); val h0 = heard.get(); val p0 = peerHeard()
        val ok = waitUntil(ms) { watch(); (!screen || frames.get() > f0 + 3) && heard.get() > h0 + 20 && peerHeard() > p0 + 20 }
        val vs = videoStats()
        android.util.Log.i("PGTEST", "$what: кадров ${frames.get() - f0}, звук к нам ${heard.get() - h0}, звук от нас ${peerHeard() - p0}, входов ${fake.joins.get()}; видео: $vs")
        assertTrue("$what: кадры демонстрации ${frames.get() - f0}, звук к нам ${heard.get() - h0}, звук от нас ${peerHeard() - p0}; видео: $vs", ok)
        assertNotEnded(what)
    }

    @Test fun longCall_screenShare_twoWayAudio_rotation_background_lock_networkLoss() {
        assumeTrue("не задан livekit_url", fake.realLivekit != null)
        assumeTrue("второй участник (ci/peer.py) не отвечает по $peer", peerHeard() >= 0)
        resetApp(fake)
        launchApp().use { _ ->
            device().findObject(By.text("Закрыть"))?.click()
            device().wait(androidx.test.uiautomator.Until.findObject(By.text("Позже")), 2_500)?.click()
            device().waitFor(By.text("Основная"))
            device().findObjects(By.text("Войти"))[0].click()
            device().waitFor(By.text("Подключиться"))!!.click()
            acceptPermissionDialogs(4_000)
            assertTrue("демонстрация экрана собеседника не появилась за 40 с", waitUntil(40_000) { calls.ui.value.tiles.any { it.screen != null } })
            val mime = calls.room()?.remoteParticipants?.values?.firstNotNullOfOrNull { it.getTrackPublication(Track.Source.SCREEN_SHARE)?.mimeType }
            android.util.Log.i("PGTEST", "кодек демонстрации: $mime")
            micOn()
            alive("после входа")

            // несколько минут подряд: связь не обрывается, звук и картинка идут
            repeat(4) { alive("через ${(it + 1) * 40} с звонка") ; Thread.sleep(10_000) }

            device().setOrientationLandscape(); Thread.sleep(2_000)
            alive("после поворота в альбомную ориентацию")
            device().setOrientationPortrait(); Thread.sleep(2_000)
            alive("после поворота обратно")
            device().unfreezeRotation()

            device().pressHome(); Thread.sleep(20_000)
            alive("в свёрнутом приложении (звук)", screen = false)
            device().executeShellCommand("monkey -p $PKG -c android.intent.category.LAUNCHER 1"); Thread.sleep(2_000)
            alive("после возврата из фона")

            device().sleep(); Thread.sleep(30_000)
            alive("при заблокированном экране (звук)", screen = false)
            device().wakeUp(); device().executeShellCommand("wm dismiss-keyguard"); Thread.sleep(2_000)
            alive("после разблокировки")

            // сеть пропадает на 20 с: сервер звонков отключает участника — приложение входит заново само
            val joinsBefore = fake.joins.get()
            device().executeShellCommand("svc wifi disable"); device().executeShellCommand("svc data disable")
            Thread.sleep(20_000)
            device().executeShellCommand("svc wifi enable"); device().executeShellCommand("svc data enable")
            alive("после восстановления сети", ms = 60_000)
            android.util.Log.i("PGTEST", "повторных входов после обрыва сети: ${fake.joins.get() - joinsBefore}")

            InstrumentationRegistry.getInstrumentation().runOnMainSync { calls.leave() }
            assertTrue("выход не завершил звонок", waitUntil(10_000) { calls.ui.value.phase == Phase.Ended })
            assertNull("при выходе показана причина, будто выбросило: ${calls.ui.value.endReason}", calls.ui.value.endReason)
        }
    }

    /** Сравнение: демонстрация кодеком VP8 (второй участник ci/peer.py --codec vp8 в комнате long-room-vp8). Если VP8 показывается, а VP9 нет — дело в декодировании VP9 на устройстве. */
    @Test fun screenShareVp8_framesArrive() {
        assumeTrue("не задан livekit_url", fake.realLivekit != null)
        val vp8 = InstrumentationRegistry.getArguments().getString("peer_vp8_url", "http://10.0.2.2:7998")
        assumeTrue("второй участник VP8 не отвечает", runCatching { (URL("$vp8/stats").openConnection() as HttpURLConnection).apply { connectTimeout = 3_000 }.inputStream.close() }.isSuccess)
        fake.livekitRoom = "long-room-vp8"
        resetApp(fake)
        launchApp().use { _ ->
            device().findObject(By.text("Закрыть"))?.click()
            device().wait(androidx.test.uiautomator.Until.findObject(By.text("Позже")), 2_500)?.click()
            device().waitFor(By.text("Основная"))
            device().findObjects(By.text("Войти"))[0].click()
            device().waitFor(By.text("Подключиться"))!!.click()
            acceptPermissionDialogs(4_000)
            assertTrue("демонстрация VP8 не появилась", waitUntil(40_000) { calls.ui.value.tiles.any { it.screen != null } })
            watch()
            val ok = waitUntil(30_000) { watch(); frames.get() > 5 }
            val vs = videoStats()
            android.util.Log.i("PGTEST", "VP8: кадров ${frames.get()}; видео: $vs")
            assertTrue("кадры демонстрации VP8 не пришли: ${frames.get()}; видео: $vs", ok)
            InstrumentationRegistry.getInstrumentation().runOnMainSync { calls.leave() }
        }
    }
}
