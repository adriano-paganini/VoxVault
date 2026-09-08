package com.paganini.voxvault.service

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import com.paganini.voxvault.BackendUrl
import com.paganini.voxvault.MainActivity
import com.paganini.voxvault.SettingsManager
import com.paganini.voxvault.dataStore
import com.paganini.voxvault.upload.SequentialUploader
import com.paganini.voxvault.upload.UploadEntry
import com.paganini.voxvault.upload.UploadJournal
import com.paganini.voxvault.upload.UploadPhase
import com.paganini.voxvault.upload.UploadQueue
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import java.util.concurrent.TimeUnit

class UploadService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val commands = Channel<Intent>(Channel.UNLIMITED)
    private lateinit var queue: UploadQueue
    private var consumer: Job? = null
    private var pendingCommands = 0
    private var lastStartId = 0
    private var stopping = false
    private var journal = UploadJournal()
    private var serviceError: String? = null
    private var wakeLock: PowerManager.WakeLock? = null

    companion object {
        private const val CHANNEL_ID = "recording_uploads"
        private const val NOTIFICATION_ID = 2002
        private const val RECORDINGS = "recording_names"
        private const val CANCEL = "com.paganini.voxvault.CANCEL_UPLOADS"

        // Also excludes an old service instance still unwinding a cancelled network callback.
        private val consumerLock = Mutex()

        fun enqueue(context: Context, names: List<String>) {
            ContextCompat.startForegroundService(context, Intent(context, UploadService::class.java)
                .putStringArrayListExtra(RECORDINGS, ArrayList(names)))
        }

        fun resume(context: Context) {
            ContextCompat.startForegroundService(context, Intent(context, UploadService::class.java))
        }

        fun cancel(context: Context) {
            context.startService(Intent(context, UploadService::class.java).setAction(CANCEL))
        }
    }

    override fun onCreate() {
        super.onCreate()
        queue = UploadQueue.get(this)
        val manager = getSystemService(NotificationManager::class.java)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            manager.createNotificationChannel(NotificationChannel(CHANNEL_ID, "Recording uploads", NotificationManager.IMPORTANCE_LOW))
        }
        val notification = buildNotification()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
        wakeLock = getSystemService(PowerManager::class.java)
            .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "VoxVault:Upload")
            .apply { setReferenceCounted(false); acquire(TimeUnit.HOURS.toMillis(6)) }

        scope.launch {
            try {
                queue.state.collect {
                    journal = it
                    if (!stopping) manager.notify(NOTIFICATION_ID, buildNotification())
                }
            } catch (error: CancellationException) {
                throw error
            } catch (error: Exception) {
                serviceError = error.message ?: "Cannot read upload queue"
            }
        }
        scope.launch {
            for (command in commands) {
                try {
                    if (command.action == CANCEL) {
                        consumer?.cancelAndJoin()
                        consumer = null
                        queue.cancelPending()
                        journal = queue.state.first()
                    } else {
                        command.getStringArrayListExtra(RECORDINGS)?.let { names ->
                            val settings = applicationContext.dataStore.data.first()
                            val url = BackendUrl.resolve(
                                settings[SettingsManager.BACKEND_URL] ?: SettingsManager.DEFAULT_BACKEND_URL,
                                settings[SettingsManager.BACKEND_PORT] ?: SettingsManager.DEFAULT_BACKEND_PORT,
                                "upload",
                            )
                            queue.enqueue(names, url.toString())
                        }
                        startConsumer()
                    }
                } catch (error: CancellationException) {
                    throw error
                } catch (error: Exception) {
                    serviceError = error.message ?: "Cannot queue uploads"
                    Log.e("UploadService", "Upload command failed", error)
                } finally {
                    pendingCommands--
                    finishIfIdle()
                }
            }
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        lastStartId = startId
        pendingCommands++
        commands.trySend(intent ?: Intent())
        return START_STICKY
    }

    private fun startConsumer() {
        if (consumer?.isActive == true) return
        consumer = scope.launch {
            try {
                consumerLock.withLock {
                    SequentialUploader(queue, filesDir, HttpCommunicationService()).run()
                    journal = queue.state.first()
                }
            } catch (error: CancellationException) {
                throw error
            } catch (error: Exception) {
                serviceError = error.message ?: "Cannot update upload queue"
                Log.e("UploadService", "Upload worker failed", error)
            }
        }.also { job ->
            job.invokeOnCompletion {
                scope.launch { finishIfIdle() }
            }
        }
    }

    private fun finishIfIdle() {
        if (pendingCommands != 0 || consumer?.isActive == true || stopping) return
        if (stopSelfResult(lastStartId)) {
            stopping = true
            getSystemService(NotificationManager::class.java).notify(NOTIFICATION_ID, buildNotification(finished = true))
            stopForeground(STOP_FOREGROUND_DETACH)
        }
    }

    private fun buildNotification(finished: Boolean = false): Notification {
        val entry = journal.entries.firstOrNull { it.pending } ?: journal.entries.lastOrNull()
        val content = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        val builder = NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.stat_sys_upload)
            .setContentTitle(if (finished) "Recording uploads" else "Uploading recording")
            .setContentText(serviceError ?: entry?.description() ?: "Preparing uploads")
            .setContentIntent(content)
            .setOnlyAlertOnce(true)
            .setOngoing(!finished)
            .setAutoCancel(finished)
            .setCategory(NotificationCompat.CATEGORY_PROGRESS)
            .setVisibility(NotificationCompat.VISIBILITY_PRIVATE)
        if (!finished) {
            val cancel = PendingIntent.getService(this, 1, Intent(this, UploadService::class.java).setAction(CANCEL),
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
            builder.addAction(android.R.drawable.ic_menu_close_clear_cancel, "Cancel uploads", cancel)
            builder.setProgress(entry?.files?.size ?: 0, entry?.uploaded?.size ?: 0, entry?.files.isNullOrEmpty())
        }
        return builder.build()
    }

    private fun UploadEntry.description(): String = when (phase) {
        UploadPhase.QUEUED, UploadPhase.PREPARING -> "Preparing $name"
        UploadPhase.UPLOADING -> "Chunk $currentChunk of ${files.size}"
        UploadPhase.RETRYING -> "Retrying chunk $currentChunk of ${files.size} (attempt ${attempts + 1} of 3)"
        UploadPhase.COMPLETED -> "Upload completed"
        UploadPhase.FAILED -> error ?: "Upload failed"
        UploadPhase.CANCELLED -> "Uploads cancelled"
    }

    override fun onTimeout(startId: Int, fgsType: Int) {
        // Android 15+ requires stopping promptly. The durable journal remains resumable.
        stopping = true
        scope.cancel()
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    override fun onDestroy() {
        stopping = true
        commands.close()
        scope.cancel()
        wakeLock?.let { if (it.isHeld) it.release() }
        wakeLock = null
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null
}
