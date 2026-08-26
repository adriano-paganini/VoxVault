package com.paganini.voxvault.service

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import androidx.core.content.ContextCompat
import com.konovalov.vad.silero.VadSilero
import com.konovalov.vad.silero.config.FrameSize
import com.konovalov.vad.silero.config.Mode
import com.konovalov.vad.silero.config.SampleRate

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
        sampleRate = SampleRate.SAMPLE_RATE_16K,
        frameSize = FrameSize.FRAME_SIZE_512,
        mode = Mode.NORMAL,
        silenceDurationMs = 300,
        speechDurationMs = 50
    )

    private var audioListener: AudioRecord? = null
    private val sampleRate = 16000
    private val channelConfig = AudioFormat.CHANNEL_IN_MONO
    private val audioFormat = AudioFormat.ENCODING_PCM_16BIT
    private val bufferSize = 512

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
            val minBufferSize = AudioRecord.getMinBufferSize(sampleRate, channelConfig, audioFormat)
            if (ContextCompat.checkSelfPermission(
                    appContext,
                    Manifest.permission.RECORD_AUDIO
                ) == PackageManager.PERMISSION_GRANTED
            ) {
                audioListener = AudioRecord(
                    MediaRecorder.AudioSource.VOICE_RECOGNITION,
                    sampleRate,
                    channelConfig,
                    audioFormat,
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
            val buffer = ShortArray(bufferSize)
            while (isListening) {
                val readResult = audioListener?.read(buffer,0,bufferSize)
                if (readResult != null && readResult >0) {
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