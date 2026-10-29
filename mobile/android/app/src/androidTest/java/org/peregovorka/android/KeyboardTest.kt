package org.peregovorka.android

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.uiautomator.By
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

/** Экранная клавиатура на настоящем Android: поле ввода и кнопка действия остаются над клавиатурой, содержимое сжимается, после закрытия всё возвращается. */
@RunWith(AndroidJUnit4::class)
class KeyboardTest {
    private lateinit var fake: FakePeregovorka

    @Before fun up() {
        device().executeShellCommand("settings put secure show_ime_with_hard_keyboard 1")      // на эмуляторе с «аппаратной» клавиатурой экранная иначе не показывается
        fake = FakePeregovorka()
    }

    @After fun down() = fake.close()

    private fun assertAboveKeyboard(what: String, bottom: Int, kbTop: Int) =
        assertTrue("$what перекрыто клавиатурой: нижняя граница $bottom, верх клавиатуры $kbTop (экран ${device().displayHeight})", bottom <= kbTop + 2)

    @Test fun addServerButtonStaysAboveKeyboardAndReturnsAfterClose() {
        resetApp()
        launchApp().use { sc ->
            device().waitFor(By.text("Добавить сервер"))!!.click()
            var kbTop = waitForKeyboardTop(sc, 6_000)
            if (kbTop == null) { device().waitFor(By.clazz("android.widget.EditText"))!!.click(); kbTop = waitForKeyboardTop(sc) }
            assertNotNull("клавиатура не появилась", kbTop)
            val button = device().waitFor(By.text("Проверить"))!!
            assertAboveKeyboard("кнопка «Проверить»", button.visibleBounds.bottom, kbTop!!)
            val field = device().waitFor(By.clazz("android.widget.EditText"))!!
            assertAboveKeyboard("поле адреса", field.visibleBounds.bottom, kbTop)
            val bottomWithKeyboard = button.visibleBounds.bottom
            device().pressBack()                                                                   // закрывает клавиатуру
            val end = System.currentTimeMillis() + 6_000
            while (imeHeight(sc) > 0 && System.currentTimeMillis() < end) Thread.sleep(150)
            assertEquals("клавиатура не закрылась", 0, imeHeight(sc))
            Thread.sleep(600)
            val after = device().waitFor(By.text("Проверить"))!!
            assertTrue("после закрытия клавиатуры кнопка должна вернуться вниз (было $bottomWithKeyboard, стало ${after.visibleBounds.bottom})", after.visibleBounds.bottom > bottomWithKeyboard)
        }
    }

    @Test fun joinSheetPasswordFieldStaysAboveKeyboard() {
        resetApp(fake)
        launchApp().use { sc ->
            device().waitFor(By.text("Закрытая"))
            val enter = device().findObjects(By.text("Войти"))
            assertTrue("нет кнопок «Войти»", enter.size >= 2)
            enter[1].click()
            val field = device().waitFor(By.clazz("android.widget.EditText"))!!
            field.click()
            val kbTop = waitForKeyboardTop(sc)
            assertNotNull("клавиатура не появилась", kbTop)
            val connect = device().waitFor(By.text("Подключиться"))!!
            assertAboveKeyboard("кнопка «Подключиться»", connect.visibleBounds.bottom, kbTop!!)
            assertAboveKeyboard("поле пароля", device().waitFor(By.clazz("android.widget.EditText"))!!.visibleBounds.bottom, kbTop)
        }
    }

    @Test fun chatsPageComposerStaysAboveKeyboardAndNavigationBarHides() {
        resetApp(fake)
        launchApp().use { sc ->
            device().waitFor(By.text("Закрытая"))
            device().waitFor(By.text("Чаты"))!!.click()
            val field = device().waitFor(By.clazz("android.widget.EditText"), 45_000)
            assertNotNull("страница чатов не загрузилась; запросы: ${fake.requests}", field)
            assertTrue("сайт не получил метку приложения (User-Agent): ${fake.requests}", fake.requests.any { it.startsWith("GET /chats ua=") && !it.endsWith("ua=-") })
            field!!.click()
            val kbTop = waitForKeyboardTop(sc)
            assertNotNull("клавиатура не появилась", kbTop)
            val composer = device().waitFor(By.clazz("android.widget.EditText"))!!
            assertAboveKeyboard("поле ввода сообщения", composer.visibleBounds.bottom, kbTop!!)
            assertFalse("нижняя панель разделов должна скрываться на время ввода", device().hasObject(By.text("Календарь")))
            device().pressBack()
            val end = System.currentTimeMillis() + 6_000
            while (imeHeight(sc) > 0 && System.currentTimeMillis() < end) Thread.sleep(150)
            assertNotNull("после закрытия клавиатуры панель разделов не вернулась", device().waitFor(By.text("Календарь"), 5_000))
        }
    }

    @Test fun chatsTabOnOlderServerFallsBackToSiteRoot() {
        fake.serverVersion = "0.49.5"                 // сервер без раздела /chats: приложение не должно открывать несуществующую страницу
        resetApp(fake)
        launchApp().use {
            device().waitFor(By.text("Закрытая"))
            device().waitFor(By.text("Чаты"))!!.click()
            assertNotNull("страница не загрузилась; запросы: ${fake.requests}", device().waitFor(By.clazz("android.widget.EditText"), 45_000))      // первый запуск WebView на холодном эмуляторе бывает долгим
            assertFalse("на старом сервере запрошен /chats: ${fake.requests}", fake.requests.any { it.startsWith("GET /chats") })
            assertTrue("сайт не получил главную страницу: ${fake.requests}", fake.requests.any { it.startsWith("GET / ua=") })
        }
    }
}
