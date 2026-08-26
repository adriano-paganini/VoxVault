package com.paganini.voxvault

import android.Manifest
import android.content.pm.PackageManager
import android.os.Bundle
import android.view.View
import android.widget.LinearLayout
import android.widget.ToggleButton
import androidx.appcompat.app.AppCompatActivity
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.lifecycle.ViewModelProvider
import com.paganini.voxvault.ViewModel.MainViewModel
import com.paganini.voxvault.dataClass.Recording
import kotlin.getValue

class MainActivity : AppCompatActivity() {

    private val viewModel by lazy {
        ViewModelProvider(this)[MainViewModel::class.java]
    }

    private val listeningService get() = viewModel.listeningService
    private val requestMultiplePermissionsLauncher = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { _ ->
        // Check the actual system status instead of relying on the map,
        // which only contains the permissions we just requested.
        val micGranted = ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) == 
                         PackageManager.PERMISSION_GRANTED

        if (!micGranted) {
            finish()
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
        // Sync button state with service
        listeningToggleButton.isChecked = listeningService.isListening

        listeningToggleButton.setOnClickListener {
            listeningService.toggle()
        }

        val mainView = findViewById<View>(R.id.main)
        
        // Initial color setup
        if (listeningService.isListening) {
            mainView.setBackgroundColor(AppConfig.UI.getStateColor(listeningService.sharedSpeaking))
        }

        listeningService.speakingListener = {
            runOnUiThread {
                mainView.setBackgroundColor(AppConfig.UI.getStateColor(listeningService.sharedSpeaking))
            }
        }

        if (savedInstanceState == null) {
            requestAppPermissions()
        }
    }

    private fun requestAppPermissions() {
        val permissionsToRequest = AppConfig.Permissions.REQUIRED

        // Filter out permissions that are already granted
        val missing = permissionsToRequest.filter {
            ContextCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED
        }

        if (missing.isNotEmpty()) {
            requestMultiplePermissionsLauncher.launch(missing.toTypedArray())
        }
    }
    fun addToScrollableList(recording: Recording) {
        val parent = findViewById<LinearLayout>(R.id.recordingLinearLayout)
        parent.addView(recording.textView(this))

    }

}

