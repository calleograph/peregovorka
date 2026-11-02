package org.peregovorka.android.call

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** Плавающее окно: кого показывать, какой формы окно и когда в него переходить. */
class PipModelTest {
    private fun s(key: String, local: Boolean = false, screen: Boolean = false, camera: Boolean = false, speaking: Boolean = false) = PipModel.Slot(key, local, screen, camera, speaking)

    @Test fun screenShareWinsOverSpeaker() =
        assertEquals("anna", PipModel.focus(listOf(s("me", local = true, camera = true), s("boris", camera = true, speaking = true), s("anna", screen = true)), "boris"))

    @Test fun ownScreenShareIsNotShown() = assertEquals("boris", PipModel.focus(listOf(s("me", local = true, screen = true), s("boris")), null))

    @Test fun speakerWithCameraFirstThenAnySpeaker() {
        assertEquals("vera", PipModel.focus(listOf(s("boris", speaking = true), s("vera", camera = true, speaking = true)), null))
        assertEquals("boris", PipModel.focus(listOf(s("boris", speaking = true), s("vera", camera = true)), "vera"))
    }

    @Test fun silenceKeepsPreviousWhileHeIsInRoom() {
        val room = listOf(s("me", local = true, camera = true), s("boris"), s("vera", camera = true))
        assertEquals("boris", PipModel.focus(room, "boris"))
        assertEquals("vera", PipModel.focus(room.filter { it.key != "boris" }, "boris"))      // ушёл — следующий с камерой
    }

    @Test fun firstInRoomSeesOthersOnceTheyJoin() {
        assertEquals("me", PipModel.focus(listOf(s("me", local = true)), null))
        assertEquals("boris", PipModel.focus(listOf(s("me", local = true), s("boris", camera = true)), "me"))
    }

    @Test fun lastSpeakerIsOnlyAnotherParticipant() {
        assertEquals(null, PipModel.lastSpeaker(listOf(s("me", local = true, speaking = true), s("boris")), null))
        assertEquals("boris", PipModel.lastSpeaker(listOf(s("me", local = true), s("boris", speaking = true)), "vera"))
        assertEquals("vera", PipModel.lastSpeaker(listOf(s("me", local = true), s("boris")), "vera"))
    }

    @Test fun aloneShowsSelfAndEmptyIsNull() {
        assertEquals("me", PipModel.focus(listOf(s("me", local = true)), null))
        assertEquals(null, PipModel.focus(emptyList(), null))
    }

    @Test fun aspectFollowsShareWithinAndroidLimits() {
        assertEquals(16 to 9, PipModel.aspect(0, 0))
        assertEquals(1920 to 1080, PipModel.aspect(1920, 1080))
        assertEquals(239 to 100, PipModel.aspect(5120, 1080))        // сверхширокий монитор
        assertEquals(100 to 239, PipModel.aspect(400, 2000))         // вертикальный экран телефона
    }

    @Test fun onlyDuringCall() {
        assertTrue(PipModel.wanted(Phase.Connected)); assertTrue(PipModel.wanted(Phase.Reconnecting)); assertTrue(PipModel.wanted(Phase.Connecting))
        assertFalse(PipModel.wanted(Phase.Idle)); assertFalse(PipModel.wanted(Phase.Ended))
    }
}
