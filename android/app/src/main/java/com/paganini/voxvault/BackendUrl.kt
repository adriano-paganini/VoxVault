package com.paganini.voxvault

import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrl
import java.net.URI

object BackendUrl {
    fun resolve(address: String, port: String, endpoint: String): HttpUrl {
        val trimmed = address.trim().trimEnd('/')
        require(trimmed.isNotEmpty()) { "Set the server address in Settings." }
        val absolute = if ("://" in trimmed) trimmed else "http://$trimmed"
        val uri = URI(absolute)
        val base = absolute.toHttpUrl()
        require(base.username.isEmpty() && base.password.isEmpty()) { "Use a server address without credentials." }
        val builder = base.newBuilder().query(null).fragment(null)
        if (uri.port == -1) {
            val configuredPort = port.toIntOrNull()
            require(configuredPort != null && configuredPort in 1..65535) { "Enter a port between 1 and 65535." }
            builder.port(configuredPort)
        }
        return builder.addPathSegments(endpoint.trim('/')).build()
    }
}
