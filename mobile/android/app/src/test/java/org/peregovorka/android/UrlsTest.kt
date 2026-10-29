package org.peregovorka.android

import org.peregovorka.android.net.LinkTarget
import org.peregovorka.android.net.normalizeServerUrl
import org.peregovorka.android.net.parseMeetingLink
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull

class UrlsTest {
    @Test fun `адрес без схемы получает https и теряет путь`() {
        assertEquals("https://meeting.example.org", normalizeServerUrl("meeting.example.org"))
        assertEquals("https://meeting.example.org", normalizeServerUrl("  https://Meeting.Example.org/ "))
        assertEquals("https://meeting.example.org:8443", normalizeServerUrl("https://meeting.example.org:8443/path?x=1"))
        assertEquals("https://meeting.example.org", normalizeServerUrl("https://meeting.example.org:443"))
    }

    @Test fun `http, учётные данные и мусор отклоняются`() {
        assertNull(normalizeServerUrl("http://meeting.example.org"))
        assertNull(normalizeServerUrl("https://user:pass@meeting.example.org"))
        assertNull(normalizeServerUrl("ftp://meeting.example.org"))
        assertNull(normalizeServerUrl("javascript:alert(1)"))
        assertNull(normalizeServerUrl("не адрес"))
        assertNull(normalizeServerUrl(""))
        assertNull(normalizeServerUrl("localhost"))          // без точки в имени и не localhost-режим
    }

    @Test fun `http разрешён только для локального адреса в отладке`() {
        assertEquals("http://10.0.2.2:8004", normalizeServerUrl("http://10.0.2.2:8004", allowLocalHttp = true))
        assertNull(normalizeServerUrl("http://meeting.example.org", allowLocalHttp = true))
    }

    @Test fun `ссылка на комнату разбирается и проверяется по белому списку`() {
        assertEquals(LinkTarget("https://a.example.org", "/rooms/main"), parseMeetingLink("https://a.example.org/rooms/main"))
        assertEquals(LinkTarget("https://a.example.org", "/m/AbC_123"), parseMeetingLink("https://a.example.org/m/AbC_123/"))
        assertEquals(LinkTarget("https://a.example.org", "/rooms/x"), parseMeetingLink("peregovorka://open?url=https%3A%2F%2Fa.example.org%2Frooms%2Fx"))
        assertNull(parseMeetingLink("https://a.example.org/admin"))
        assertNull(parseMeetingLink("https://a.example.org/rooms/../../etc"))
        assertNull(parseMeetingLink("peregovorka://open?url=https%3A%2F%2Fa.example.org%2Frooms%2Fx&cmd=1"))
        assertNull(parseMeetingLink("peregovorka://run?url=https%3A%2F%2Fa.example.org%2F"))
        assertNull(parseMeetingLink("http://a.example.org/rooms/x"))
        assertNull(parseMeetingLink(null))
    }
}
