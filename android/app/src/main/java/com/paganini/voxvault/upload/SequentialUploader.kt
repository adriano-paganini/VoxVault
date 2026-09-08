package com.paganini.voxvault.upload

import com.paganini.voxvault.dataClass.Recording
import com.paganini.voxvault.service.ChunkTransport
import com.paganini.voxvault.service.HttpFailure
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import java.io.File
import java.io.IOException
import kotlin.time.Duration.Companion.milliseconds

/** The service calls run once. Every suspend call completes before the next chunk is opened. */
internal class SequentialUploader(
    private val queue: UploadQueue,
    private val filesDir: File,
    private val network: ChunkTransport,
    private val retryDelay: suspend (Long) -> Unit = { delay(it.milliseconds) },
) {
    suspend fun run() {
        while (true) {
            currentCoroutineContext().ensureActive()
            val entry = queue.next() ?: return
            try {
                upload(entry)
            } catch (error: CancellationException) {
                throw error
            } catch (error: Exception) {
                queue.update(entry.name) {
                    it.copy(phase = UploadPhase.FAILED, error = error.message ?: "Upload failed")
                }
            }
        }
    }

    private suspend fun upload(initial: UploadEntry) {
        var entry = queue.update(initial.name) { it.copy(phase = UploadPhase.PREPARING, error = null) }
        val (recording, chunker) = withContext(Dispatchers.IO) {
            val file = File(filesDir, "recordings/${entry.name}/metadata.json")
            require(file.isFile && file.length() <= 64 * 1024) { "Recording metadata is missing or invalid" }
            val recording = Json.decodeFromString<Recording>(file.readText())
            require(recording.name == entry.name && recording.timestamp > 0 && recording.encryptedSerializedSymmetricKey.isNotEmpty()) {
                "Recording is not finalized"
            }
            recording to recording.getChunker(filesDir)
        }
        val files = withContext(Dispatchers.IO) {
            (0 until chunker.totalChunks).map { index ->
                chunker.getChunk(index).file.let { UploadFile(it.name, it.length()) }
            }
        }
        require(files.isNotEmpty() && files.size <= 20000 && files.all { it.size > 0 }) { "Recording has no valid chunks" }
        require(entry.files.isEmpty() || (entry.files == files && entry.timestamp == recording.timestamp)) {
            "Recording files changed since upload was queued"
        }
        entry = queue.update(entry.name) { it.copy(files = files, timestamp = recording.timestamp) }

        val remote = retry(entry.name, statusRequest = true) {
            network.getStatus(entry.uploadUrl, recording.timestamp)
        }
        if (remote != null) {
            require(remote.totalChunks == null || remote.totalChunks == files.size) { "Server chunk count differs from this recording" }
            require(remote.receivedChunks.all { it in files.indices }) { "Invalid chunk indexes from server" }
            entry = queue.update(entry.name) {
                it.copy(uploaded = if (remote.complete) files.indices.toSet() else remote.receivedChunks)
            }
        }

        var completionConfirmed = remote?.complete
        for (index in files.indices) {
            currentCoroutineContext().ensureActive()
            if (index in entry.uploaded) continue
            entry = queue.update(entry.name) {
                it.copy(currentChunk = index + 1, attempts = if (it.currentChunk == index + 1) it.attempts else 0)
            }
            val complete = retry(entry.name, statusRequest = false) {
                val chunk = chunker.getChunk(index)
                withContext(Dispatchers.IO) {
                    require(chunk.file.isFile && chunk.file.length() == files[index].size) {
                        "Chunk file changed before upload"
                    }
                }
                network.sendChunk(entry.uploadUrl, chunk)
            }
            completionConfirmed = complete
            entry = queue.update(entry.name) {
                it.copy(uploaded = it.uploaded + index, attempts = 0, phase = UploadPhase.UPLOADING)
            }
            if (complete == true) {
                // The server may already have the whole recording after a lost acknowledgment.
                entry = queue.update(entry.name) { it.copy(uploaded = files.indices.toSet()) }
                break
            }
        }
        // A server restart can discard earlier chunks, including a previously acknowledged tail.
        if (completionConfirmed == false) {
            throw IOException("Server is missing earlier chunks. Tap Upload to reconcile and resume.")
        }
        queue.update(entry.name) { it.copy(phase = UploadPhase.COMPLETED, error = null) }
    }

    private suspend fun <T> retry(name: String, statusRequest: Boolean, action: suspend () -> T): T {
        while (true) {
            currentCoroutineContext().ensureActive()
            val entry = queue.update(name) {
                val previous = if (statusRequest) it.statusAttempts else it.attempts
                check(previous < MAX_ATTEMPTS) { "Upload attempts exhausted. Tap Upload to resume." }
                it.copy(
                    attempts = if (statusRequest) it.attempts else previous + 1,
                    statusAttempts = if (statusRequest) previous + 1 else it.statusAttempts,
                    phase = if (statusRequest) UploadPhase.PREPARING else UploadPhase.UPLOADING,
                )
            }
            try {
                return action().also {
                    if (statusRequest) queue.update(name) { it.copy(statusAttempts = 0) }
                }
            } catch (error: IOException) {
                val attempt = if (statusRequest) entry.statusAttempts else entry.attempts
                if ((error is HttpFailure && !error.retryable) || attempt >= MAX_ATTEMPTS) throw error
                queue.update(name) { it.copy(phase = UploadPhase.RETRYING, error = error.message) }
                retryDelay(2000L shl (attempt - 1))
            }
        }
    }

    companion object { private const val MAX_ATTEMPTS = 3 }
}
