package com.paganini.voxvault.service

import android.content.Context
import android.content.pm.PackageManager
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaPlayer
import android.media.MediaRecorder

class ListeningService(
    private val context: Context
    ) {
    var isListening = false

    private var audioRecord: AudioRecord? = null
    var mediaPlayer: MediaPlayer? = null

    private val sampleRate = 16000
    private val channelConfig = AudioFormat.CHANNEL_IN_MONO
    private val audioFormat = AudioFormat.ENCODING_PCM_16BIT

    fun toggle() {
        if (isListening){
            endListening()
        }else{
            startListening()
        }
        isListening = !isListening
    }

    fun startListening() {
        if (context.packageManager.hasSystemFeature(PackageManager.FEATURE_MICROPHONE)) {
            val minBufferSize = AudioRecord.getMinBufferSize(sampleRate, channelConfig, audioFormat)
            
            try {
                audioRecord = AudioRecord(
                    MediaRecorder.AudioSource.MIC,
                    sampleRate,
                    channelConfig,
                    audioFormat,
                    minBufferSize
                )

                audioRecord?.startRecording()
            } catch (_: SecurityException) {
                // Handle permission not granted
            }
        }
    }

    fun endListening() {
        audioRecord?.stop()
        audioRecord?.release()
        audioRecord = null
    }
}