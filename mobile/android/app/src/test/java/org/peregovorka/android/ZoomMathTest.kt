package org.peregovorka.android

import org.peregovorka.android.ui.Size2
import org.peregovorka.android.ui.ZoomMath
import org.peregovorka.android.ui.ZoomState
import kotlin.math.abs
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class ZoomMathTest {
    private val phoneLandscape = Size2(2400f, 1080f)
    private val phonePortrait = Size2(1080f, 2200f)
    private val monitor = Size2(3840f, 2160f)

    private fun near(a: Float, b: Float, eps: Float = 0.5f) = assertTrue(abs(a - b) <= eps, "$a ≠ $b")

    @Test fun `картинка вписывается с сохранением пропорций`() {
        val f = ZoomMath.fitted(phonePortrait, monitor)
        near(f.w, 1080f); near(f.h, 607.5f)                                  // в вертикальном положении по ширине: сверху и снизу пустое место
        val l = ZoomMath.fitted(phoneLandscape, monitor)
        near(l.h, 1080f); near(l.w, 1920f)
        assertEquals(phoneLandscape, ZoomMath.fitted(phoneLandscape, null), "размер источника неизвестен — на весь контейнер")
    }

    @Test fun `разведение пальцев увеличивает вокруг точки под пальцами, масштаб ограничен`() {
        val c = phonePortrait; val content = ZoomMath.fitted(c, monitor)
        val s = ZoomMath.transform(ZoomState(), 540f, 1100f, 3f, 0f, 0f, c, content)
        near(s.scale, 3f); near(s.offsetX, 0f); near(s.offsetY, 0f)          // центр экрана — центр картинки: сдвига нет
        val edge = ZoomMath.transform(ZoomState(), 200f, 1100f, 3f, 0f, 0f, c, content)
        assertTrue(edge.offsetX > 0f, "точка у левого края: картинка сдвигается вправо, чтобы эта точка осталась на месте")
        val max = ZoomMath.transform(s, 540f, 1100f, 100f, 0f, 0f, c, content)
        near(max.scale, ZoomMath.MAX_SCALE)
        val min = ZoomMath.transform(s, 540f, 1100f, 0.0001f, 0f, 0f, c, content)
        assertEquals(ZoomState(), min, "сведение до целого — возврат без остаточного сдвига")
    }

    @Test fun `точка под пальцами остаётся на месте при увеличении`() {
        val c = phoneLandscape; val content = ZoomMath.fitted(c, monitor)
        val fx = 1700f; val fy = 400f
        val a = ZoomMath.transform(ZoomState(), fx, fy, 2f, 0f, 0f, c, content)
        // координата точки картинки под пальцами до и после: q = c + (f − c − o)/s
        val before = (fx - c.w / 2) / 1f
        val after = (fx - c.w / 2 - a.offsetX) / a.scale
        near(before, after, 1f)
    }

    @Test fun `сдвиг ограничен краями картинки и не теряет её за экраном`() {
        val c = phonePortrait; val content = ZoomMath.fitted(c, monitor)
        val z = ZoomMath.transform(ZoomState(), 540f, 1100f, 4f, 0f, 0f, c, content)
        val far = ZoomMath.transform(z, 540f, 1100f, 1f, 100000f, -100000f, c, content)
        val maxX = (content.w * 4f - c.w) / 2f
        near(far.offsetX, maxX, 1f)
        near(far.offsetY, -(content.h * 4f - c.h) / 2f, 1f)                   // по высоте масштабированная картинка (2430) чуть больше окна (2200): можно сдвинуть лишь на 115 px
        val tall = ZoomMath.clamp(ZoomState(2f, 0f, 999f), c, content)
        near(tall.offsetY, 0f, 0.1f)                                           // по вертикали масштабированная картинка (1215) меньше окна — остаётся по центру
    }

    @Test fun `двойное нажатие приближает к точке и повторное возвращает целиком`() {
        val c = phoneLandscape; val content = ZoomMath.fitted(c, monitor)
        val z = ZoomMath.doubleTap(ZoomState(), 1500f, 300f, c, content)
        near(z.scale, ZoomMath.DOUBLE_TAP_SCALE); assertTrue(z.zoomed)
        assertEquals(ZoomState(), ZoomMath.doubleTap(z, 10f, 10f, c, content))
    }

    @Test fun `при повороте телефона сохраняется рассматриваемая область`() {
        val c1 = phoneLandscape; val content1 = ZoomMath.fitted(c1, monitor)
        // приблизили правый верхний угол картинки
        var s = ZoomMath.transform(ZoomState(), 2200f, 100f, 3f, 0f, 0f, c1, content1)
        val frac = -s.offsetX / (content1.w * s.scale)                         // положение центра обзора в долях картинки
        val c2 = phonePortrait; val content2 = ZoomMath.fitted(c2, monitor)
        s = ZoomMath.resize(s, c1, content1, c2, content2)
        assertTrue(s.zoomed, "масштаб сохранён")
        val frac2 = -s.offsetX / (content2.w * s.scale)
        assertTrue(frac2 <= frac + 1e-3f && frac2 >= 0f || frac2 <= 0.5f, "обзор остался в той же части картинки (насколько позволяют границы)")
        assertEquals(ZoomState(), ZoomMath.resize(ZoomState(), c1, content1, c2, content2), "неувеличенная картинка остаётся целой")
    }

    @Test fun `полное разрешение запрашивается только при увеличении и не больше 4K`() {
        assertFalse(ZoomMath.needsFullResolution(ZoomState()))
        assertTrue(ZoomMath.needsFullResolution(ZoomState(2f)))
        val r = ZoomMath.requestedSize(monitor, phoneLandscape, 6f)
        near(r.w, 3840f); near(r.h, 2160f)
        val small = ZoomMath.requestedSize(Size2(1280f, 720f), phoneLandscape, 6f)
        near(small.w, 1280f); near(small.h, 720f)                              // больше оригинала не просим
    }
}
