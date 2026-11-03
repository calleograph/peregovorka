package org.peregovorka.android

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.uiautomator.By
import androidx.test.uiautomator.Until
import org.junit.After
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.peregovorka.android.call.Phase
import java.net.HttpURLConnection
import java.net.URL
import java.util.regex.Pattern

/**
 * Показ своего экрана с телефона (MediaProjection) к настоящему LiveKit: системное окно согласия, служба переднего плана с типом mediaProjection (LiveKit SDK),
 * кадры доходят до второго участника (ci/peer.py считает кадры демонстрации от приложения), показ продолжается в свёрнутом приложении, остановка из приложения.
 * Отказ в окне согласия — понятное сообщение. Устройства производителей (свои окна согласия, энергосбережение) здесь не проверяются.
 */
@RunWith(AndroidJUnit4::class)
class ScreenShareTest {
    private lateinit var fake: FakePeregovorka
    private val peer = InstrumentationRegistry.getArguments().getString("peer_url", "http://10.0.2.2:7999")
    private val calls get() = App.instance.calls

    @Before fun up() { fake = FakePeregovorka().also { it.livekitRoom = "long-room"; it.sources = listOf("microphone", "camera", "screen_share") } }
    @After fun down() { logRequests("запросы", fake); fake.close() }

    /** Сколько кадров демонстрации экрана от приложения (identity u1) получил второй участник; -1 — он не отвечает. */
    private fun peerScreenFrames(): Long = runCatching {
        val c = URL("$peer/stats").openConnection() as HttpURLConnection
        c.connectTimeout = 3000; c.readTimeout = 3000
        val j = org.json.JSONObject(c.inputStream.bufferedReader().readText())
        j.optJSONObject("screen_frames")?.optLong("u1", 0) ?: 0
    }.getOrDefault(-1)

    private fun waitUntil(ms: Long, cond: () -> Boolean): Boolean {
        val end = System.currentTimeMillis() + ms
        while (System.currentTimeMillis() < end) { if (cond()) return true; Thread.sleep(500) }
        return cond()
    }

    /** Системное окно MediaProjection: Android 10–13 — «Начать»; 14+ — сначала выбор «Весь экран», затем «Начать»/«Поделиться экраном». */
    private fun acceptScreenCapture(): Boolean {
        val d = device()
        val positive = By.text(Pattern.compile("(?i)(start now|start|share screen|начать|начать трансляцию|поделиться экраном)"))
        val end = System.currentTimeMillis() + 15_000
        while (System.currentTimeMillis() < end) {
            d.findObject(By.clazz("android.widget.Spinner"))?.let { sp ->
                if (sp.findObject(By.text(Pattern.compile("(?i).*(entire screen|весь экран).*"))) == null) {
                    sp.click()
                    d.wait(Until.findObject(By.text(Pattern.compile("(?i).*(entire screen|весь экран).*"))), 3_000)?.click()
                }
            }
            val b = d.findObject(positive) ?: d.findObject(By.res("android:id/button1"))
            if (b != null) { b.click(); Thread.sleep(1_000); if (d.findObject(positive) == null && d.findObject(By.res("android:id/button1")) == null) return true }
            Thread.sleep(500)
        }
        return false
    }

    private fun openMore() { device().findObject(By.desc("Ещё"))!!.click(); device().waitFor(By.text(Pattern.compile("Показать экран|Остановить показ экрана"))) }

    @Test fun screenShare_consent_framesReachPeer_background_stop() {
        assumeTrue("не задан livekit_url", fake.realLivekit != null)
        assumeTrue("второй участник (ci/peer.py) не отвечает по $peer", peerScreenFrames() >= 0)
        resetApp(fake)
        launchApp().use { _ ->
            device().findObject(By.text("Закрыть"))?.click()
            device().wait(Until.findObject(By.text("Позже")), 2_500)?.click()
            device().waitFor(By.text("Основная"))
            device().findObjects(By.text("Войти"))[0].click()
            device().waitFor(By.text("Подключиться"))!!.click()
            acceptPermissionDialogs(4_000)
            assertTrue("не подключились к конференции", waitUntil(40_000) { calls.ui.value.phase == Phase.Connected })
            assertTrue("сервер разрешил показ экрана, а пункта нет", calls.ui.value.canScreen)

            // отказ в окне согласия: показа нет, человеку объяснено
            openMore(); device().findObject(By.text("Показать экран"))!!.click()
            device().wait(Until.findObject(By.text(Pattern.compile("(?i)(cancel|отмена)"))), 10_000)?.click()
            assertTrue("после отказа нет пояснения", waitUntil(5_000) { calls.ui.value.screenNote != null })
            assertFalse(calls.ui.value.screenOn)
            device().findObject(By.text("Понятно"))?.click()

            // согласие: показ идёт, второй участник получает кадры
            openMore(); device().findObject(By.text("Показать экран"))!!.click()
            assertTrue("системное окно согласия на показ экрана не принято", acceptScreenCapture())
            assertTrue("показ экрана не начался: ${calls.ui.value.screenNote}", waitUntil(20_000) { calls.ui.value.screenOn })
            device().waitFor(By.text("Вы показываете экран"))
            val f0 = peerScreenFrames()
            assertTrue("второй участник не получает кадры демонстрации", waitUntil(30_000) { peerScreenFrames() > f0 + 20 })
            android.util.Log.i("PGTEST", "кадров демонстрации у собеседника: ${peerScreenFrames()}")

            // свёрнутое приложение: показ продолжается (захватывается то, что на экране)
            device().pressHome(); Thread.sleep(3_000)
            val f1 = peerScreenFrames()
            assertTrue("в свёрнутом приложении кадры демонстрации перестали приходить", waitUntil(20_000) { peerScreenFrames() > f1 + 10 })
            device().executeShellCommand("monkey -p $PKG -c android.intent.category.LAUNCHER 1"); Thread.sleep(2_000)
            assertTrue("после возврата показ экрана пропал", calls.ui.value.screenOn)

            // блокировка экрана: Android может не отдавать кадры — звонок и показ при этом не обрываются
            device().sleep(); Thread.sleep(10_000)
            android.util.Log.i("PGTEST", "кадров демонстрации при блокировке: ${peerScreenFrames()}")
            device().wakeUp(); device().executeShellCommand("wm dismiss-keyguard"); Thread.sleep(2_000)
            assertNotEquals("звонок прервался при блокировке экрана", Phase.Ended, calls.ui.value.phase)

            // остановка из приложения
            device().waitFor(By.text("Остановить"))!!.click()
            assertTrue("показ экрана не остановился", waitUntil(10_000) { !calls.ui.value.screenOn })
            val f2 = peerScreenFrames(); Thread.sleep(5_000)
            assertTrue("после остановки кадры продолжают приходить", peerScreenFrames() - f2 < 10)

            InstrumentationRegistry.getInstrumentation().runOnMainSync { calls.leave() }
            assertTrue("выход не завершил звонок", waitUntil(10_000) { calls.ui.value.phase == Phase.Ended })
        }
    }
}
