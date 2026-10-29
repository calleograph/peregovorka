package org.peregovorka.android

import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Before
import org.junit.Rule
import org.junit.Test

/** Холодный запуск на чистом устройстве: приветствие → добавление сервера → понятная ошибка для негодного адреса. */
class OnboardingTest {
    @Before fun cleanState() {
        InstrumentationRegistry.getInstrumentation().targetContext.getSharedPreferences("peregovorka", 0).edit().clear().commit()
    }

    @get:Rule val rule = createAndroidComposeRule<MainActivity>()

    @Test fun firstLaunchShowsWelcome() {
        rule.waitUntil(5_000) { rule.onAllNodesWithText("Добро пожаловать в Peregovorka").fetchSemanticsNodes().isNotEmpty() }
        rule.onNodeWithText("Добавить сервер").assertIsDisplayed().performClick()
        rule.onNodeWithText("Адрес сервера").assertIsDisplayed()
    }

    @Test fun badAddressShowsError() {
        rule.waitUntil(5_000) { rule.onAllNodesWithText("Добавить сервер").fetchSemanticsNodes().isNotEmpty() }
        rule.onNodeWithText("Добавить сервер").performClick()
        rule.onNodeWithText("Адрес сервера").performTextInput("не адрес")
        rule.onNodeWithText("Проверить").performClick()
        rule.onNodeWithText("Введите адрес в виде https://meeting.example.org").assertIsDisplayed()
    }
}
