package com.paganini.voxvault

import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test

class BackendUrlTest {
    @Test fun resolvesArchiveAndUploadWithConfiguredPort() {
        assertEquals("http://vault.local:6100/ui/explorer", BackendUrl.resolve(" vault.local/ ", "6100", "ui/explorer").toString())
        assertEquals("http://vault.local:6100/upload", BackendUrl.resolve("vault.local", "6100", "upload").toString())
    }

    @Test fun preservesExplicitPortsIncludingStandardHttpsPort() {
        assertEquals("https://vault.local/ui/explorer", BackendUrl.resolve("https://vault.local:443", "6100", "ui/explorer").toString())
        assertEquals("https://vault.local:8443/ui/explorer", BackendUrl.resolve("https://vault.local:8443/", "6100", "ui/explorer").toString())
    }

    @Test fun supportsIpv6AndClearsQueryAndFragment() {
        assertEquals("http://[::1]:6100/ui/explorer", BackendUrl.resolve("http://[::1]/?q=ignored#section", "6100", "ui/explorer").toString())
    }

    @Test fun rejectsMissingAddressUnsupportedSchemeAndInvalidPort() {
        for ((address, port) in listOf("" to "6100", "file:///data" to "6100", "vault.local" to "0", "vault.local" to "65536")) {
            assertThrows(IllegalArgumentException::class.java) { BackendUrl.resolve(address, port, "ui/explorer") }
        }
    }
}
