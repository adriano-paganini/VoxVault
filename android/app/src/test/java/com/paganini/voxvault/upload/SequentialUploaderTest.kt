package com.paganini.voxvault.upload

import androidx.datastore.core.DataStoreFactory
import com.paganini.voxvault.dataClass.Chunk
import com.paganini.voxvault.dataClass.Recording
import com.paganini.voxvault.service.ChunkTransport
import com.paganini.voxvault.service.HttpFailure
import com.paganini.voxvault.service.RemoteUploadStatus
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import java.io.IOException

class SequentialUploaderTest {
    @get:Rule val directory = TemporaryFolder()
    private lateinit var storeJob: Job
    private lateinit var queue: UploadQueue
    private val calls = mutableListOf<Pair<String, Int>>()
    private val received = mutableMapOf<Long, MutableSet<Int>>()
    private var active = 0
    private var peakActive = 0
    private var failIndex: Int? = null
    private var failure: IOException = IOException("offline")
    private var legacy = false
    private val delays = mutableListOf<Long>()
    private val network = object : ChunkTransport {
        override suspend fun getStatus(uploadUrl: String, timestamp: Long): RemoteUploadStatus? =
            if (legacy) null else RemoteUploadStatus(receivedChunks = received[timestamp]?.toSet() ?: emptySet())

        override suspend fun sendChunk(uploadUrl: String, chunk: Chunk): Boolean? {
            active++
            peakActive = maxOf(active, peakActive)
            try {
                calls.add(chunk.file.parentFile!!.name to chunk.chunkIndex)
                delay(1)
                if (chunk.file.parentFile!!.name == "A" && chunk.chunkIndex == failIndex) throw failure
                val indexes = received.getOrPut(chunk.timestamp) { mutableSetOf() }
                indexes.add(chunk.chunkIndex)
                return if (legacy) null else indexes.size == chunk.totalChunks
            } finally {
                active--
            }
        }
    }

    @Before fun setup() { openStore() }
    @After fun cleanup() = runBlocking { storeJob.cancelAndJoin() }

    private fun openStore() {
        storeJob = SupervisorJob()
        queue = UploadQueue(DataStoreFactory.create(UploadJournalSerializer,
            scope = CoroutineScope(storeJob + Dispatchers.IO)) { File(directory.root, "queue.json") })
    }

    private fun recording(name: String, count: Int, timestamp: Long = 123) {
        val folder = File(directory.root, "recordings/$name").apply { mkdirs() }
        File(folder, "metadata.json").writeText(Json.encodeToString(Recording(name, "encrypted-key", 3600.0, timestamp)))
        repeat(count) { File(folder, "chunk_${(it + 1).toString().padStart(3, '0')}.pcm").writeBytes(byteArrayOf(1)) }
    }

    private suspend fun runUploads() = SequentialUploader(queue, directory.root, network) { delays.add(it) }.run()
    private suspend fun entry() = queue.state.first().entries.first()

    @Test fun ninetyOneChunksAndMultipleRecordingsAreSequentialAndDeduplicated() = runBlocking {
        recording("A", 91)
        recording("B", 5, 456)
        queue.enqueue(listOf("A", "A", "B"), "http://localhost/upload")
        queue.enqueue(listOf("A", "B"), "http://localhost/upload")
        runUploads()
        assertEquals((0 until 91).map { "A" to it } + (0 until 5).map { "B" to it }, calls)
        assertEquals(1, peakActive)
        assertTrue(queue.state.first().entries.all { it.phase == UploadPhase.COMPLETED })
        queue.enqueue(listOf("A"), "http://localhost/upload")
        runUploads()
        assertEquals(96, calls.size)
    }

    @Test fun failureAtChunk73ResumesAfterReopeningPersistentQueue() = runBlocking {
        recording("A", 91)
        failIndex = 72
        queue.enqueue(listOf("A"), "http://localhost/upload")
        runUploads()
        assertEquals(UploadPhase.FAILED, entry().phase)
        assertEquals((0 until 72).toSet(), entry().uploaded)
        assertEquals(listOf(72, 72, 72), calls.takeLast(3).map { it.second })
        assertEquals(listOf(2000L, 4000L), delays)
        storeJob.cancelAndJoin()
        openStore()
        failIndex = null
        calls.clear()
        queue.enqueue(listOf("A"), "http://localhost/upload")
        runUploads()
        assertEquals((72 until 91).toList(), calls.map { it.second })
        assertEquals(UploadPhase.COMPLETED, entry().phase)
    }

    @Test fun permanent4xxIsNotRetriedAndNextRecordingCanProceed() = runBlocking {
        recording("A", 5)
        recording("B", 2, 456)
        failIndex = 0
        failure = HttpFailure(400)
        queue.enqueue(listOf("A", "B"), "http://localhost/upload")
        runUploads()
        assertEquals(listOf("A" to 0, "B" to 0, "B" to 1), calls)
        assertTrue(delays.isEmpty())
        assertEquals(UploadPhase.FAILED, entry().phase)
        assertEquals(UploadPhase.COMPLETED, queue.state.first().entries.last().phase)
    }

    @Test fun serverAcknowledgmentWinsAfterLostResponse() = runBlocking {
        recording("A", 5)
        received[123] = mutableSetOf(0, 1, 2)
        queue.enqueue(listOf("A"), "http://localhost/upload")
        runUploads()
        assertEquals(listOf(3, 4), calls.map { it.second })
    }

    @Test fun serverRestartDiscardsLocalAcknowledgments() = runBlocking {
        recording("A", 5)
        failIndex = 3
        queue.enqueue(listOf("A"), "http://localhost/upload")
        runUploads()
        received.clear()
        failIndex = null
        calls.clear()
        queue.enqueue(listOf("A"), "http://localhost/upload")
        runUploads()
        assertEquals((0 until 5).toList(), calls.map { it.second })
    }

    @Test fun legacyServerUsesLocalAcknowledgments() = runBlocking {
        recording("A", 5)
        legacy = true
        failIndex = 3
        queue.enqueue(listOf("A"), "http://localhost/upload")
        runUploads()
        failIndex = null
        calls.clear()
        queue.enqueue(listOf("A"), "http://localhost/upload")
        runUploads()
        assertEquals(listOf(3, 4), calls.map { it.second })
    }

    @Test fun cancellationDoesNotAcknowledgeAnUnfinishedChunkAndRecreationResumesIt() = runBlocking {
        recording("A", 5)
        queue.enqueue(listOf("A"), "http://localhost/upload")
        val started = CompletableDeferred<Unit>()
        val blocking = object : ChunkTransport by network {
            override suspend fun sendChunk(uploadUrl: String, chunk: Chunk): Boolean? {
                started.complete(Unit)
                awaitCancellation()
            }
        }
        val worker = launch { SequentialUploader(queue, directory.root, blocking).run() }
        started.await()
        worker.cancelAndJoin()
        assertTrue(entry().uploaded.isEmpty())
        assertEquals(1, entry().attempts)
        storeJob.cancelAndJoin()
        openStore()
        runUploads()
        assertEquals((0 until 5).toList(), calls.map { it.second })
    }

    @Test fun changedFilesAreRejectedBeforeAnyNetworkUpload() = runBlocking {
        recording("A", 5)
        failIndex = 3
        queue.enqueue(listOf("A"), "http://localhost/upload")
        runUploads()
        File(directory.root, "recordings/A/chunk_001.pcm").appendBytes(byteArrayOf(2))
        failIndex = null
        calls.clear()
        queue.enqueue(listOf("A"), "http://localhost/upload")
        runUploads()
        assertTrue(calls.isEmpty())
        assertEquals(UploadPhase.FAILED, entry().phase)
    }

    @Test fun lostAcknowledgedTailDoesNotProduceFalseCompletion() = runBlocking {
        recording("A", 5)
        received[123] = mutableSetOf(3, 4)
        queue.enqueue(listOf("A"), "http://localhost/upload")
        val restartingServer = object : ChunkTransport by network {
            override suspend fun sendChunk(uploadUrl: String, chunk: Chunk): Boolean? {
                if (chunk.chunkIndex == 1) received.clear()
                return network.sendChunk(uploadUrl, chunk)
            }
        }
        SequentialUploader(queue, directory.root, restartingServer).run()
        assertEquals(UploadPhase.FAILED, entry().phase)
        assertTrue(entry().error!!.contains("missing earlier chunks"))
        calls.clear()
        queue.enqueue(listOf("A"), "http://localhost/upload")
        runUploads()
        assertEquals(listOf(0, 3, 4), calls.map { it.second })
        assertEquals(UploadPhase.COMPLETED, entry().phase)
    }

    @Test fun persistedAttemptBudgetIsNotResetByProcessRecreation() = runBlocking {
        recording("A", 5)
        queue.enqueue(listOf("A"), "http://localhost/upload")
        queue.update("A") { it.copy(phase = UploadPhase.UPLOADING, currentChunk = 1, attempts = 3) }
        storeJob.cancelAndJoin()
        openStore()
        runUploads()
        assertTrue(calls.isEmpty())
        assertEquals(UploadPhase.FAILED, entry().phase)
    }

    @Test fun cleanupDeletesCompletedRecordingsAndPreservesAllOtherStates() = runBlocking {
        val phases = mapOf("A" to UploadPhase.COMPLETED, "B" to UploadPhase.COMPLETED,
            "C" to UploadPhase.FAILED, "D" to UploadPhase.CANCELLED, "E" to UploadPhase.UPLOADING)
        for ((name, phase) in phases) {
            recording(name, 3)
            queue.enqueue(listOf(name), "http://localhost/upload")
            queue.update(name) { it.copy(phase = phase) }
        }
        recording("not-queued", 2)
        val result = queue.deleteUploaded(File(directory.root, "recordings"), phases.keys + "not-queued")
        assertEquals(UploadCleanupResult(deleted = 2), result)
        for (name in listOf("A", "B")) assertFalse(File(directory.root, "recordings/$name").exists())
        for (name in listOf("C", "D", "E", "not-queued")) assertTrue(File(directory.root, "recordings/$name/chunk_001.pcm").exists())
        assertEquals(setOf("C", "D", "E"), queue.state.first().entries.map { it.name }.toSet())
    }

    @Test fun cleanupRechecksRecordingsRequeuedSinceConfirmationOpened() = runBlocking {
        recording("A", 3)
        queue.enqueue(listOf("A"), "http://localhost/upload")
        queue.update("A") { it.copy(phase = UploadPhase.COMPLETED) }
        queue.enqueue(listOf("A"), "http://another-server/upload")
        val result = queue.deleteUploaded(File(directory.root, "recordings"), setOf("A"))
        assertEquals(UploadCleanupResult(), result)
        assertTrue(File(directory.root, "recordings/A/chunk_001.pcm").exists())
        assertEquals(UploadPhase.QUEUED, entry().phase)
    }

    @Test fun cleanupOnlyDeletesTheConfirmedSnapshot() = runBlocking {
        for (name in listOf("A", "B")) {
            recording(name, 3)
            queue.enqueue(listOf(name), "http://localhost/upload")
            queue.update(name) { it.copy(phase = UploadPhase.COMPLETED) }
        }
        queue.deleteUploaded(File(directory.root, "recordings"), setOf("A"))
        assertFalse(File(directory.root, "recordings/A").exists())
        assertTrue(File(directory.root, "recordings/B/chunk_001.pcm").exists())
        assertEquals("B", entry().name)
    }
}
