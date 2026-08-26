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
    }

    object VAD {
        val SAMPLE_RATE = SampleRate.SAMPLE_RATE_16K
        val FRAME_SIZE = FrameSize.FRAME_SIZE_1536
        val MODE = Mode.NORMAL
        const val SILENCE_DURATION_MS = 500
        const val SPEECH_DURATION_MS = 100
    }

    object UI {
        val COLOR_SPEECH = Color.GREEN
        val COLOR_SILENCE = Color.RED
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
