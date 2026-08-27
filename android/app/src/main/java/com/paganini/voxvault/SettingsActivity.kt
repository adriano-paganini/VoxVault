package com.paganini.voxvault

import android.os.Bundle
import android.widget.Button
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.google.android.material.textfield.TextInputEditText
import com.google.android.material.textfield.TextInputLayout
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch

class SettingsActivity : AppCompatActivity() {

    private lateinit var settingsManager: SettingsManager

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_settings)

        settingsManager = SettingsManager(this)

        val backendUrlEdit = findViewById<TextInputEditText>(R.id.backendUrlEdit)
        val maxSilenceEdit = findViewById<TextInputEditText>(R.id.maxSilenceEdit)
        val preBufferEdit = findViewById<TextInputEditText>(R.id.preBufferEdit)
        val chunkLengthEdit = findViewById<TextInputEditText>(R.id.chunkLengthEdit)
        
        val maxSilenceLayout = findViewById<TextInputLayout>(R.id.maxSilenceLayout)
        val preBufferLayout = findViewById<TextInputLayout>(R.id.preBufferLayout)
        val chunkLengthLayout = findViewById<TextInputLayout>(R.id.chunkLengthLayout)
        
        val saveButton = findViewById<Button>(R.id.saveButton)

        lifecycleScope.launch {
            backendUrlEdit.setText(settingsManager.backendUrlFlow.first())
            maxSilenceEdit.setText((settingsManager.maxSilenceTimeFlow.first() / 1000.0).toString())
            preBufferEdit.setText((settingsManager.preBufferLengthFlow.first() / 1000.0).toString())
            chunkLengthEdit.setText((settingsManager.chunkLengthFlow.first() / 1000.0).toString())
        }

        saveButton.setOnClickListener {
            val backendUrl = backendUrlEdit.text.toString()
            
            val maxSilenceSec = maxSilenceEdit.text.toString().toDoubleOrNull() ?: 0.0
            val preBufferSec = preBufferEdit.text.toString().toDoubleOrNull() ?: 0.0
            val chunkLengthSec = chunkLengthEdit.text.toString().toDoubleOrNull() ?: 0.0

            var isValid = true

            if (maxSilenceSec < 0.5) {
                maxSilenceLayout.error = "Must be at least 0.5 seconds"
                isValid = false
            } else if (maxSilenceSec > 3600.0) {
                maxSilenceLayout.error = "Cannot exceed 3600 seconds"
                isValid = false
            } else {
                maxSilenceLayout.error = null
            }

            if (preBufferSec < 0.1) {
                preBufferLayout.error = "Must be at least 0.1 seconds"
                isValid = false
            } else if (preBufferSec > 300.0) {
                preBufferLayout.error = "Cannot exceed 300 seconds (RAM limit)"
                isValid = false
            } else {
                preBufferLayout.error = null
            }

            if (chunkLengthSec < 30.0) {
                chunkLengthLayout.error = "Must be at least 30 seconds"
                isValid = false
            } else if (chunkLengthSec > 86400.0) {
                chunkLengthLayout.error = "Cannot exceed 24 hours"
                isValid = false
            } else {
                chunkLengthLayout.error = null
            }

            if (!isValid) return@setOnClickListener

            lifecycleScope.launch {
                settingsManager.updateBackendUrl(backendUrl)
                settingsManager.updateMaxSilenceTime((maxSilenceSec * 1000).toLong())
                settingsManager.updatePreBufferLength((preBufferSec * 1000).toLong())
                settingsManager.updateChunkLength((chunkLengthSec * 1000).toLong())
                finish()
            }
        }
    }
}
