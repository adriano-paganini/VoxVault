package com.paganini.voxvault.service

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.media.AudioRecord
import android.os.Binder
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import com.konovalov.vad.silero.VadSilero
import com.paganini.voxvault.AppConfig
import com.paganini.voxvault.MainActivity
import com.paganini.voxvault.R

class ListeningService : Service() {

    private val binder = LocalBinder()

    var speakingListener: (() -> Unit)? = null
    var sharedSpeaking = 0
    var isListening = false

    private var listeningThread: Thread? = null
    private var vad: VadSilero? = null
    private var audioListener: AudioRecord? = null
    private var wakeLock: PowerManager.WakeLock? = null

    private var ringBufferInsertionIndex = 0
    private var ringBufferReadIndex = 0
    private var samplesInRingBuffer = 0
    private val ringBuffer = ShortArray(AppConfig.Audio.PRE_RECORDING_BUFFER_SIZE)

    inner class LocalBinder : Binder() {
        fun getService(): ListeningService = this@ListeningService
    }

    override fun onBind(intent: Intent?): IBinder = binder

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
        
        vad = VadSilero(
            applicationContext,
            sampleRate = AppConfig.VAD.SAMPLE_RATE,
            frameSize = AppConfig.VAD.FRAME_SIZE,
            mode = AppConfig.VAD.MODE,
            silenceDurationMs = AppConfig.VAD.SILENCE_DURATION_MS,
            speechDurationMs = AppConfig.VAD.SPEECH_DURATION_MS
        )

        val powerManager = getSystemService(Context.POWER_SERVICE) as PowerManager
        wakeLock = powerManager.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "VoxVault:RecordingWakeLock")
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val notification = buildNotification("Ready to listen")
        
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(
                AppConfig.UI.NOTIFICATION_ID,
                notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE
            )
        } else {
            startForeground(AppConfig.UI.NOTIFICATION_ID, notification)
        }
        
        return START_STICKY
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val name = "VoxVault Listening Service"
            val descriptionText = "Ensures background recording works reliably"
            val importance = NotificationManager.IMPORTANCE_LOW
            val channel = NotificationChannel(AppConfig.UI.NOTIFICATION_CHANNEL_ID, name, importance).apply {
                description = descriptionText
            }
            val notificationManager: NotificationManager =
                getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            notificationManager.createNotificationChannel(channel)
        }
    }

    private fun buildNotification(text: String): Notification {
        val intent = Intent(this, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK
        }
        val pendingIntent: PendingIntent = PendingIntent.getActivity(this, 0, intent, PendingIntent.FLAG_IMMUTABLE)

        return NotificationCompat.Builder(this, AppConfig.UI.NOTIFICATION_CHANNEL_ID)
            .setSmallIcon(R.mipmap.ic_launcher) // TODO: Use a proper icon
            .setContentTitle("VoxVault")
            .setContentText(text)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setContentIntent(pendingIntent)
            .setOngoing(true)
            .build()
    }

    fun toggle() {
        if (isListening) {
            endListening()
        } else {
            startListening()
        }
        // No longer toggling isListening here, it's done in start/end
    }

    fun setupListening() {
        if (packageManager.hasSystemFeature(PackageManager.FEATURE_MICROPHONE)) {
            val minBufferSize = AudioRecord.getMinBufferSize(
                AppConfig.Audio.SAMPLE_RATE,
                AppConfig.Audio.CHANNEL_CONFIG,
                AppConfig.Audio.AUDIO_FORMAT
            )
            if (ContextCompat.checkSelfPermission(
                    this,
                    Manifest.permission.RECORD_AUDIO
                ) == PackageManager.PERMISSION_GRANTED
            ) {
                audioListener = AudioRecord(
                    AppConfig.Audio.AUDIO_SOURCE,
                    AppConfig.Audio.SAMPLE_RATE,
                    AppConfig.Audio.CHANNEL_CONFIG,
                    AppConfig.Audio.AUDIO_FORMAT,
                    minBufferSize
                )
            }
        }
    }

    fun startListening() {
        if (isListening) return
        
        if (audioListener == null) {
            setupListening()
        }
        
        if (audioListener?.state != AudioRecord.STATE_INITIALIZED) {
            return
        }

        isListening = true
        audioListener?.startRecording()
        wakeLock?.acquire(10 * 60 * 1000L /*10 minutes*/)
        
        // Update notification
        val notificationManager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        notificationManager.notify(AppConfig.UI.NOTIFICATION_ID, buildNotification("Listening..."))

        listeningThread = Thread {
            listeningLoop()
        }
        listeningThread?.start()
    }

    private fun listeningLoop() {
        val buffer = ShortArray(AppConfig.Audio.BUFFER_SIZE)
        var silenceDuration = 0
        var recordingState = 0
        while (isListening) {
            val readResult = audioListener?.read(buffer, 0, AppConfig.Audio.BUFFER_SIZE)
            if (readResult != null && readResult > 0) {
                if (recordingState == 0) ringBufferAppend(buffer)
                val isSpeech = vad?.isSpeech(buffer) ?: false

                if (isSpeech) {
                    if (recordingState == 0) {
                        recordingState = 1
                        silenceDuration = 0
                        sharedSpeaking = 1
                        speakingListener?.invoke()
                    } else if (recordingState == 2) {
                        recordingState = 1
                        silenceDuration = 0
                        sharedSpeaking = 1
                        speakingListener?.invoke()
                    }
                } else {
                    if (recordingState == 1) {
                        recordingState = 2
                        sharedSpeaking = 2
                        speakingListener?.invoke()
                    } else if (recordingState == 2) {
                        silenceDuration += AppConfig.Audio.MS_PER_FRAME
                        sharedSpeaking = 2
                        speakingListener?.invoke()
                    }
                }
                if (silenceDuration >= AppConfig.Audio.RECORDING_MAX_SILENCE) {
                    recordingState = 0
                    ringBufferInsertionIndex = 0
                    ringBufferReadIndex = 0
                    samplesInRingBuffer = 0
                    ringBuffer.fill(0)
                    sharedSpeaking = 0
                    speakingListener?.invoke()
                }
            }
        }
    }

    fun endListening() {
        if (!isListening) return
        isListening = false
        audioListener?.stop()
        audioListener?.release()
        audioListener = null
        samplesInRingBuffer = 0
        if (wakeLock?.isHeld == true) {
            wakeLock?.release()
        }
        val notificationManager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        notificationManager.notify(AppConfig.UI.NOTIFICATION_ID, buildNotification("Ready to listen"))
    }

    fun ringBufferAppend(data: ShortArray) {
        val remainingSpace = AppConfig.Audio.PRE_RECORDING_BUFFER_SIZE - ringBufferInsertionIndex

        if (remainingSpace >= AppConfig.Audio.BUFFER_SIZE) {
            System.arraycopy(data, 0, ringBuffer, ringBufferInsertionIndex, AppConfig.Audio.BUFFER_SIZE)
        } else {
            System.arraycopy(data, 0, ringBuffer, ringBufferInsertionIndex, remainingSpace)
            System.arraycopy(data, remainingSpace, ringBuffer, 0, AppConfig.Audio.BUFFER_SIZE - remainingSpace)
        }

        ringBufferInsertionIndex = (ringBufferInsertionIndex + AppConfig.Audio.BUFFER_SIZE) % AppConfig.Audio.PRE_RECORDING_BUFFER_SIZE

        samplesInRingBuffer = minOf(
            samplesInRingBuffer + AppConfig.Audio.BUFFER_SIZE,
            AppConfig.Audio.PRE_RECORDING_BUFFER_SIZE
        )
    }

    override fun onDestroy() {
        super.onDestroy()
        endListening()
        vad?.close()
    }
}
