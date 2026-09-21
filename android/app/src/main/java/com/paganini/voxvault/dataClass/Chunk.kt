package com.paganini.voxvault.dataClass

import java.io.File

data class Chunk(
    val timestamp: Long,
    val totalChunks: Int,
    val chunkIndex: Int,
    val encryptedSerializedSymmetricKey: String,
    val file: File,
)
