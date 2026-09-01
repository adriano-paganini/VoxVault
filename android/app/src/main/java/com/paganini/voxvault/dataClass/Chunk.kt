package com.paganini.voxvault.dataClass

import kotlinx.serialization.InternalSerializationApi
import kotlinx.serialization.Serializable

@OptIn(InternalSerializationApi::class)
@Serializable
data class Chunk(
    val timestamp: Long,
    val totalChunks: Int,
    val chunkIndex: Int,
    val data: ShortArray
) {

    override fun equals(other: Any?): Boolean {
        if (this === other) return true
        if (javaClass != other?.javaClass) return false

        other as Chunk

        if (timestamp != other.timestamp) return false
        if (totalChunks != other.totalChunks) return false
        if (chunkIndex != other.chunkIndex) return false
        if (!data.contentEquals(other.data)) return false

        return true
    }

    override fun hashCode(): Int {
        var result = timestamp.hashCode()
        result = 31 * result + totalChunks
        result = 31 * result + chunkIndex
        result = 31 * result + data.contentHashCode()
        return result
    }
}
