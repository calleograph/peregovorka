package org.peregovorka.android.ui

import android.Manifest

/**
 * Разрешения приложения: что нужно, зачем и когда спрашивать. Логика не зависит от Android (проверяется обычными JVM-тестами); системные запросы и проверки выполняет AppRoot.
 *
 * Принципы:
 *  • спрашиваем только то, чем приложение действительно пользуется: микрофон, камера, Bluetooth-гарнитура (Android 12+), уведомление идущей конференции (Android 13+);
 *  • один общий запрос на первом запуске после входа (система сама показывает отдельные окна по группам) и по необходимости — перед конкретным действием;
 *  • уже выданное повторно не спрашиваем; отказ не блокирует работу: конференция идёт слушателем, а что нужно разрешить — сказано рядом с кнопкой.
 */
enum class Perm(val manifest: String, val title: String, val why: String, val minSdk: Int = 1) {
    Mic(Manifest.permission.RECORD_AUDIO, "Микрофон", "чтобы вас слышали в конференции"),
    Camera(Manifest.permission.CAMERA, "Камера", "чтобы показывать себя в видео"),
    Bluetooth(Manifest.permission.BLUETOOTH_CONNECT, "Устройства поблизости", "чтобы звук шёл в Bluetooth-гарнитуру", 31),
    Notifications(Manifest.permission.POST_NOTIFICATIONS, "Уведомления", "чтобы идущая конференция была видна в шторке, а из неё можно было выключить микрофон или выйти", 33),
}

enum class Access { Granted, Denied, Blocked }

object PermissionPlan {
    private fun applicable(sdk: Int, p: Perm) = sdk >= p.minSdk

    /** Всё, что приложению может понадобиться: общий запрос на первом запуске. */
    fun forFirstRun(sdk: Int): List<Perm> = Perm.entries.filter { applicable(sdk, it) }

    /**
     * Что запросить перед входом в конференцию: нужные для выбранных действий микрофон и камера, а если общий запрос ещё не делался (`extrasAsked == false`) —
     * заодно Bluetooth и уведомления (один раз, а не при каждом входе).
     */
    fun forJoin(sdk: Int, mic: Boolean, camera: Boolean, extrasAsked: Boolean): List<Perm> = buildList {
        if (mic) add(Perm.Mic)
        if (camera) add(Perm.Camera)
        if (!extrasAsked) { add(Perm.Bluetooth); add(Perm.Notifications) }
    }.filter { applicable(sdk, it) }

    fun missing(list: List<Perm>, granted: (Perm) -> Boolean): List<Perm> = list.filterNot(granted)

    /** После запроса: выдано; отказано (спросить можно ещё раз); отказано окончательно (система окно больше не покажет — только настройки). */
    fun classify(granted: Boolean, canAskAgain: Boolean): Access = if (granted) Access.Granted else if (canAskAgain) Access.Denied else Access.Blocked
}

/** Параметры входа, ради которых запрашиваются разрешения: после ответа системы вход продолжается именно с ними. */
data class JoinRequest(
    val roomId: String, val name: String, val hasPassword: Boolean, val cameraAllowed: Boolean,
    val password: String?, val mic: Boolean, val cam: Boolean,
)

sealed interface JoinStep {
    /** Разрешения не нужны или уже получены: входим (микрофон и камера — только если разрешены). */
    data class Join(val request: JoinRequest, val mic: Boolean, val cam: Boolean) : JoinStep
    /** Нужен системный запрос этих разрешений. */
    data class Ask(val perms: List<Perm>) : JoinStep
    /** Запрос уже идёт — повторное нажатие игнорируется (иначе окна разрешений накладывались бы). */
    data object Busy : JoinStep
}

/**
 * Вход в конференцию, прерванный запросом разрешений. Состояние хранится в ViewModel, а не в Compose-переменных экрана, поэтому не теряется при пересоздании
 * Activity, пока система показывает окно разрешений (на части устройств оно приводит к пересозданию), и действие продолжается автоматически после ответа.
 */
class JoinPermissionFlow(private val sdk: Int) {
    var pending: JoinRequest? = null; private set
    var asking: Boolean = false; private set

    fun begin(req: JoinRequest, extrasAsked: Boolean, granted: (Perm) -> Boolean): JoinStep {
        if (asking) return JoinStep.Busy
        pending = req
        val need = PermissionPlan.missing(PermissionPlan.forJoin(sdk, req.mic, req.cam, extrasAsked), granted)
        if (need.isEmpty()) return finish(granted)!!
        asking = true
        return JoinStep.Ask(need)
    }

    /** Ответ системы (или возврат из настроек): продолжить прерванный вход. null — продолжать нечего. */
    fun result(granted: (Perm) -> Boolean): JoinStep.Join? { asking = false; return finish(granted) }

    /** Состояние восстановлено после пересоздания процесса: ответ системы придёт уже новому экземпляру. */
    fun restore(req: JoinRequest?) { pending = req; asking = req != null }

    fun clear() { pending = null; asking = false }

    private fun finish(granted: (Perm) -> Boolean): JoinStep.Join? {
        val r = pending ?: return null
        return JoinStep.Join(r, r.mic && granted(Perm.Mic), r.cam && granted(Perm.Camera))
    }
}
