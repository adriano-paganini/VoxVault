package com.paganini.voxvault

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.content.res.ColorStateList
import android.graphics.Color
import android.os.Bundle
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.CheckBox
import android.widget.LinearLayout
import android.widget.ToggleButton
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.children
import androidx.core.view.isEmpty
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.lifecycleScope
import com.paganini.voxvault.viewModel.MainViewModel
import com.paganini.voxvault.dataClass.Recording
import com.paganini.voxvault.service.ListeningService
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import java.io.File
import kotlin.time.Duration.Companion.seconds

class MainActivity : AppCompatActivity() {

    // region Properties
    private lateinit var viewModel: MainViewModel
    private var currentService: ListeningService? = null
    private val displayedRecordings = mutableSetOf<String>()
    // endregion

    // region Lifecycle
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        
        // UI Initialization
        enableEdgeToEdge()
        setContentView(R.layout.activity_main)
        setupEdgeToEdge()

        // Service & ViewModel Setup
        viewModel = ViewModelProvider(this)[MainViewModel::class.java]
        setupServiceObservation()

        // Input Listeners
        setupClickListeners()

        // Recording File Listener
        setupRecordingFileReaderService()

        // Initial State / Permissions
        if (savedInstanceState == null) {
            requestAppPermissions()
        } else {
            ensureServiceIsRunningIfPermitted()
        }
    }

    override fun onStart() {
        super.onStart()
        viewModel.bindService()
    }

    override fun onStop() {
        super.onStop()
        viewModel.unbindService()
    }
    // endregion

    // region Service Logic
    private fun setupServiceObservation() {
        val listeningToggleButton = findViewById<ToggleButton>(R.id.listeningToggle)
        val mainView = findViewById<View>(R.id.main)

        viewModel.listeningService.observe(this) { service ->
            currentService = service
            if (service != null) {
                // Initial Sync
                syncUIWithService(service, listeningToggleButton, mainView)

                // Live Updates
                service.speakingListener = {
                    runOnUiThread {
                        syncUIWithService(service, listeningToggleButton, mainView)
                    }
                }
            }
        }
    }

    fun setupRecordingFileReaderService(){
        lifecycleScope.launch {
            while (true) {
                val recordings = viewModel.recordingFileReaderService.getAllRecordings()

                recordings.forEach { r ->
                    addToScrollableList(r)
                }

                delay(15.seconds)
            }
        }
    }

    private fun syncUIWithService(service: ListeningService, toggle: ToggleButton, root: View) {
        toggle.isChecked = service.isListening
        val color = AppConfig.UI.getStateColor(service.isListening, service.sharedSpeaking)
        
        root.setBackgroundColor(Color.TRANSPARENT)
        toggle.backgroundTintList = ColorStateList.valueOf(color)
    }

    private fun startListeningService() {
        val intent = Intent(this, ListeningService::class.java)
        ContextCompat.startForegroundService(this, intent)
    }
    // endregion

    // region UI Setup
    private fun setupEdgeToEdge() {
        ViewCompat.setOnApplyWindowInsetsListener(findViewById(R.id.main)) { v, insets ->
            val systemBars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            v.setPadding(systemBars.left, systemBars.top, systemBars.right, systemBars.bottom)
            insets
        }
    }

    private fun setupClickListeners() {
        val listeningToggleButton = findViewById<ToggleButton>(R.id.listeningToggle)
        listeningToggleButton.setOnClickListener {
            currentService?.toggle()
        }

        val listeningSelectAllButton = findViewById<Button>(R.id.selectAll)
        listeningSelectAllButton.setOnClickListener {
            val parent = findViewById<LinearLayout>(R.id.recordingLinearLayout)
            val anyUnchecked = parent.children.any { child ->
                !child.findViewById<CheckBox>(R.id.recordingCheckbox).isChecked
            }

            parent.children.forEach { child ->
                child.findViewById<CheckBox>(R.id.recordingCheckbox).isChecked = anyUnchecked
            }
            updateSelectAllButtonText()
        }

        val listeningDeleteButton = findViewById<Button>(R.id.deleteButton)
        listeningDeleteButton.setOnClickListener {
            val parent = findViewById<LinearLayout>(R.id.recordingLinearLayout)
            val toDelete = parent.children.filter { child ->
                child.findViewById<CheckBox>(R.id.recordingCheckbox).isChecked
            }.toList()

            toDelete.forEach { child ->
                deleteRecording(child)
            }
        }
    }

    private fun updateSelectAllButtonText() {
        val parent = findViewById<LinearLayout>(R.id.recordingLinearLayout)
        val selectAllButton = findViewById<Button>(R.id.selectAll)

        val anyUnchecked = parent.children.any { child ->
            !child.findViewById<CheckBox>(R.id.recordingCheckbox).isChecked
        }

        if (anyUnchecked || parent.isEmpty()) {
            selectAllButton.setText(R.string.select_all)
        } else {
            selectAllButton.setText(R.string.deselect_all)
        }
    }
    // endregion

    // region Permissions
    private val requestMultiplePermissionsLauncher = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { _ ->
        if (hasCriticalPermissions()) {
            startListeningService()
        } else {
            finish() // App cannot function without basic permissions
        }
    }

    private fun requestAppPermissions() {
        val missing = AppConfig.Permissions.REQUIRED.filter {
            ContextCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED
        }

        if (missing.isNotEmpty()) {
            requestMultiplePermissionsLauncher.launch(missing.toTypedArray())
        } else {
            startListeningService()
        }
    }

    private fun hasCriticalPermissions(): Boolean {
        return ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) ==
                PackageManager.PERMISSION_GRANTED
    }

    private fun ensureServiceIsRunningIfPermitted() {
        if (hasCriticalPermissions()) {
            startListeningService()
        }
    }
    // endregion

    // region List Management
    fun addToScrollableList(recording: Recording) {
        val parent = findViewById<LinearLayout>(R.id.recordingLinearLayout)
        if (!displayedRecordings.contains(recording.name)) {
            displayedRecordings.add(recording.name)
            val view = recording.getView(this, parent)
            view.findViewById<CheckBox>(R.id.recordingCheckbox).setOnClickListener {
                updateSelectAllButtonText()
            }
            parent.addView(view)
        }
    }

    fun deleteRecording(recordingView: View) {
        // We use the 'tag' we set in Recording.getView to get the exact folder name
        val folderName = recordingView.tag as? String ?: return
        val recordingDir = File(filesDir, "recordings/$folderName")

        if (recordingDir.exists()) {
            recordingDir.deleteRecursively()
        }

        val parent = recordingView.parent as? ViewGroup
        parent?.removeView(recordingView)
        displayedRecordings.remove(folderName)
        updateSelectAllButtonText()
    }
    // endregion
}
