package com.paganini.voxvault.service

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.media.AudioRecord
import android.os.Binder
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import android.provider.ContactsContract
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import com.google.android.gms.location.FusedLocationProviderClient
import com.google.android.gms.location.LocationServices
import com.google.android.gms.location.Priority
import com.konovalov.vad.silero.VadSilero
import com.paganini.voxvault.AppConfig
import com.paganini.voxvault.MainActivity
import com.paganini.voxvault.R
import com.paganini.voxvault.dataClass.Recording
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import java.io.File
import java.io.FileOutputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

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
    private var samplesInRingBuffer = 0
    private val ringBuffer = ShortArray(AppConfig.Audio.PRE_RECORDING_BUFFER_SIZE)

    private var chunkCounter = 1
    private var currentFile : File?= null
    private var currentFileOutputStream : FileOutputStream? = null
    private lateinit var fusedLocationClient: FusedLocationProviderClient

    inner class LocalBinder : Binder() {
        fun getService(): ListeningService = this@ListeningService
    }

    override fun onBind(intent: Intent?): IBinder = binder

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()

        fusedLocationClient = LocationServices.getFusedLocationProviderClient(this)
        
        vad = VadSilero(
            applicationContext,
            sampleRate = AppConfig.VAD.SAMPLE_RATE,
            frameSize = AppConfig.VAD.FRAME_SIZE,
            mode = AppConfig.VAD.MODE,
            silenceDurationMs = AppConfig.VAD.SILENCE_DURATION_MS,
            speechDurationMs = AppConfig.VAD.SPEECH_DURATION_MS
        )

        val powerManager = getSystemService(POWER_SERVICE) as PowerManager
        wakeLock = powerManager.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "VoxVault:RecordingWakeLock")
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val notification = buildNotification("Ready to listen")
        
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
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
                getSystemService(NOTIFICATION_SERVICE) as NotificationManager
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

            val internalBufferSize = minBufferSize*10

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
                    internalBufferSize
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
        
        // Notify Activity to update color (it will now turn Red immediately)
        speakingListener?.invoke()

        // Update notification
        val notificationManager = getSystemService(NOTIFICATION_SERVICE) as NotificationManager
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
        var recordingDuration = 0
        while (isListening) {
            val readResult = audioListener?.read(buffer, 0, AppConfig.Audio.BUFFER_SIZE)
            if (readResult != null && readResult > 0) {
                if (recordingState == 0) ringBufferAppend(buffer)
                else{
                    writeToFile(buffer)
                    recordingDuration+= AppConfig.Audio.MS_PER_FRAME
                }
                val isSpeech = vad?.isSpeech(buffer) ?: false

                if (isSpeech) {
                    if (recordingState == 0) {
                        recordingState = 1
                        silenceDuration = 0

                        writeRingBufferToFile()

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

                if (recordingDuration >= AppConfig.Audio.RECORDING_CHUNK_SIZE_MS){
                    val directory = currentFile?.parent
                    currentFileOutputStream?.close()
                    currentFileOutputStream = null
                    chunkCounter++
                    recordingDuration = 0

                    currentFile = File("$directory/chunk_${String.format(Locale.US, "%03d", chunkCounter)}.pcm")
                    currentFileOutputStream = FileOutputStream(currentFile)
                }

                if (silenceDuration >= AppConfig.Audio.RECORDING_MAX_SILENCE) {
                    completeMetadata()
                    recordingState = 0
                    silenceDuration = 0
                    recordingDuration = 0
                    ringBufferInsertionIndex = 0
                    samplesInRingBuffer = 0
                    ringBuffer.fill(0)
                    chunkCounter = 1

                    try {
                        currentFileOutputStream?.flush()
                        currentFileOutputStream?.close()
                    } catch (_: Exception) { }
                    currentFileOutputStream = null

                    sharedSpeaking = 0
                    speakingListener?.invoke()
                }
            }
        }
    }

    fun writeRingBufferToFile(){
        val timestamp = SimpleDateFormat(
            "yyyy-MM-dd_HH-mm-ss",
            Locale.US
        ).format(Date())

        val recordingDir = File(
            applicationContext.filesDir,
            "recordings/$timestamp"
        )
         recordingDir.mkdirs()

        currentFile = File("$recordingDir/chunk_${String.format(Locale.US, "%03d", chunkCounter)}.pcm")

        saveLocationMetadata(recordingDir)

        val byteBuffer = ByteBuffer.allocate(AppConfig.Audio.PRE_RECORDING_BUFFER_SIZE*2)
            .order(ByteOrder.LITTLE_ENDIAN)

        val bufferedSamples = ringBufferGetAll()

        byteBuffer.asShortBuffer().put(bufferedSamples)

        currentFileOutputStream = FileOutputStream(currentFile)

        currentFileOutputStream?.write(byteBuffer.array())
    }

    fun writeToFile(buffer: ShortArray){
        val byteBuffer = ByteBuffer.allocate(AppConfig.Audio.BUFFER_SIZE*2).order(ByteOrder.LITTLE_ENDIAN)
        byteBuffer.asShortBuffer().put(buffer)

        currentFileOutputStream?.write(byteBuffer.array())
    }
    fun endListening() {
        if (!isListening) return
        completeMetadata()
        isListening = false

        // 1. Stop hardware resources
        audioListener?.stop()
        audioListener?.release()
        audioListener = null

        // 2. Safely close stream
        try {
            currentFileOutputStream?.flush()
            currentFileOutputStream?.close()
        } catch (_: Exception) { }
        currentFileOutputStream = null

        // 3. Reset internal logic
        samplesInRingBuffer = 0
        ringBufferInsertionIndex = 0
        chunkCounter = 1
        currentFile = null
        ringBuffer.fill(0)

        // 4. Reset UI State
        sharedSpeaking = 0
        speakingListener?.invoke() // Notify Activity to update color (it will now turn Gray immediately)

        if (wakeLock?.isHeld == true) {
            wakeLock?.release()
        }
        val notificationManager = getSystemService(NOTIFICATION_SERVICE) as NotificationManager
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

    fun ringBufferGetAll():ShortArray{
        val bufferContent = ShortArray(AppConfig.Audio.PRE_RECORDING_BUFFER_SIZE)
        System.arraycopy(ringBuffer, ringBufferInsertionIndex, bufferContent,
            0,AppConfig.Audio.PRE_RECORDING_BUFFER_SIZE-ringBufferInsertionIndex)
        System.arraycopy(ringBuffer,0,bufferContent,
            AppConfig.Audio.PRE_RECORDING_BUFFER_SIZE-ringBufferInsertionIndex,ringBufferInsertionIndex)
        return bufferContent
    }

    private fun saveLocationMetadata(directory: File) {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED ||
            ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_COARSE_LOCATION) == PackageManager.PERMISSION_GRANTED) {

            fusedLocationClient.getCurrentLocation(Priority.PRIORITY_BALANCED_POWER_ACCURACY, null)
                .addOnSuccessListener { location ->
                    if (location != null) {
                        val metadataFile = File(directory, "metadata.json")
                        
                        // Create initial Recording object with location data
                        val recording = Recording(
                            latitude = location.latitude,
                            longitude = location.longitude,
                            timestamp = System.currentTimeMillis()
                        )
                        
                        // Encode to JSON string and write to file
                        metadataFile.writeText(Json.encodeToString(recording))
                    }
                }
        }
    }

    private fun completeMetadata() {
        val path = currentFile?.parent ?: return
        val metadataFile = File(path, "metadata.json")
        if (!metadataFile.exists()) return

        try {
            // 1. Read and decode existing metadata
            val recording = Json.decodeFromString<Recording>(metadataFile.readText())

            // 2. Calculate duration and extract name
            val totalBytes = File(path)
                .listFiles { f -> f.name.startsWith("chunk") }
                ?.sumOf { it.length() }
                ?: 0L
            
            val durationSeconds = totalBytes.toDouble() / (AppConfig.Audio.SAMPLE_RATE * 2)
            val folderName = File(path).name

            // 3. Update fields
            recording.duration = durationSeconds
            recording.name = folderName

            // 4. Encode and save back
            metadataFile.writeText(Json.encodeToString(recording))
        } catch (e: Exception) {
            // Log or handle error
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        endListening()
        vad?.close()
    }
}
