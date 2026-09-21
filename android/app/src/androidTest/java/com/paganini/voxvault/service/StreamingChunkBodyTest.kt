package com.paganini.voxvault.service

import android.util.Base64
import android.util.Log
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.paganini.voxvault.dataClass.Chunk
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okio.Buffer
import okio.Sink
import okio.Timeout
import okio.buffer
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import org.junit.runner.RunWith
import java.io.RandomAccessFile

@RunWith(AndroidJUnit4::class)
class StreamingChunkBodyTest {
    @get:Rule val directory = TemporaryFolder()

    @Test fun exactWireBytesAndLengthAcrossBase64PaddingAndBufferBoundaries() {
        for (size in listOf(1, 2, 3, 16383, 16384, 16385, 32769)) {
            val data = ByteArray(size) { (it % 251).toByte() }
            val file = directory.newFile().apply { writeBytes(data) }
            val key = "escaped\"key\\value\n"
            val body = StreamingChunkBody(Chunk(123, 91, 16, key, file))
            val sink = Buffer()
            body.writeTo(sink)
            assertEquals(body.contentLength(), sink.size)
            val json = Json.parseToJsonElement(sink.readUtf8()).jsonObject
            assertEquals(key, json.getValue("encryptedSerializedSymmetricKey").jsonPrimitive.content)
            assertArrayEquals(data, Base64.decode(json.getValue("data").jsonPrimitive.content, Base64.NO_WRAP))
            assertEquals("16", json.getValue("chunkIndex").jsonPrimitive.content)
        }
    }

    @Test fun ninetyOneChunksAndOneGigabyteChunkKeepSinkBufferBounded() {
        val file = directory.newFile()
        RandomAccessFile(file, "rw").use { it.setLength(1280000) }
        val runtime = Runtime.getRuntime()
        System.gc()
        val before = runtime.totalMemory() - runtime.freeMemory()
        repeat(3) {
            repeat(91) { index -> streamAndCheck(Chunk(123, 91, index, "wrapped-key", file)) }
            System.gc()
            val retained = runtime.totalMemory() - runtime.freeMemory() - before
            Log.i("UploadMemoryTest", "After ${(it + 1) * 91} chunks: retained delta=$retained bytes")
            assertTrue("Audio was retained across uploads: $retained bytes", retained < 16 * 1024 * 1024)
        }
        // Larger than this device's Java heap. A whole-file read would fail here.
        RandomAccessFile(file, "rw").use { it.setLength(1024L * 1024 * 1024) }
        streamAndCheck(Chunk(123, 1, 0, "wrapped-key", file))
    }

    private fun streamAndCheck(chunk: Chunk) {
        val body = StreamingChunkBody(chunk)
        var transmitted = 0L
        var peakBuffer = 0L
        val sink = object : Sink {
            override fun write(source: Buffer, byteCount: Long) {
                peakBuffer = maxOf(peakBuffer, source.size)
                transmitted += byteCount
                source.skip(byteCount)
            }
            override fun flush() = Unit
            override fun close() = Unit
            override fun timeout() = Timeout.NONE
        }.buffer()
        sink.use { body.writeTo(it) }
        assertEquals(body.contentLength(), transmitted)
        assertTrue("Sink buffered $peakBuffer bytes", peakBuffer <= 64 * 1024)
    }
}
