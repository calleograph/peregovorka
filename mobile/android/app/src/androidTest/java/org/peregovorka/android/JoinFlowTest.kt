package org.peregovorka.android

import androidx.lifecycle.Lifecycle
import androidx.test.core.app.ActivityScenario
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.uiautomator.By
import androidx.test.uiautomator.Until
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

    /** Пояснение перед общим запросом разрешений (первый запуск после входа, пока что-то не выдано): закрываем, если оно есть, — тесты входа его не проверяют. */
    private fun dismissPermissionsIntro() { device().wait(Until.findObject(By.text("Позже")), 2_500)?.click() }

    private fun joinFirstRoom(sc: ActivityScenario<MainActivity>) {
        dismissPermissionsIntro()
        device().waitFor(By.text("Основная"))
        device().findObjects(By.text("Войти"))[0].click()
        device().waitFor(By.text("Подключиться"))!!.click()
    }

    /** `ActivityScenario.recreate()` ждёт RESUMED, а под системным окном Activity на паузе, поэтому пересоздаём её напрямую (так её и пересоздаёт система). */
    private fun recreateWhileCovered(sc: ActivityScenario<MainActivity>) { sc.onActivity { it.recreate() }; Thread.sleep(1_500) }

    private val callScreen = By.text(Pattern.compile("Подключение…|Вы вышли из конференции|Тестовая организация|Связь потеряна.*"))

    /** Системное окно запроса сейчас на экране (кнопка «разрешить» контроллера разрешений). */
    private fun permissionDialogShown(ms: Long = 20_000) = device().wait(Until.findObject(By.res(Pattern.compile("com\\.android\\.permissioncontroller:id/permission_(allow|deny).*|com\\.android\\.packageinstaller:id/permission_(allow|deny).*"))), ms) != null

    /**
     * Первый запуск после входа: приложение само предлагает общий запрос («Доступ для конференций»). Пока показано системное окно, Activity пересоздаётся (так ведут себя
     * часть устройств) — раньше после этого прерванное действие терялось. Затем вход в конференцию не вызывает ни одного запроса и сразу открывает экран конференции.
     */
    @Test fun t1_firstRunPermissionsIntro_survivesRecreation_thenJoinAsksNothing() {
        resetApp(fake, permissionsIntro = true)
        launchApp().use { sc ->
            assertNotNull("пояснение «Доступ для конференций» не появилось", device().waitFor(By.text("Доступ для конференций"), 15_000))
            device().findObject(By.text("Продолжить"))!!.click()
            assertTrue("системное окно разрешений не появилось", permissionDialogShown())
            recreateWhileCovered(sc)
            val granted = acceptPermissionDialogs(15_000)
            android.util.Log.i("PGTEST", "принято системных запросов разрешений: $granted")
            assertNotNull("после запросов разрешений список переговорных не появился (пакет: ${device().currentPackageName})", device().waitFor(By.text("Основная"), 15_000))
            assertFalse("пояснение показано повторно", device().hasObject(By.text("Доступ для конференций")))
            device().findObjects(By.text("Войти"))[0].click()
            device().waitFor(By.text("Подключиться"))!!.click()
            assertFalse("при выданных разрешениях вход не должен вызывать системных запросов", permissionDialogShown(2_500))
            assertNotNull("экран конференции не появился (пакет: ${device().currentPackageName})", device().waitFor(callScreen, 20_000))
            assertStaysForeground(sc, 6_000, "вход после общего запроса")
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
            // после возврата конференция показана; если связь за время фона кратко пропала — SDK переподключается сам («Связь потеряна…» → снова «Подключено»), но не завершается
            assertNotNull("после возврата конференция не показана", device().waitFor(By.text(Pattern.compile("Тестовая организация|Связь потеряна.*")), 15_000))
            assertNotNull("соединение не восстановилось после фона за 40 с", device().waitFor(By.text("Тестовая организация"), 40_000))
            assertStaysForeground(sc, 3_000, "после возврата из фона")
            assertFalse("конференция завершилась после сворачивания", device().hasObject(By.text("Вы вышли из конференции")))
        }
    }

    private fun joinWithSwitches(camera: Boolean, allow: Boolean) {
        dismissPermissionsIntro()
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

    /**
     * Отказ в разрешениях: вход всё равно выполняется слушателем (приложение не сворачивается), а рядом с кнопками сказано, что не разрешено. Системное окно запроса перед
     * входом переживает пересоздание Activity: прерванный вход продолжается автоматически, без повторного нажатия «Подключиться».
     */
    @Test fun t0_deniedPermissions_joinContinuesAfterRecreation_asListener() {
        resetApp(fake)
        // свежие разрешения нужны отозванными: отзыв из самого теста убил бы процесс, поэтому проверяем только при их отсутствии
        val granted = appContext().checkSelfPermission(android.Manifest.permission.RECORD_AUDIO) == android.content.pm.PackageManager.PERMISSION_GRANTED
        org.junit.Assume.assumeFalse("разрешения уже выданы предыдущими тестами", granted)
        launchApp().use { sc ->
            dismissPermissionsIntro()
            device().waitFor(By.text("Основная"))
            device().findObjects(By.text("Войти"))[0].click()
            device().waitFor(By.text("Подключиться"))
            device().findObjects(By.checkable(true)).getOrNull(1)?.click()                   // камера включена
            device().waitFor(By.text("Подключиться"))!!.click()
            assertTrue("системное окно разрешений не появилось", permissionDialogShown())
            recreateWhileCovered(sc)                                                         // Activity пересоздана, пока система спрашивает
            acceptPermissionDialogs(10_000, allowAll = false)
            assertNotNull("после отказа и пересоздания экран конференции не появился — прерванный вход потерян", device().waitFor(callScreen, 20_000))
            assertStaysForeground(sc, 5_000, "вход с отказом в разрешениях")
            assertNotNull("рядом с кнопками нет пояснения, что не разрешено", device().waitFor(By.textContains("нет доступа"), 10_000))
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
