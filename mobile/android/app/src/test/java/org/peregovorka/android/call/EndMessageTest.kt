package org.peregovorka.android.call

import io.livekit.android.events.DisconnectReason
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** Что делает приложение по причине отключения от сервера звонков: обрыв связи — входит заново, конец встречи и удаление — экран завершения. */
class EndMessageTest {
    @Test fun networkAndServerFailuresAreRecovered() {
        for (r in listOf(DisconnectReason.UNKNOWN_REASON, DisconnectReason.SIGNAL_CLOSE, DisconnectReason.JOIN_FAILURE, DisconnectReason.SERVER_SHUTDOWN,
                         DisconnectReason.STATE_MISMATCH, DisconnectReason.MIGRATION, DisconnectReason.CONNECTION_TIMEOUT, DisconnectReason.MEDIA_FAILURE, null))
            assertNull("$r — повторный вход", CallManager.endMessage(r))
    }
    @Test fun endedOrRemovedShowsReason() {
        assertEquals("Встреча завершена.", CallManager.endMessage(DisconnectReason.ROOM_DELETED))
        assertEquals("Вас отключили от встречи.", CallManager.endMessage(DisconnectReason.PARTICIPANT_REMOVED))
        assertEquals("", CallManager.endMessage(DisconnectReason.CLIENT_INITIATED))
    }
    /** Сигнал проходит, медиа нет: после NO_MEDIA_LIMIT таких попыток подряд приложение останавливается с сообщением, а не переподключается по кругу. */
    @Test fun noMediaStopsTheLoop() {
        assertEquals(true, CallManager.keepTrying(1))
        assertEquals(false, CallManager.keepTrying(CallManager.NO_MEDIA_LIMIT))
    }
}
