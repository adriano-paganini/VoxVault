package com.paganini.voxvault

import android.content.Intent
import android.os.Bundle
import android.view.View
import android.widget.Button
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.activity.enableEdgeToEdge
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.lifecycle.lifecycleScope
import com.google.android.material.textfield.TextInputEditText
import com.google.android.material.textfield.TextInputLayout
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch

class SettingsActivity : AppCompatActivity() {

    private lateinit var settingsManager: SettingsManager
    private lateinit var encryptionPublicKeyEdit: TextInputEditText

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContentView(R.layout.activity_settings)
        ViewCompat.setOnApplyWindowInsetsListener(findViewById(R.id.settingsRoot)) { view, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.ime())
            view.setPadding(bars.left, bars.top, bars.right, bars.bottom)
            insets
        }

        settingsManager = SettingsManager(this)

        val backendUrlEdit = findViewById<TextInputEditText>(R.id.backendUrlEdit)
        val backendPortEdit = findViewById<TextInputEditText>(R.id.backendPortEdit)
        val maxSilenceEdit = findViewById<TextInputEditText>(R.id.maxSilenceEdit)
        val preBufferEdit = findViewById<TextInputEditText>(R.id.preBufferEdit)
        encryptionPublicKeyEdit = findViewById(R.id.encryptionPublicKeyEdit)
        
        val maxSilenceLayout = findViewById<TextInputLayout>(R.id.maxSilenceLayout)
        val preBufferLayout = findViewById<TextInputLayout>(R.id.preBufferLayout)
        val encryptionPublicKeyLayout = findViewById<TextInputLayout>(R.id.encryptionPublicKeyLayout)
        
        val saveButton = findViewById<Button>(R.id.saveButton)
        val backButton = findViewById<View>(R.id.backButton)

        lifecycleScope.launch {
            backendUrlEdit.setText(settingsManager.backendUrlFlow.first())
            backendPortEdit.setText(settingsManager.backendPortFlow.first())
            maxSilenceEdit.setText((settingsManager.maxSilenceTimeFlow.first() / 1000.0).toString())
            preBufferEdit.setText((settingsManager.preBufferLengthFlow.first() / 1000.0).toString())
            encryptionPublicKeyEdit.setText(settingsManager.encryptionPublicKeyFlow.first())
            
            handleDeepLink(intent)
        }

        backButton.setOnClickListener {
            navigateToMain()
        }

        saveButton.setOnClickListener {
            val backendUrl = backendUrlEdit.text.toString().trim()
            val backendPort = backendPortEdit.text.toString().trim()
            
            val maxSilenceSec = maxSilenceEdit.text.toString().toDoubleOrNull() ?: 0.0
            val preBufferSec = preBufferEdit.text.toString().toDoubleOrNull() ?: 0.0

            val encryptionKey = encryptionPublicKeyEdit.text.toString()

            var isValid = true
            val backendUrlLayout = findViewById<TextInputLayout>(R.id.backendUrlLayout)
            backendUrlLayout.error = null
            if (backendUrl.isNotEmpty()) {
                try {
                    BackendUrl.resolve(backendUrl, backendPort, "ui/explorer")
                } catch (_: Exception) {
                    backendUrlLayout.error = getString(R.string.invalid_server_address)
                    isValid = false
                }
            }

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

            if (encryptionKey.isNotEmpty() && encryptionKey.length != 44) {
                encryptionPublicKeyLayout.error = "Public key must be 44 characters long"
                isValid = false
            } else {
                encryptionPublicKeyLayout.error = null
            }

            if (!isValid) return@setOnClickListener

            lifecycleScope.launch {
                settingsManager.updateBackendUrl(backendUrl)
                settingsManager.updateBackendPort(backendPort)
                settingsManager.updateMaxSilenceTime((maxSilenceSec * 1000).toLong())
                settingsManager.updatePreBufferLength((preBufferSec * 1000).toLong())
                settingsManager.updateEncryptionPublicKey(encryptionKey)
                navigateToMain()
            }
        }
    }

    private fun navigateToMain() {
        if (!isTaskRoot) {
            finish()
            return
        }
        val intent = Intent(this, MainActivity::class.java)
        intent.addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP)
        startActivity(intent)
        finish()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handleDeepLink(intent)
    }

    private fun handleDeepLink(intent: Intent?) {
        val data = intent?.data ?: return
        if ((data.scheme == "voxvault" || data.scheme == "intent") && data.host == "setup") {
            val key = data.getQueryParameter("key")?.replace(" ", "+") ?: return
            if (key.length == 44) {
                encryptionPublicKeyEdit.setText(key)
                AppConfig.Encryption.ENCRYPTION_PUBLIC_KEY = key
                lifecycleScope.launch {
                    settingsManager.updateEncryptionPublicKey(key)
                    Toast.makeText(this@SettingsActivity, "Public key saved from link", Toast.LENGTH_SHORT).show()
                }
            }
        }
    }
}
