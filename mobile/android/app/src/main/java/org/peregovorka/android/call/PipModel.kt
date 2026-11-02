package org.peregovorka.android.call

/** Плавающее окно встречи (системная «картинка в картинке»): что в нём показывать и какой формы окно. Чистые правила — проверяются JVM-тестом PipModelTest. */
object PipModel {
    data class Slot(val key: String, val local: Boolean, val screen: Boolean, val camera: Boolean, val speaking: Boolean)

    /** Звонок, ради которого приложение при сворачивании переходит в плавающее окно: подключение, встреча, восстановление связи. */
    fun wanted(phase: Phase) = phase == Phase.Connecting || phase == Phase.Connected || phase == Phase.Reconnecting

    /**
     * Кого показывать: чужую демонстрацию экрана; иначе говорящего собеседника (с камерой — в первую очередь); иначе последнего говорившего, пока он в комнате, —
     * чтобы окно не перескакивало в паузах речи; иначе собеседника с камерой, любого собеседника, себя. [lastSpeaker] — только собеседник: сам участник «залипнуть» не может
     * (иначе вошедший первым так и видел бы себя после прихода остальных).
     */
    fun focus(slots: List<Slot>, lastSpeaker: String?): String? {
        val others = slots.filter { !it.local }
        return (others.firstOrNull { it.screen } ?: others.firstOrNull { it.speaking && it.camera } ?: others.firstOrNull { it.speaking }
            ?: others.firstOrNull { it.key == lastSpeaker } ?: others.firstOrNull { it.camera } ?: others.firstOrNull() ?: slots.firstOrNull())?.key
    }

    /** Последний говоривший собеседник: кто говорит сейчас, иначе прежний. */
    fun lastSpeaker(slots: List<Slot>, prev: String?): String? = slots.firstOrNull { !it.local && it.speaking }?.key ?: prev

    /** Пропорции окна: как у демонстрации экрана (в пределах, которые принимает Android: от 1:2,39 до 2,39:1), иначе 16:9. */
    fun aspect(w: Int, h: Int): Pair<Int, Int> = when {
        w <= 0 || h <= 0 -> 16 to 9
        w * 100L > h * 239L -> 239 to 100
        w * 239L < h * 100L -> 100 to 239
        else -> w to h
    }
}

fun Tile.pipSlot() = PipModel.Slot(key, isLocal, screen != null, camera != null, speaking)
