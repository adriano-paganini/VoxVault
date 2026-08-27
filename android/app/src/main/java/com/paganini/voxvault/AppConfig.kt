package com.paganini.voxvault

import android.Manifest
import android.graphics.Color
import android.media.AudioFormat
import android.media.MediaRecorder
import com.konovalov.vad.silero.config.FrameSize
import com.konovalov.vad.silero.config.Mode
import com.konovalov.vad.silero.config.SampleRate
import androidx.core.graphics.toColorInt

object AppConfig {
    object Audio {
        const val SAMPLE_RATE = 16000
        const val CHANNEL_CONFIG = AudioFormat.CHANNEL_IN_MONO
        const val AUDIO_FORMAT = AudioFormat.ENCODING_PCM_16BIT
        const val AUDIO_SOURCE = MediaRecorder.AudioSource.MIC
        
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
        var RECORDING_MAX_SILENCE = 20000L

        var PRE_RECORDING_BUFFER_LENGTH_MS = 5000L

        /**
         * Number of samples required to store [PRE_RECORDING_BUFFER_LENGTH_MS] of audio.
         * (16,000 * 5,000) / 1,000 = 80,000
         */
        val PRE_RECORDING_BUFFER_SIZE: Int
            get() = ((SAMPLE_RATE * PRE_RECORDING_BUFFER_LENGTH_MS) / 1000).toInt()

        var RECORDING_CHUNK_SIZE_MS = 120000L
    }

    object VAD {
        val SAMPLE_RATE = SampleRate.SAMPLE_RATE_16K
        val FRAME_SIZE = FrameSize.FRAME_SIZE_1536
        val MODE = Mode.NORMAL
        const val SILENCE_DURATION_MS = 96
        const val SPEECH_DURATION_MS = 96
    }

    object UI {
        val COLOR_IDLE = "#444444".toColorInt() // Dark gray for better white text contrast
        const val COLOR_SILENCE = Color.RED      // 0: Active but quiet
        const val COLOR_SPEECH = Color.GREEN     // 1: Active and hearing voice
        val COLOR_TRANSITION = Color.rgb(255, 165, 0) // 2: Active orange

        fun getStateColor(isListening: Boolean, state: Int): Int {
            if (!isListening) return COLOR_IDLE
            return when (state) {
                1 -> COLOR_SPEECH
                2 -> COLOR_TRANSITION
                else -> COLOR_SILENCE
            }
        }

        const val NOTIFICATION_CHANNEL_ID = "voxvault_listening_channel"
        const val NOTIFICATION_ID = 1001
    }

    object Permissions {
        /**
         * List of permissions that require a runtime popup.
         * Automatically filters out [Manifest.permission.POST_NOTIFICATIONS] on devices below Android 13.
         */
        val REQUIRED: Array<String>
            get() {
                val list = mutableListOf(
                    Manifest.permission.RECORD_AUDIO
                )
                
                if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.TIRAMISU) {
                    list.add(Manifest.permission.POST_NOTIFICATIONS)
                }
                
                return list.toTypedArray()
            }
    }

    object Web{
        var BACKEND_ADDRESS = "lab.elk-iwato.ts.net"
    }
}
