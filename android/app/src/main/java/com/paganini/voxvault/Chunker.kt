package com.paganini.voxvault

import com.paganini.voxvault.dataClass.Chunk
import com.paganini.voxvault.dataClass.Recording
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder

class Chunker(
    private val recording: Recording,
    filesDir: File
) {
    private val recordingDir = File(filesDir, "recordings/${recording.name}")
    private val chunkFiles = recordingDir.listFiles { f -> 
        f.name.startsWith("chunk") && f.name.endsWith(".pcm") 
    }?.sortedBy { it.name } ?: emptyList()

    val totalChunks: Int = chunkFiles.size
    val recordingDirPath: String = recordingDir.absolutePath
    val chunkFileNames: List<String> = chunkFiles.map { it.name }
    private var currentChunkIndex: Int = 0

    fun hasNext(): Boolean = currentChunkIndex < totalChunks

    fun getNextChunk(): Chunk? {
        if (!hasNext()) return null

        val file = chunkFiles[currentChunkIndex]
        val bytes = file.readBytes()
        
        // Correct initialization: Create a ShortArray of the appropriate size
        val shortData = ShortArray(bytes.size / 2)
        
        // Use ByteBuffer to convert raw bytes (Little Endian) to shorts
        ByteBuffer.wrap(bytes)
            .order(ByteOrder.LITTLE_ENDIAN)
            .asShortBuffer()
            .get(shortData)

        return Chunk(
            timestamp = recording.timestamp,
            totalChunks = totalChunks,
            chunkIndex = currentChunkIndex++,
            encryptedSerializedSymmetricKey= recording.encryptedSerializedSymmetricKey,
            data = shortData
        )
    }
}
