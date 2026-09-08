package com.paganini.voxvault.dataClass

import kotlinx.serialization.Serializable
import kotlinx.serialization.SerialName

@Serializable
internal enum class ChunkMatchType {
    @SerialName("keyword") KEYWORD,
    @SerialName("semantic") SEMANTIC,
}

// These are processed archive records; Recording and Chunk remain local upload models.
@Serializable
internal data class ExplorerPage<T>(
    val items: List<T>, val total: Int, val limit: Int, val offset: Int,
)

@Serializable
internal data class ConversationSummary(
    val id: Long,
    val timestamp: Long,
    val language: String? = null,
    val chunkCount: Int,
    val wordCount: Int,
    val durationMs: Long,
    val personCount: Int,
    val preview: String = "",
    val similarity: Double? = null,
    val matchScore: Double? = null,
    val matchedChunkIds: List<Long> = emptyList(),
)

@Serializable
internal data class ConversationDetail(
    val id: Long,
    val timestamp: Long,
    val language: String? = null,
    val chunkCount: Int,
    val wordCount: Int,
    val durationMs: Long,
    val personCount: Int,
    val chunks: List<TranscriptChunk>,
    val similarity: Double? = null,
    val matchScore: Double? = null,
    val matchedChunkIds: List<Long> = emptyList(),
)

@Serializable
internal data class TranscriptChunk(
    val id: Long,
    val recordingId: Long,
    val recordingTimestamp: Long,
    val language: String? = null,
    val chunkIndex: Int,
    val text: String,
    val wordCount: Int,
    val startMs: Long,
    val endMs: Long,
    val personId: Long? = null,
    val personName: String? = null,
    val hasVoiceEmbedding: Boolean = false,
    val words: List<TranscriptWord> = emptyList(),
    val similarity: Double? = null,
    val matched: Boolean = false,
    val matchType: ChunkMatchType? = null,
    val matchScore: Double? = null,
)

@Serializable
internal data class TranscriptWord(
    val word: String, val index: Int, val startMs: Long, val endMs: Long,
    val confidence: Double? = null, val speakerLabel: String? = null,
)

@Serializable
internal data class PersonProfile(
    val id: Long, val name: String? = null,
    val chunkCount: Int, val recordingCount: Int, val wordCount: Int,
    val hasVoiceEmbedding: Boolean = false, val similarity: Double? = null,
)

@Serializable
internal data class PersonList(
    val items: List<PersonProfile>, val total: Int = 0, val limit: Int = 25,
    val offset: Int = 0, val hasMore: Boolean = false,
)

@Serializable
internal data class SpeakerAssignment(val chunk: TranscriptChunk, val person: PersonProfile? = null)

@Serializable
internal data class AssignPersonRequest(val personId: Long?)

@Serializable
internal data class CreatePersonRequest(val name: String, val chunkId: Long)
