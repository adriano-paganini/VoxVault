package com.paganini.voxvault.service

import com.paganini.voxvault.AppConfig
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody


class HttpCommunicationService() {

    var client: OkHttpClient = OkHttpClient()

    fun sendPing():String{
        val json = """{"name": "ping"}"""
        val mediaType = "application/json; charset=utf-8".toMediaType()
        val body = json.toRequestBody(mediaType)

        var baseUrl = AppConfig.Web.BACKEND_ADDRESS
        if (!baseUrl.startsWith("http")) {
            baseUrl = "http://$baseUrl"
        }

        val url = if (baseUrl.indexOf(":", 7) != -1) {
            "$baseUrl/ping"
        } else {
            "$baseUrl:8080/ping"
        }

        val request: Request = Request.Builder()
            .url(url)
            .post(body)
            .build()

        return try {
            client.newCall(request).execute().use { response ->
                if (!response.isSuccessful) return "Error: ${response.code}"
                response.body?.string() ?: "Empty body"
            }
        } catch (e: Exception) {
            "Failure: ${e.message}"
        }
    }
}