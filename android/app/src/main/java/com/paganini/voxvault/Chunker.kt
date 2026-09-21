package com.paganini.voxvault

import com.paganini.voxvault.dataClass.Chunk
import com.paganini.voxvault.dataClass.Recording
import java.io.File

/** Enumerates file metadata only. Encrypted audio is opened only by RequestBody.writeTo. */
class Chunker(private val recording: Recording, filesDir: File) {
    private val recordingDir = File(filesDir, "recordings/${recording.name}")
    private val chunkFiles = recordingDir.listFiles { file ->
        file.isFile && file.name.matches(Regex("chunk_\\d+\\.pcm"))
    }?.sortedBy { it.name.removePrefix("chunk_").removeSuffix(".pcm").toLong() }
        ?: emptyList()

    val totalChunks: Int = chunkFiles.size

    fun getChunk(index: Int): Chunk = Chunk(
        timestamp = recording.timestamp,
        totalChunks = totalChunks,
        chunkIndex = index,
        encryptedSerializedSymmetricKey = recording.encryptedSerializedSymmetricKey,
        file = chunkFiles[index],
    )
}
