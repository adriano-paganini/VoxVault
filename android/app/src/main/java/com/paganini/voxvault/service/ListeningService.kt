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

    var speakingListener: (() -> Unit)? = null
    var sharedSpeaking = 0
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

    private var ringBufferInsertionIndex = 0
    private var ringBufferReadIndex = 0
    private var samplesInRingBuffer = 0
    private val ringBuffer = ShortArray(AppConfig.Audio.PRE_RECORDING_BUFFER_SIZE)

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

        if (audioListener == null) {
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
        var silenceDuration = 0
        var recordingState = 0
        while (isListening) {
            val readResult = audioListener?.read(buffer, 0, AppConfig.Audio.BUFFER_SIZE)
            if (readResult != null && readResult > 0) {
                if(recordingState==0)ringBufferAppend(buffer)
                val isSpeech = vad.isSpeech(buffer)

                if (isSpeech) {
                    if (recordingState == 0) {
                        //start the recording of the acutal voice
                        recordingState = 1
                        silenceDuration = 0

                        sharedSpeaking = 1
                        speakingListener?.invoke()
                    }else if(recordingState == 2){
                        //the silence was interrupted by voice, before the timer could run out
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
                if (silenceDuration >= AppConfig.Audio.RECORDING_MAX_SILENCE){
                    //finish and save the recording
                    recordingState = 0
                    
                    // Reset the indices
                    ringBufferInsertionIndex = 0
                    ringBufferReadIndex = 0
                    samplesInRingBuffer = 0
                    
                    // Actually zero out the array memory
                    ringBuffer.fill(0)

                    sharedSpeaking = 0
                    speakingListener?.invoke()
                }

            }
        }
    }

    fun endListening() {
        audioListener?.stop()
        audioListener?.release()
        audioListener = null
        samplesInRingBuffer = 0
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
}