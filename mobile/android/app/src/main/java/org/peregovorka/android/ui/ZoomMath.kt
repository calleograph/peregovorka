package org.peregovorka.android.ui

import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min

/**
 * Приближение демонстрации экрана: чистая математика без Android — проверяется обычными JVM-тестами.
 *
 * Модель: область просмотра (контейнер) W×H; картинка вписана в неё с сохранением пропорций (как «FitInside»), затем весь слой увеличивается в [scale] раз вокруг центра
 * контейнера и сдвигается на ([offsetX], [offsetY]) пикселей. Сдвиг ограничен так, чтобы края увеличенной картинки не уходили внутрь области просмотра: изображение нельзя
 * «потерять» за экраном, а если картинка по какой-то оси меньше области просмотра, она остаётся по центру.
 */
data class ZoomState(val scale: Float = 1f, val offsetX: Float = 0f, val offsetY: Float = 0f) {
    val zoomed: Boolean get() = scale > ZoomMath.RESET_BELOW
}

/** Размер в пикселях. */
data class Size2(val w: Float, val h: Float)

object ZoomMath {
    const val MIN_SCALE = 1f
    /** Предел приближения: при 8× текст 4K-монитора, переданный слоем 1080p, всё ещё читается, а телефон не тратит память на лишние слои. */
    const val MAX_SCALE = 8f
    /** Быстрое приближение по двойному нажатию. */
    const val DOUBLE_TAP_SCALE = 2.5f
    /** Ниже этого масштаба изображение считается неувеличенным (сброс сдвига, возврат качества по размеру окна). */
    const val RESET_BELOW = 1.02f

    /** Размер картинки, вписанной в контейнер (пропорции сохраняются). Размер источника неизвестен — считаем, что картинка занимает весь контейнер. */
    fun fitted(container: Size2, source: Size2?): Size2 {
        if (source == null || source.w <= 0f || source.h <= 0f || container.w <= 0f || container.h <= 0f) return container
        val k = min(container.w / source.w, container.h / source.h)
        return Size2(source.w * k, source.h * k)
    }

    /** Приводит состояние к допустимому: масштаб в пределах, сдвиг — так, чтобы края картинки не уходили внутрь области просмотра. */
    fun clamp(s: ZoomState, container: Size2, content: Size2): ZoomState {
        val sc = s.scale.coerceIn(MIN_SCALE, MAX_SCALE)
        if (sc <= RESET_BELOW) return ZoomState()
        val maxX = max(0f, (content.w * sc - container.w) / 2f)
        val maxY = max(0f, (content.h * sc - container.h) / 2f)
        return ZoomState(sc, s.offsetX.coerceIn(-maxX, maxX), s.offsetY.coerceIn(-maxY, maxY))
    }

    /** Жест двумя пальцами: изменение масштаба в [factor] раз вокруг точки [focal] (координаты в контейнере) и одновременный сдвиг на ([panX], [panY]). */
    fun transform(s: ZoomState, focalX: Float, focalY: Float, factor: Float, panX: Float, panY: Float, container: Size2, content: Size2): ZoomState {
        val ns = (s.scale * factor).coerceIn(MIN_SCALE, MAX_SCALE)
        val k = ns / s.scale
        val fx = focalX - container.w / 2f
        val fy = focalY - container.h / 2f
        // точка под пальцами остаётся на месте: o' = (f − c)(1 − k) + k·o
        return clamp(ZoomState(ns, fx * (1 - k) + k * s.offsetX + panX, fy * (1 - k) + k * s.offsetY + panY), container, content)
    }

    /** Двойное нажатие: увеличено — вернуть целиком; иначе — приблизить в [DOUBLE_TAP_SCALE] раз к точке нажатия. */
    fun doubleTap(s: ZoomState, x: Float, y: Float, container: Size2, content: Size2): ZoomState =
        if (s.zoomed) ZoomState() else transform(s, x, y, DOUBLE_TAP_SCALE, 0f, 0f, container, content)

    /**
     * Смена размера области просмотра (поворот телефона, полноэкранный режим): сохраняется та часть картинки, которая была в центре, в долях от размера картинки.
     * Масштаб сохраняется относительно вписанной картинки; если новое состояние не получается привести к допустимому (картинка стала меньше области) — возврат к целому.
     */
    fun resize(s: ZoomState, oldContainer: Size2, oldContent: Size2, newContainer: Size2, newContent: Size2): ZoomState {
        if (!s.zoomed || oldContent.w <= 0f || oldContent.h <= 0f) return ZoomState()
        // центр области просмотра в долях картинки (−0,5…0,5 от её центра)
        val fx = -s.offsetX / (oldContent.w * s.scale)
        val fy = -s.offsetY / (oldContent.h * s.scale)
        val moved = ZoomState(s.scale, -fx * newContent.w * s.scale, -fy * newContent.h * s.scale)
        return clamp(moved, newContainer, newContent)
    }

    /** Нужна ли картинка в полном разрешении: слой, выбранный по размеру окна, при увеличении был бы размытым. */
    fun needsFullResolution(s: ZoomState): Boolean = s.scale > 1.2f

    /** Разумное разрешение, которое стоит запросить у сервера при увеличении: не больше оригинала и не больше 4K (память и декодер телефона). */
    fun requestedSize(source: Size2?, container: Size2, scale: Float): Size2 {
        val want = Size2(container.w * scale, container.h * scale)
        if (source == null) return want
        return Size2(min(want.w, min(source.w, 3840f)), min(want.h, min(source.h, 2160f)))
    }

    fun sameScale(a: Float, b: Float) = abs(a - b) < 0.01f
}
