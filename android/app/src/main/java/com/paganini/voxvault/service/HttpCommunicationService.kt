package com.paganini.voxvault.service

import android.content.Context
import android.util.Log
import com.paganini.voxvault.AppConfig
import com.paganini.voxvault.BackendUrl
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

        if (totalChunks == 0) {
            Log.e(
                "HttpCommunicationService",
                "No chunks found for recording: ${recording.name} in ${chunker.recordingDirPath}. Files: ${chunker.chunkFileNames}"
            )
            return "Error: No chunks found for ${recording.name}"
        }

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

        return try {
            val request = Request.Builder()
                .url(BackendUrl.resolve(AppConfig.Web.BACKEND_ADDRESS, AppConfig.Web.BACKEND_PORT, "upload"))
                .post(body)
                .build()
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
}
