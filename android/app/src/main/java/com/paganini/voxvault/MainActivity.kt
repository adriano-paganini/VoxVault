package com.paganini.voxvault

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.view.View
import android.widget.LinearLayout
import android.widget.ToggleButton
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.lifecycle.ViewModelProvider
import com.paganini.voxvault.viewModel.MainViewModel
import com.paganini.voxvault.dataClass.Recording
import com.paganini.voxvault.service.ListeningService

class MainActivity : AppCompatActivity() {

    private val viewModel by lazy {
        ViewModelProvider(this)[MainViewModel::class.java]
    }

    private var currentService: ListeningService? = null

    private val requestMultiplePermissionsLauncher = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { _ ->
        val micGranted = ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) ==
                PackageManager.PERMISSION_GRANTED

        if (!micGranted) {
            finish()
        } else {
            startListeningService()
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContentView(R.layout.activity_main)

        ViewCompat.setOnApplyWindowInsetsListener(findViewById(R.id.main)) { v, insets ->
            val systemBars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            v.setPadding(systemBars.left, systemBars.top, systemBars.right, systemBars.bottom)
            insets
        }

        val listeningToggleButton = findViewById<ToggleButton>(R.id.listeningToggle)
        val mainView = findViewById<View>(R.id.main)

        viewModel.listeningService.observe(this) { service ->
            currentService = service
            if (service != null) {
                // Sync UI with existing service state
                listeningToggleButton.isChecked = service.isListening
                mainView.setBackgroundColor(AppConfig.UI.getStateColor(service.sharedSpeaking))

                service.speakingListener = {
                    runOnUiThread {
                        mainView.setBackgroundColor(AppConfig.UI.getStateColor(service.sharedSpeaking))
                    }
                }
            }
        }

        listeningToggleButton.setOnClickListener {
            currentService?.toggle()
        }

        if (savedInstanceState == null) {
            requestAppPermissions()
        } else {
            // If already granted, ensure service is running
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) ==
                PackageManager.PERMISSION_GRANTED) {
                startListeningService()
            }
        }
    }

    private fun startListeningService() {
        val intent = Intent(this, ListeningService::class.java)
        ContextCompat.startForegroundService(this, intent)
    }

    override fun onStart() {
        super.onStart()
        viewModel.bindService()
    }

    override fun onStop() {
        super.onStop()
        viewModel.unbindService()
    }

    private fun requestAppPermissions() {
        val permissionsToRequest = AppConfig.Permissions.REQUIRED
        val missing = permissionsToRequest.filter {
            ContextCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED
        }

        if (missing.isNotEmpty()) {
            requestMultiplePermissionsLauncher.launch(missing.toTypedArray())
        } else {
            startListeningService()
        }
    }

    fun addToScrollableList(recording: Recording) {
        val parent = findViewById<LinearLayout>(R.id.recordingLinearLayout)
        parent.addView(recording.textView(this))
    }
}
