package com.paganini.voxvault.service

import android.util.Base64
import android.util.Base64OutputStream
import com.paganini.voxvault.dataClass.Chunk
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody
import okio.BufferedSink
import java.io.IOException

/** Keeps the existing JSON wire format without materializing its audio string. */
internal class StreamingChunkBody(private val chunk: Chunk) : RequestBody() {
    private val fileSize = chunk.file.length()
    private val prefix = ("{\"timestamp\":${chunk.timestamp},\"totalChunks\":${chunk.totalChunks}," +
        "\"chunkIndex\":${chunk.chunkIndex},\"encryptedSerializedSymmetricKey\":" +
        Json.encodeToString(chunk.encryptedSerializedSymmetricKey) + ",\"data\":\"")
        .toByteArray(Charsets.UTF_8)

    override fun contentType() = "application/json; charset=utf-8".toMediaType()
    override fun contentLength(): Long = prefix.size + 4 * ((fileSize + 2) / 3) + 2
    // Only the queue may retry. Disable OkHttp's implicit HTTP follow-up replays.
    override fun isOneShot(): Boolean = true

    override fun writeTo(sink: BufferedSink) {
        if (!chunk.file.isFile || chunk.file.length() != fileSize) {
            throw IOException("Chunk file changed before upload")
        }
        sink.write(prefix)
        chunk.file.inputStream().use { input ->
            // NO_CLOSE finalizes Base64 padding without closing OkHttp's socket sink.
            Base64OutputStream(sink.outputStream(), Base64.NO_WRAP or Base64.NO_CLOSE).use { encoded ->
                val buffer = ByteArray(16 * 1024)
                var remaining = fileSize
                while (remaining > 0) {
                    val count = input.read(buffer, 0, minOf(buffer.size.toLong(), remaining).toInt())
                    if (count < 0) throw IOException("Chunk file was truncated during upload")
                    encoded.write(buffer, 0, count)
                    remaining -= count
                }
                if (input.read() != -1) throw IOException("Chunk file grew during upload")
            }
        }
        sink.writeUtf8("\"}")
    }
}
