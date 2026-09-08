package com.paganini.voxvault.service

import com.paganini.voxvault.dataClass.Chunk
import com.paganini.voxvault.dataClass.AssignPersonRequest
import com.paganini.voxvault.dataClass.ConversationDetail
import com.paganini.voxvault.dataClass.ConversationSearchMode
import com.paganini.voxvault.dataClass.ConversationSummary
import com.paganini.voxvault.dataClass.CreatePersonRequest
import com.paganini.voxvault.dataClass.ExplorerPage
import com.paganini.voxvault.dataClass.PersonList
import com.paganini.voxvault.dataClass.SpeakerAssignment
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.Call
import okhttp3.Callback
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import java.io.IOException
import java.util.concurrent.TimeUnit
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

internal class HttpFailure(val status: Int) : IOException("Server returned HTTP $status") {
    val retryable: Boolean get() = status == 408 || status == 429 || status in 500..599
}

@Serializable
internal data class RemoteUploadStatus(
    val complete: Boolean = false,
    val totalChunks: Int? = null,
    val receivedChunks: Set<Int> = emptySet(),
)

internal interface ChunkTransport {
    suspend fun sendChunk(uploadUrl: String, chunk: Chunk): Boolean?
    suspend fun getStatus(uploadUrl: String, timestamp: Long): RemoteUploadStatus?
}

internal class HttpCommunicationService(private val client: OkHttpClient = sharedClient) : ChunkTransport {
    companion object {
        // Connection pooling is shared by every recording and retry in this process.
        private val sharedClient = OkHttpClient.Builder()
            .connectTimeout(60, TimeUnit.SECONDS)
            .readTimeout(60, TimeUnit.SECONDS)
            .writeTimeout(60, TimeUnit.SECONDS)
            .callTimeout(5, TimeUnit.MINUTES)
            .retryOnConnectionFailure(false)
            .followRedirects(false)
            .followSslRedirects(false)
            .build()
        private val json = Json { ignoreUnknownKeys = true }
    }

    override suspend fun sendChunk(uploadUrl: String, chunk: Chunk): Boolean? {
        val response = request(Request.Builder().url(uploadUrl).post(StreamingChunkBody(chunk)).build(), 4096)
        return json.parseToJsonElement(response).jsonObject["recordingComplete"]?.jsonPrimitive?.booleanOrNull
    }

    override suspend fun getStatus(uploadUrl: String, timestamp: Long): RemoteUploadStatus? {
        val base = uploadUrl.toHttpUrl()
        val url = base.newBuilder().removePathSegment(base.pathSize - 1)
            .addPathSegments("api/uploads/$timestamp").build()
        return try {
            json.decodeFromString<RemoteUploadStatus>(request(Request.Builder().url(url).build(), 256 * 1024))
        } catch (error: HttpFailure) {
            // Older servers still support resumption from the local acknowledgment journal.
            if (error.status == 404) null else throw error
        }
    }

    private fun explorerUrl(uploadUrl: String, path: String): okhttp3.HttpUrl.Builder {
        val base = uploadUrl.toHttpUrl()
        return base.newBuilder().removePathSegment(base.pathSize - 1)
            .query(null).fragment(null).addPathSegments("api/explorer/$path")
    }

    suspend fun getConversations(
        uploadUrl: String, query: String = "", mode: ConversationSearchMode = ConversationSearchMode.SEMANTIC,
        assignment: String = "all", limit: Int = 25, offset: Int = 0, minSimilarity: Double = 0.75,
    ): ExplorerPage<ConversationSummary> {
        val url = explorerUrl(uploadUrl, "conversations")
            .addQueryParameter("q", query).addQueryParameter("mode", mode.queryValue)
            .addQueryParameter("assignment", assignment).addQueryParameter("limit", limit.toString())
            .addQueryParameter("offset", offset.toString()).addQueryParameter("min_similarity", minSimilarity.toString()).build()
        return json.decodeFromString(request(Request.Builder().url(url).build(), 4 * 1024 * 1024))
    }

    suspend fun getConversation(
        uploadUrl: String, recordingId: Long, query: String = "",
        mode: ConversationSearchMode = ConversationSearchMode.SEMANTIC,
        assignment: String = "all", minSimilarity: Double = 0.75,
    ): ConversationDetail {
        val url = explorerUrl(uploadUrl, "conversations/$recordingId")
            .addQueryParameter("q", query).addQueryParameter("mode", mode.queryValue)
            .addQueryParameter("assignment", assignment).addQueryParameter("min_similarity", minSimilarity.toString()).build()
        return json.decodeFromString(request(Request.Builder().url(url).build(), 32 * 1024 * 1024))
    }

    suspend fun getSpeakerSuggestions(uploadUrl: String, chunkId: Long): PersonList {
        val url = explorerUrl(uploadUrl, "persons").addQueryParameter("chunk_id", chunkId.toString()).build()
        return json.decodeFromString(request(Request.Builder().url(url).build(), 4 * 1024 * 1024))
    }

    suspend fun assignSpeaker(uploadUrl: String, chunkId: Long, personId: Long?): SpeakerAssignment {
        val body = json.encodeToString(AssignPersonRequest(personId)).toRequestBody("application/json".toMediaType())
        val url = explorerUrl(uploadUrl, "chunks/$chunkId/person").build()
        return json.decodeFromString(request(Request.Builder().url(url).put(body).build(), 4 * 1024 * 1024))
    }

    suspend fun createSpeaker(uploadUrl: String, chunkId: Long, name: String): SpeakerAssignment {
        val body = json.encodeToString(CreatePersonRequest(name, chunkId)).toRequestBody("application/json".toMediaType())
        return json.decodeFromString(request(Request.Builder().url(explorerUrl(uploadUrl, "persons").build())
            .post(body).build(), 4 * 1024 * 1024))
    }

    private suspend fun request(request: Request, responseLimit: Long): String {
        val call = client.newCall(request)
        val finished = CompletableDeferred<Unit>()
        try {
            return suspendCancellableCoroutine { continuation ->
                continuation.invokeOnCancellation { call.cancel() }
                call.enqueue(object : Callback {
                    override fun onFailure(call: Call, e: IOException) {
                        finished.complete(Unit)
                        continuation.resumeWithException(e)
                    }

                    override fun onResponse(call: Call, response: Response) {
                        try {
                            val result = response.use {
                                if (!it.isSuccessful) throw HttpFailure(it.code)
                                val source = it.body.source()
                                if (source.request(responseLimit + 1)) {
                                    throw IOException("Server response exceeds $responseLimit bytes")
                                }
                                source.readUtf8()
                            }
                            continuation.resume(result)
                        } catch (error: Exception) {
                            continuation.resumeWithException(error)
                        } finally {
                            finished.complete(Unit)
                        }
                    }
                })
            }
        } finally {
            // Cancellation must finish the old call before the queue can start another one.
            withContext(NonCancellable) { finished.await() }
        }
    }
}
