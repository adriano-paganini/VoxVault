package com.paganini.voxvault.service

import android.content.Context
import android.util.Log
import com.paganini.voxvault.AppConfig
import com.paganini.voxvault.Chunker
import com.paganini.voxvault.dataClass.Chunk
import com.paganini.voxvault.dataClass.Recording
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody


class HttpCommunicationService(private val context: Context) {

    var client: OkHttpClient = OkHttpClient()

    fun sendRecording(recording: Recording, onProgress: (Int, Int) -> Unit = { _, _ -> }): String {
        val chunker: Chunker = recording.getChunker(context.filesDir)
        var lastResult = ""
        val totalChunks = chunker.totalChunks

        while (chunker.hasNext()) {
            val chunk = chunker.getNextChunk() ?: break
            lastResult = sendChunk(chunk)
            if (lastResult.startsWith("Failure") || lastResult.startsWith("Error")) {
                return lastResult
            }
            onProgress(chunk.chunkIndex + 1, totalChunks)
        }
        return lastResult.ifEmpty { "No chunks sent" }
    }

    private fun sendChunk(chunk: Chunk): String {
        val json = Json.encodeToString(chunk)
        val mediaType = "application/json; charset=utf-8".toMediaType()
        val body = json.toRequestBody(mediaType)

        val url = getUrl("upload")

        val request: Request = Request.Builder()
            .url(url)
            .post(body)
            .build()

        Log.d("HttpCommunicationService", "Sending chunk ${chunk.chunkIndex + 1}/${chunk.totalChunks} to: $url")

        return try {
            client.newCall(request).execute().use { response ->
                if (!response.isSuccessful) {
                    Log.e("HttpCommunicationService", "Chunk upload failed with code: ${response.code}")
                    return "Error: ${response.code}"
                }
                val result = response.body.string()
                Log.d("HttpCommunicationService", "Chunk upload successful: $result")
                result
            }
        } catch (e: Exception) {
            Log.e("HttpCommunicationService", "Chunk upload failed", e)
            "Failure: ${e.message}"
        }
    }
    private fun getUrl(endpoint: String = "ping"): String {
        var baseUrl = AppConfig.Web.BACKEND_ADDRESS
        if (!baseUrl.startsWith("http")) {
            baseUrl = "http://$baseUrl"
        }

        // Strip trailing slash if present
        if (baseUrl.endsWith("/")) {
            baseUrl = baseUrl.substring(0, baseUrl.length - 1)
        }

        val url = if (baseUrl.indexOf(":", 7) != -1) {
            "$baseUrl/$endpoint"
        } else {
            "$baseUrl:8000/$endpoint"
        }
        return url
    }
}
