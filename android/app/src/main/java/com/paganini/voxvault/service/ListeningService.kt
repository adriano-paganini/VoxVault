package com.paganini.voxvault.service

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.media.AudioRecord
import androidx.core.content.ContextCompat
import com.konovalov.vad.silero.VadSilero
import com.paganini.voxvault.AppConfig

class ListeningService(
    private val context: Context
) {

    var speakingListener: (()->Unit)?=null
    var sharedSpeaking = false
    var isListening = false
    private val appContext = context.applicationContext

    private var listeningThread: Thread? = null
    private val vad: VadSilero = VadSilero(
        appContext,
        sampleRate = AppConfig.VAD.SAMPLE_RATE,
        frameSize = AppConfig.VAD.FRAME_SIZE,
        mode = AppConfig.VAD.MODE,
        silenceDurationMs = AppConfig.VAD.SILENCE_DURATION_MS,
        speechDurationMs = AppConfig.VAD.SPEECH_DURATION_MS
    )

    private var audioListener: AudioRecord? = null

    fun toggle() {
        if (isListening) {
            endListening()
        } else {
            startListening()
        }
        isListening = !isListening
    }


    fun setupListening() {
        if (context.packageManager.hasSystemFeature(PackageManager.FEATURE_MICROPHONE)) {
            val minBufferSize = AudioRecord.getMinBufferSize(
                AppConfig.Audio.SAMPLE_RATE,
                AppConfig.Audio.CHANNEL_CONFIG,
                AppConfig.Audio.AUDIO_FORMAT
            )
            if (ContextCompat.checkSelfPermission(
                    appContext,
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

            if (audioListener == null){
                setupListening()
            }
            audioListener?.startRecording()

            listeningThread = Thread {
                listeningLoop()
            }
            listeningThread?.start()
        }

        fun listeningLoop() {
            val buffer = ShortArray(AppConfig.Audio.BUFFER_SIZE)
            while (isListening) {
                val readResult = audioListener?.read(buffer, 0, AppConfig.Audio.BUFFER_SIZE)
                if (readResult != null && readResult > 0) {
                    val isSpeech = vad.isSpeech(buffer)

                    if(isSpeech != sharedSpeaking){
                        sharedSpeaking = !sharedSpeaking
                        speakingListener?.invoke()
                    }
                }
            }
        }

        fun endListening() {
            audioListener?.stop()
            audioListener?.release()
            audioListener = null
        }
    }