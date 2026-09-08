package com.paganini.voxvault.upload

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.core.DataStoreFactory
import androidx.datastore.core.Serializer
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.withContext
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.serialization.Serializable
import kotlinx.serialization.ExperimentalSerializationApi
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.decodeFromStream
import kotlinx.serialization.json.encodeToStream
import java.io.File
import java.io.InputStream
import java.io.OutputStream

@Serializable
enum class UploadPhase { QUEUED, PREPARING, UPLOADING, RETRYING, COMPLETED, FAILED, CANCELLED }

@Serializable
data class UploadFile(val name: String, val size: Long)

@Serializable
data class UploadEntry(
    val name: String,
    val uploadUrl: String,
    val phase: UploadPhase = UploadPhase.QUEUED,
    val timestamp: Long = 0,
    val files: List<UploadFile> = emptyList(),
    val uploaded: Set<Int> = emptySet(),
    val currentChunk: Int = 0,
    val attempts: Int = 0,
    val statusAttempts: Int = 0,
    val error: String? = null,
) {
    val pending: Boolean get() = phase in setOf(
        UploadPhase.QUEUED, UploadPhase.PREPARING, UploadPhase.UPLOADING, UploadPhase.RETRYING
    )
    val progress: Float get() = if (files.isEmpty()) 0f else uploaded.size.toFloat() / files.size
}

@Serializable
data class UploadJournal(val entries: List<UploadEntry> = emptyList()) {
    val idle: Boolean get() = entries.none { it.pending }
}

data class UploadCleanupResult(val deleted: Int = 0, val failed: Int = 0, val error: String? = null)

@OptIn(ExperimentalSerializationApi::class)
internal object UploadJournalSerializer : Serializer<UploadJournal> {
    private val json = Json { ignoreUnknownKeys = true }
    override val defaultValue = UploadJournal()
    override suspend fun readFrom(input: InputStream): UploadJournal =
        json.decodeFromStream(input)
    override suspend fun writeTo(t: UploadJournal, output: OutputStream) {
        json.encodeToStream(t, output)
    }
}

/** Atomic, durable metadata only. No audio, Base64 payloads, or request objects belong here. */
class UploadQueue internal constructor(private val store: DataStore<UploadJournal>) {
    val state: Flow<UploadJournal> = store.data

    companion object {
        @Volatile private var instance: UploadQueue? = null

        fun get(context: Context): UploadQueue = instance ?: synchronized(this) {
            instance ?: UploadQueue(DataStoreFactory.create(UploadJournalSerializer) {
                File(context.applicationContext.noBackupFilesDir, "upload-queue.json")
            }).also { instance = it }
        }
    }

    suspend fun enqueue(names: List<String>, uploadUrl: String) {
        require(names.all { it.isNotBlank() && File(it).name == it && it != "." && it != ".." })
        store.updateData { journal ->
            val entries = journal.entries.toMutableList()
            for (name in names.distinct()) {
                val old = entries.find { it.name == name }
                if (old?.pending == true || (old?.phase == UploadPhase.COMPLETED && old.uploadUrl == uploadUrl)) continue
                entries.removeAll { it.name == name }
                entries.add(if (old != null && old.uploadUrl == uploadUrl) {
                    old.copy(phase = UploadPhase.QUEUED, attempts = 0, statusAttempts = 0, error = null)
                } else UploadEntry(name, uploadUrl))
            }
            UploadJournal(entries)
        }
    }

    internal suspend fun next(): UploadEntry? = state.first().entries.firstOrNull { it.pending }

    internal suspend fun update(name: String, transform: (UploadEntry) -> UploadEntry): UploadEntry {
        val updated = store.updateData { journal ->
            journal.copy(entries = journal.entries.map { if (it.name == name) transform(it) else it })
        }
        return updated.entries.first { it.name == name }
    }

    internal suspend fun cancelPending() {
        store.updateData { journal ->
            journal.copy(entries = journal.entries.map {
                if (it.pending) it.copy(phase = UploadPhase.CANCELLED, error = null) else it
            })
        }
    }

    suspend fun forget(name: String) {
        store.updateData { journal ->
            journal.copy(entries = journal.entries.filterNot { it.name == name && !it.pending })
        }
    }

    suspend fun deleteUploaded(recordingsDir: File, names: Set<String>): UploadCleanupResult =
        withContext(NonCancellable + Dispatchers.IO) {
            var deleted = 0
            var failed = 0
            // Serialize eligibility checks, deletion, and journal updates against new enqueues.
            store.updateData { journal ->
                journal.copy(entries = journal.entries.filter { entry ->
                    if (entry.name !in names || entry.phase != UploadPhase.COMPLETED) return@filter true
                    val folder = File(recordingsDir, entry.name)
                    if (!folder.exists()) return@filter false
                    if (folder.deleteRecursively()) {
                        deleted++
                        false
                    } else {
                        failed++
                        true
                    }
                })
            }
            UploadCleanupResult(deleted, failed)
        }
}
