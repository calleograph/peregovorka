package org.peregovorka.android

import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import org.peregovorka.android.data.SecretBox

/** Проверка на настоящем Android Keystore: расшифровать можно только своё значение и только с тем же AAD (изоляция серверов). */
@RunWith(AndroidJUnit4::class)
class KeystoreTest {
    private val box = SecretBox("peregovorka_test_${System.nanoTime()}")

    @Test fun roundtrip() {
        val sealed = box.seal("токен-доверенного-устройства", "trust.s1")
        assertFalse(sealed.contains("токен"))
        assertEquals("токен-доверенного-устройства", box.open(sealed, "trust.s1"))
    }

    @Test fun otherServerCannotReadSecret() {
        val sealed = box.seal("cookie-1", "cookie.s1")
        assertNull(box.open(sealed, "cookie.s2"))
    }

    @Test fun tamperedOrBrokenValueIsRejected() {
        val sealed = box.seal("x", "a")
        assertNull(box.open(sealed.dropLast(4) + "AAAA", "a"))
        assertNull(box.open("не base64", "a"))
    }
}
