package com.paganini.voxvault

import android.Manifest
import android.content.pm.PackageManager
import android.graphics.Color
import android.location.Location
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
import java.util.Date
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
            mainView.setBackgroundColor(if (listeningService.sharedSpeaking) Color.GREEN else Color.RED)
        }

        listeningService.speakingListener = {
            runOnUiThread {
                if (listeningService.sharedSpeaking) {
                    mainView.setBackgroundColor(Color.GREEN)
                } else {
                    mainView.setBackgroundColor(Color.RED)
                }
            }
        }

        if (savedInstanceState == null) {
            requestAppPermissions()
        }
    }

    private fun requestAppPermissions() {
        val permissionsToRequest = arrayOf(
            Manifest.permission.RECORD_AUDIO,
            Manifest.permission.ACCESS_COARSE_LOCATION,
            Manifest.permission.ACCESS_FINE_LOCATION,
            Manifest.permission.INTERNET
        )

        // Filter out permissions that are already granted
        val missing = permissionsToRequest.filter {
            ContextCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED
        }

        if (missing.isNotEmpty()) {
            requestMultiplePermissionsLauncher.launch(missing.toTypedArray())
        }
    }

    fun clearTestRecordings() {
        val parent = findViewById<LinearLayout>(R.id.recordingLinearLayout)
        parent.removeAllViews()
    }

    fun addTestRecordings() {
        val recordings = listOf(
            Recording(Date(), Date(), Location("gps"), "Test1"),
            Recording(Date(), Date(), Location("gps"), "Test2"),
            Recording(Date(), Date(), Location("gps"), "Test3"),
            Recording(Date(), Date(), Location("gps"), "Test4"),
            Recording(Date(), Date(), Location("gps"), "Test5"),
            Recording(Date(), Date(), Location("gps"), "Test6"),
            Recording(Date(), Date(), Location("gps"), "Test7"),
            Recording(Date(), Date(), Location("gps"), "Test1"),
            Recording(Date(), Date(), Location("gps"), "Test2"),
            Recording(Date(), Date(), Location("gps"), "Test3"),
            Recording(Date(), Date(), Location("gps"), "Test4"),
            Recording(Date(), Date(), Location("gps"), "Test5"),
            Recording(Date(), Date(), Location("gps"), "Test6"),
            Recording(Date(), Date(), Location("gps"), "Test7"),
            Recording(Date(), Date(), Location("gps"), "Test1"),
            Recording(Date(), Date(), Location("gps"), "Test2"),
            Recording(Date(), Date(), Location("gps"), "Test3"),
            Recording(Date(), Date(), Location("gps"), "Test4"),
            Recording(Date(), Date(), Location("gps"), "Test5"),
            Recording(Date(), Date(), Location("gps"), "Test6"),
            Recording(Date(), Date(), Location("gps"), "Test7"),
            Recording(Date(), Date(), Location("gps"), "Test8")
        )

        for (recording in recordings) {
            addToScrollableList(recording)
        }
    }

    fun addToScrollableList(recording: Recording) {
        val parent = findViewById<LinearLayout>(R.id.recordingLinearLayout)
        parent.addView(recording.textView(this))

    }

}

