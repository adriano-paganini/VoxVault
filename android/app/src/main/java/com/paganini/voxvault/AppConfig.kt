package com.paganini.voxvault

import android.Manifest
import android.graphics.Color
import android.media.AudioFormat
import android.media.MediaRecorder
import com.konovalov.vad.silero.config.FrameSize
import com.konovalov.vad.silero.config.Mode
import com.konovalov.vad.silero.config.SampleRate

object AppConfig {
    object Audio {
        const val SAMPLE_RATE = 16000
        const val CHANNEL_CONFIG = AudioFormat.CHANNEL_IN_MONO
        const val AUDIO_FORMAT = AudioFormat.ENCODING_PCM_16BIT
        const val AUDIO_SOURCE = MediaRecorder.AudioSource.VOICE_RECOGNITION
        
        /**
         * CRITICAL: This must match [AppConfig.VAD.FRAME_SIZE] numeric value.
         */
        const val BUFFER_SIZE = 1536 

        /**
         * Duration of one buffer in milliseconds.
         * Calculated as: (BUFFER_SIZE / SAMPLE_RATE) * 1000
         */
        const val MS_PER_FRAME = ((BUFFER_SIZE*1000)/SAMPLE_RATE)
        /**
         * Duration of silence to stop a recording in milliseconds
         */
        const val RECORDING_MAX_SILENCE = 20000

        const val PRE_RECORDING_BUFFER_LENGTH_MS = 5000

        /**
         * Number of samples required to store [PRE_RECORDING_BUFFER_LENGTH_MS] of audio.
         * (16,000 * 5,000) / 1,000 = 80,000
         */
        const val PRE_RECORDING_BUFFER_SIZE = (SAMPLE_RATE * PRE_RECORDING_BUFFER_LENGTH_MS) / 1000
    }

    object VAD {
        val SAMPLE_RATE = SampleRate.SAMPLE_RATE_16K
        val FRAME_SIZE = FrameSize.FRAME_SIZE_1536
        val MODE = Mode.NORMAL
        const val SILENCE_DURATION_MS = 500
        const val SPEECH_DURATION_MS = 100
    }

    object UI {
        val COLOR_SILENCE = Color.RED    // 0
        val COLOR_SPEECH = Color.GREEN   // 1
        val COLOR_TRANSITION = Color.rgb(255, 165, 0) // 2: Orange

        fun getStateColor(state: Int): Int {
            return when (state) {
                1 -> COLOR_SPEECH
                2 -> COLOR_TRANSITION
                else -> COLOR_SILENCE
            }
        }

        const val LIST_ITEM_PADDING_TOP = 100
    }

    object Permissions {
        val REQUIRED = arrayOf(
            Manifest.permission.RECORD_AUDIO,
            Manifest.permission.ACCESS_COARSE_LOCATION,
            Manifest.permission.ACCESS_FINE_LOCATION,
            Manifest.permission.INTERNET
        )
    }
}
