package org.peregovorka.android

import org.junit.Assert.*
import org.junit.Test
import org.peregovorka.android.ui.*

class PermissionsTest {
    private val room = JoinRequest("r1", "Основная", hasPassword = false, cameraAllowed = true, password = null, mic = true, cam = true)
    private fun none(p: Perm) = false
    private fun all(p: Perm) = true

    @Test fun `разрешения выбираются по версии Android и не запрашивается лишнее`() {
        assertEquals(listOf(Perm.Mic, Perm.Camera, Perm.Bluetooth, Perm.Notifications), PermissionPlan.forFirstRun(34))
        assertEquals(listOf(Perm.Mic, Perm.Camera, Perm.Bluetooth), PermissionPlan.forFirstRun(31))
        assertEquals(listOf(Perm.Mic, Perm.Camera), PermissionPlan.forFirstRun(29))                         // Android 10: ни Bluetooth, ни уведомления разрешением не являются
        assertEquals(listOf(Perm.Mic), PermissionPlan.forJoin(34, mic = true, camera = false, extrasAsked = true))
        assertEquals(emptyList<Perm>(), PermissionPlan.forJoin(34, mic = false, camera = false, extrasAsked = true))
        assertEquals(listOf(Perm.Mic, Perm.Bluetooth, Perm.Notifications), PermissionPlan.forJoin(34, mic = true, camera = false, extrasAsked = false))
    }

    @Test fun `уже выданное повторно не запрашивается и вход идёт сразу`() {
        val f = JoinPermissionFlow(34)
        val st = f.begin(room, extrasAsked = true, ::all)
        assertEquals(JoinStep.Join(room, mic = true, cam = true), st)
    }

    @Test fun `после выдачи разрешения прерванный вход продолжается в ту же комнату с теми же параметрами`() {
        val f = JoinPermissionFlow(34)
        val st = f.begin(room.copy(password = "pw"), extrasAsked = true, ::none)
        assertEquals(JoinStep.Ask(listOf(Perm.Mic, Perm.Camera)), st)
        val r = f.result { it == Perm.Mic || it == Perm.Camera }
        assertEquals("r1", r!!.request.roomId); assertEquals("pw", r.request.password)
        assertTrue(r.mic && r.cam)
    }

    @Test fun `отказ не блокирует вход — человек подключается слушателем, а намерение сохраняется`() {
        val f = JoinPermissionFlow(34)
        f.begin(room, extrasAsked = true, ::none)
        val r = f.result(::none)!!
        assertFalse(r.mic); assertFalse(r.cam)
        assertTrue("намерение (включить микрофон) не теряется: после выдачи в настройках устройство включится само", r.request.mic && r.request.cam)
    }

    @Test fun `выдан только микрофон — камера остаётся выключенной`() {
        val f = JoinPermissionFlow(34)
        f.begin(room, extrasAsked = true, ::none)
        val r = f.result { it == Perm.Mic }!!
        assertTrue(r.mic); assertFalse(r.cam)
    }

    @Test fun `повторное нажатие во время запроса не создаёт второй запрос`() {
        val f = JoinPermissionFlow(34)
        assertTrue(f.begin(room, true, ::none) is JoinStep.Ask)
        assertEquals(JoinStep.Busy, f.begin(room, true, ::none))
        f.result(::none)
        assertTrue("после ответа новый запрос возможен", f.begin(room, true, ::none) is JoinStep.Ask)
    }

    @Test fun `ответ без прерванного входа ничего не запускает`() {
        assertNull(JoinPermissionFlow(34).result(::all))
    }

    @Test fun `после пересоздания процесса вход продолжается по сохранённому состоянию`() {
        val before = JoinPermissionFlow(34)
        before.begin(room, true, ::none)                          // здесь процесс завершился; «пароль» в сохранённое состояние не попадает
        val after = JoinPermissionFlow(34)
        after.restore(room)
        val r = after.result(::all)!!
        assertEquals("r1", r.request.roomId); assertTrue(r.mic && r.cam)
        after.clear()
        assertNull(after.result(::all))
    }

    @Test fun `итог запроса различает отказ и окончательный отказ`() {
        assertEquals(Access.Granted, PermissionPlan.classify(true, false))
        assertEquals(Access.Denied, PermissionPlan.classify(false, true))
        assertEquals(Access.Blocked, PermissionPlan.classify(false, false))
    }
}
