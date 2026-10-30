package org.peregovorka.android

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.size
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.unit.dp
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.peregovorka.android.ui.Size2
import org.peregovorka.android.ui.ZoomSurface
import org.peregovorka.android.ui.ZoomState

/**
 * Жесты приближения демонстрации экрана на настоящем Android (эмулятор: касания идут через систему ввода Compose): сведение/разведение двумя пальцами, перемещение пальцем,
 * двойное нажатие, кнопка «Показать целиком», полноэкранный режим. Вместо видео — цветной блок: проверяется преобразование слоя, а не декодер.
 * На реальном телефоне жесты проверяются вручную (docs/ANDROID.md) — эмулятор не заменяет настоящий сенсорный экран.
 */
class ZoomSurfaceTest {
    @get:Rule val rule = createComposeRule()

    private var fullscreen = false
    private var taps = 0
    private var last = ZoomState()

    private fun show() {
        rule.setContent {
            ZoomSurface(
                key = "t", source = Size2(3840f, 2160f), label = "Докладчик · экран", fullscreen = fullscreen, onFullscreen = { fullscreen = it }, onTap = { taps++ },
                modifier = Modifier.size(width = 400.dp, height = 700.dp).testTag("zoom"), onZoomChanged = { z, _ -> last = z },
            ) { Box(Modifier.fillMaxSize().background(Color(0xFF2255AA))) }
        }
    }

    private fun state(): String = rule.onNodeWithTag("zoom").fetchSemanticsNode().config[SemanticsProperties.StateDescription]
    private fun scale(): Float = Regex("масштаб ([0-9]+[.,][0-9]+)").find(state())!!.groupValues[1].replace(',', '.').toFloat()

    @Test fun pinchOutZoomsInAndPinchInZoomsBack() {
        show()
        assertEquals(1f, scale(), 0.01f)
        rule.onNodeWithTag("zoom").performTouchInput {
            val c = center
            pinch(c - Offset(40f, 0f), c - Offset(100f, 0f), c + Offset(40f, 0f), c + Offset(100f, 0f))          // разведение: pinch(начало и конец пути первого пальца, затем второго); расстояние между пальцами ×2,5
        }
        rule.waitForIdle()
        val zoomedIn = scale()
        assertTrue("масштаб после разведения: $zoomedIn", zoomedIn > 1.5f)
        rule.onNodeWithText("×", substring = true).assertExists()
        rule.onNodeWithTag("zoom").performTouchInput {
            val c = center
            pinch(c - Offset(100f, 0f), c - Offset(40f, 0f), c + Offset(100f, 0f), c + Offset(40f, 0f))          // сведение
        }
        rule.waitForIdle()
        assertTrue("масштаб после сведения ($zoomedIn → ${scale()}) стал меньше", scale() < zoomedIn - 0.3f)
    }

    @Test fun zoomedImageCanBePannedButNotLost() {
        show()
        rule.onNodeWithTag("zoom").performTouchInput { val c = center; pinch(c - Offset(30f, 0f), c - Offset(150f, 0f), c + Offset(30f, 0f), c + Offset(150f, 0f)) }
        rule.waitForIdle()
        val before = state()
        rule.onNodeWithTag("zoom").performTouchInput { swipe(center, center + Offset(-120f, 0f), 200) }
        rule.waitForIdle()
        val after = state()
        assertTrue("сдвиг изменился: $before → $after", before != after)
        rule.onNodeWithTag("zoom").performTouchInput { swipe(center, center + Offset(-5000f, 0f), 200) }          // далеко за край — картинка остаётся в области просмотра
        rule.waitForIdle()
        val m = Regex("сдвиг (-?\\d+), (-?\\d+)").find(state())!!
        assertTrue("сдвиг ограничен краем: $m", kotlin.math.abs(m.groupValues[1].toInt()) < 4000)
    }

    @Test fun doubleTapZoomsInAndSecondDoubleTapReturns() {
        show()
        rule.onNodeWithTag("zoom").performTouchInput { doubleClick() }
        rule.waitForIdle(); rule.mainClock.advanceTimeBy(400)
        assertTrue("после двойного нажатия: ${scale()}", scale() > 2.0f)
        rule.onNodeWithTag("zoom").performTouchInput { doubleClick() }
        rule.waitForIdle(); rule.mainClock.advanceTimeBy(400)
        assertEquals(1f, scale(), 0.05f)
    }

    @Test fun resetButtonReturnsTheWholePicture() {
        show()
        rule.onNodeWithTag("zoom").performTouchInput { val c = center; pinch(c - Offset(30f, 0f), c - Offset(150f, 0f), c + Offset(30f, 0f), c + Offset(150f, 0f)) }
        rule.waitForIdle()
        rule.onNodeWithContentDescription("Показать целиком").performClick()
        rule.waitForIdle(); rule.mainClock.advanceTimeBy(400)
        assertEquals(1f, scale(), 0.05f)
        rule.onNodeWithContentDescription("Показать целиком").assertDoesNotExist()
    }

    @Test fun singleTapIsReportedAndFullscreenButtonToggles() {
        show()
        rule.onNodeWithTag("zoom").performTouchInput { click() }
        rule.waitForIdle(); rule.mainClock.advanceTimeBy(500)
        assertEquals("одиночное касание сообщается (показать/скрыть панель)", 1, taps)
        rule.onNodeWithContentDescription("На весь экран").performClick()
        assertTrue(fullscreen)
    }
}
