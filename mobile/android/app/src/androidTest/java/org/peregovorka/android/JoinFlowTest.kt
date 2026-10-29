package org.peregovorka.android

import androidx.lifecycle.Lifecycle
import androidx.test.core.app.ActivityScenario
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.uiautomator.By
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.FixMethodOrder
import org.junit.Test
import org.junit.runner.RunWith
import org.junit.runners.MethodSorters
import java.util.regex.Pattern

/**
 * Вход в конференцию на настоящем Android: приложение всё время остаётся на переднем плане (пакет на экране — наш, Activity в состоянии RESUMED),
 * системные запросы разрешений проходят штатно. Порядок важен: сначала запросы разрешений (чистая установка), затем вход с уже выданными.
 * Если задан аргумент livekit_url, подключение идёт к настоящему серверу LiveKit; иначе адрес недоступен и вход заканчивается понятной ошибкой.
 */
@RunWith(AndroidJUnit4::class)
@FixMethodOrder(MethodSorters.NAME_ASCENDING)
class JoinFlowTest {
    private lateinit var fake: FakePeregovorka

    @Before fun up() { fake = FakePeregovorka() }
    @After fun down() { logRequests("запросы", fake); fake.close() }

    /** Следит за передним планом заданное время; любой выход на домашний экран или в другое приложение — провал с описанием момента. */
    private fun assertStaysForeground(sc: ActivityScenario<MainActivity>, ms: Long, what: String) {
        val end = System.currentTimeMillis() + ms
        while (System.currentTimeMillis() < end) {
            val pkg = device().currentPackageName
            assertEquals("$what: на экране не наше приложение ($pkg) — приложение свернулось", PKG, pkg)
            assertEquals("$what: Activity не на переднем плане", Lifecycle.State.RESUMED, sc.state)
            Thread.sleep(250)
        }
    }

    private fun joinFirstRoom(sc: ActivityScenario<MainActivity>) {
        device().waitFor(By.text("Основная"))
        device().findObjects(By.text("Войти"))[0].click()
        device().waitFor(By.text("Подключиться"))!!.click()
    }

    private val callScreen = By.text(Pattern.compile("Подключение…|Вы вышли из конференции|Тестовая организация|Связь потеряна.*"))

    @Test fun t1_joinWithPermissionDialogsKeepsAppInForeground() {
        resetApp(fake)
        launchApp().use { sc ->
            joinFirstRoom(sc)
            val granted = acceptPermissionDialogs()
            android.util.Log.i("PGTEST", "принято системных запросов разрешений: $granted")
            assertNotNull("после запросов разрешений экран конференции не появился (пакет: ${device().currentPackageName})", device().waitFor(callScreen, 20_000))
            assertStaysForeground(sc, 8_000, "вход после запросов разрешений")
            assertTrue("конференция не показана", device().hasObject(By.text("Основная")))
        }
    }

    @Test fun t2_joinWithGrantedPermissionsKeepsAppInForeground() {
        resetApp(fake)
        launchApp().use { sc ->
            // выход из прошлой конференции (если экран «Вы вышли» остался) — состояние процесса общее для тестов
            device().findObject(By.text("Закрыть"))?.click()
            joinFirstRoom(sc)
            assertNotNull("экран конференции не появился (пакет: ${device().currentPackageName})", device().waitFor(callScreen, 20_000))
            assertStaysForeground(sc, 8_000, "вход с выданными разрешениями")
        }
    }

    @Test fun t3_repeatedJoinsToDifferentRoomsDoNotMinimizeTheApp() {
        resetApp(fake)
        launchApp().use { sc ->
            repeat(3) { round ->
                device().findObject(By.text("Закрыть"))?.click()
                assertNotNull("раунд $round: список переговорных не появился", device().waitFor(By.text("Закрытая")))
                val room = if (round % 2 == 0) 0 else 1
                device().findObjects(By.text("Войти"))[room].click()
                if (room == 1) typeIntoFirstField("пароль")
                device().waitFor(By.text("Подключиться"))!!.click()
                acceptPermissionDialogs(3_000)
                assertNotNull("раунд $round: экран конференции не появился", device().waitFor(callScreen, 20_000))
                assertStaysForeground(sc, 4_000, "раунд $round")
                device().findObject(By.desc("Выйти из конференции"))?.click()
                device().waitFor(By.text("Выйти"), 5_000)?.click()                       // подтверждение выхода
                assertNotNull("раунд $round: после выхода нет экрана «Вы вышли»", device().waitFor(By.text("Вы вышли из конференции"), 15_000))
            }
        }
    }

    @Test fun t4_realLivekit_connectionSurvivesBackgroundAndReturn() {
        org.junit.Assume.assumeTrue("не задан livekit_url — проверка с настоящим LiveKit пропущена", fake.realLivekit != null)
        resetApp(fake)
        launchApp().use { sc ->
            device().findObject(By.text("Закрыть"))?.click()
            joinFirstRoom(sc)
            acceptPermissionDialogs(4_000)
            // подключено: под названием комнаты — название сервера, а не «Подключение…»
            assertNotNull("подключение к LiveKit не установилось за 40 с", device().waitFor(By.text("Тестовая организация"), 40_000))
            assertStaysForeground(sc, 6_000, "после подключения к LiveKit")
            // сворачивание: служба переднего плана с микрофоном продолжает работать, соединение остаётся
            device().pressHome()
            Thread.sleep(3_000)
            val services = device().executeShellCommand("dumpsys activity services $PKG")
            assertTrue("служба конференции не работает в фоне:\n$services", services.contains("CallService") && services.contains("isForeground=true"))
            if (Regex("foregroundServiceType=0x[0-9a-f]+").containsMatchIn(services))        // на Android 10 тип службы в выводе не показывается
                assertTrue("тип службы переднего плана — не микрофон:\n$services", Regex("foregroundServiceType=0x0*([0-9a-f]*)").findAll(services).any { (it.groupValues[1].toIntOrNull(16) ?: 0) and 0x80 != 0 })
            device().executeShellCommand("monkey -p $PKG -c android.intent.category.LAUNCHER 1")
            assertNotNull("после возврата конференция не показана", device().waitFor(By.text("Тестовая организация"), 15_000))
            assertStaysForeground(sc, 3_000, "после возврата из фона")
            assertFalse("соединение потеряно после сворачивания", device().hasObject(By.text("Вы вышли из конференции")))
        }
    }

    private fun joinWithSwitches(camera: Boolean, allow: Boolean) {
        device().waitFor(By.text("Основная"))
        device().findObjects(By.text("Войти"))[0].click()
        device().waitFor(By.text("Подключиться"))
        if (camera) device().findObjects(By.checkable(true)).getOrNull(1)?.click()           // второй переключатель листа входа — «Камера включена»
        device().waitFor(By.text("Подключиться"))!!.click()
        acceptPermissionDialogs(10_000, allow)
    }

    @Test fun t5_cameraAndMicrophoneOn_realLivekit_staysInForeground() {
        org.junit.Assume.assumeTrue("не задан livekit_url", fake.realLivekit != null)
        resetApp(fake)
        launchApp().use { sc ->
            device().findObject(By.text("Закрыть"))?.click()
            joinWithSwitches(camera = true, allow = true)
            assertNotNull("подключение с камерой не установилось за 40 с", device().waitFor(By.text("Тестовая организация"), 40_000))
            assertStaysForeground(sc, 8_000, "вход с включённой камерой")
            android.util.Log.i("PGTEST", "камера включена в конференции: ${device().hasObject(By.desc("Выключить камеру"))}")
            val micOn = device().hasObject(By.desc("Выключить микрофон"))
            android.util.Log.i("PGTEST", "микрофон включён в конференции: $micOn")
            device().findObject(By.desc(if (micOn) "Выключить микрофон" else "Включить микрофон"))!!.click()          // переключение микрофона
            assertNotNull("микрофон не переключился", device().waitFor(By.desc(if (micOn) "Включить микрофон" else "Выключить микрофон"), 8_000))
            assertStaysForeground(sc, 2_000, "переключение микрофона")
        }
    }

    @Test fun t0_deniedPermissions_joinAsListenerWithoutLeavingTheApp() {
        resetApp(fake)
        // свежие разрешения нужны отозванными: отзыв из самого теста убил бы процесс, поэтому проверяем только при их отсутствии
        val granted = appContext().checkSelfPermission(android.Manifest.permission.RECORD_AUDIO) == android.content.pm.PackageManager.PERMISSION_GRANTED
        org.junit.Assume.assumeFalse("разрешения уже выданы предыдущими тестами", granted)
        launchApp().use { sc ->
            joinWithSwitches(camera = true, allow = false)
            assertNotNull("после отказа в разрешениях экран конференции не появился", device().waitFor(callScreen, 20_000))
            assertStaysForeground(sc, 6_000, "вход с отказом в разрешениях")
        }
    }

    @Test fun t7_rotationAndBackButtonDoNotMinimizeTheApp() {
        resetApp(fake)
        launchApp().use { sc ->
            device().findObject(By.text("Закрыть"))?.click()
            joinFirstRoom(sc)
            acceptPermissionDialogs(4_000)
            assertNotNull("экран конференции не появился", device().waitFor(callScreen, 20_000))
            try {
                device().setOrientationLeft(); Thread.sleep(2_000)
                assertStaysForeground(sc, 2_500, "поворот экрана")
                assertTrue("после поворота конференция пропала", device().hasObject(By.text("Основная")))
                device().setOrientationNatural(); Thread.sleep(1_500)
            } finally { device().unfreezeRotation() }
            device().pressBack()                                                                   // «Назад» — вопрос о выходе, а не сворачивание
            assertNotNull("«Назад» не показал вопрос о выходе", device().waitFor(By.text("Выйти из конференции?"), 5_000))
            assertStaysForeground(sc, 1_500, "системная кнопка «Назад»")
            device().findObject(By.text("Остаться"))?.click()
            assertNotNull("после «Остаться» конференция пропала", device().waitFor(By.text("Основная"), 8_000))
        }
    }

    @Test fun t6_realLivekit_repeatedJoinAndLeaveDoesNotCrashTheProcess() {
        org.junit.Assume.assumeTrue("не задан livekit_url", fake.realLivekit != null)
        resetApp(fake)
        launchApp().use { sc ->
            repeat(5) { round ->
                device().findObject(By.text("Закрыть"))?.click()
                joinWithSwitches(camera = round % 2 == 1, allow = true)
                assertNotNull("раунд $round: подключение не установилось", device().waitFor(By.text("Тестовая организация"), 40_000))
                assertStaysForeground(sc, 2_500, "раунд $round")
                device().findObject(By.desc("Выйти из конференции"))?.click()
                device().waitFor(By.text("Выйти"), 5_000)?.click()
                assertNotNull("раунд $round: нет экрана «Вы вышли»", device().waitFor(By.text("Вы вышли из конференции"), 15_000))
            }
            Thread.sleep(6_000)                                        // нативный сбой при освобождении приходил через несколько секунд
            assertEquals("процесс приложения не должен был завершиться", PKG, device().currentPackageName)
        }
    }
}
