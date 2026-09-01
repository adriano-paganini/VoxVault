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
import android.widget.Toast
import android.widget.ToggleButton
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.children
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.lifecycleScope
import com.paganini.voxvault.viewModel.MainViewModel
import com.paganini.voxvault.dataClass.Recording
import com.paganini.voxvault.service.ListeningService
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File
import kotlin.time.Duration.Companion.seconds

class MainActivity : AppCompatActivity() {

    // region Properties
    private lateinit var viewModel: MainViewModel
    private lateinit var settingsManager: SettingsManager
    private var currentService: ListeningService? = null
    private val displayedRecordings = mutableMapOf<String, Recording>()
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
        settingsManager = SettingsManager(this)
        setupServiceObservation()
        setupSettingsObservation()

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

    private fun setupSettingsObservation() {
        lifecycleScope.launch {
            settingsManager.backendUrlFlow.collect { url ->
                AppConfig.Web.BACKEND_ADDRESS = url
            }
        }

        lifecycleScope.launch {
            var first = true
            settingsManager.maxSilenceTimeFlow.collect { time ->
                val changed = AppConfig.Audio.RECORDING_MAX_SILENCE != time
                AppConfig.Audio.RECORDING_MAX_SILENCE = time
                if (!first && changed) restartServiceIfListening()
                first = false
            }
        }

        lifecycleScope.launch {
            var first = true
            settingsManager.preBufferLengthFlow.collect { length ->
                val changed = AppConfig.Audio.PRE_RECORDING_BUFFER_LENGTH_MS != length
                AppConfig.Audio.PRE_RECORDING_BUFFER_LENGTH_MS = length
                if (!first && changed) restartServiceIfListening()
                first = false
            }
        }
    }

    private fun restartServiceIfListening() {
        val service = currentService ?: return
        if (service.isListening) {
            service.endListening()
            service.startListening()
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
        findViewById<ToggleButton>(R.id.listeningToggle).setOnClickListener {
            currentService?.toggle()
        }

        findViewById<View>(R.id.settingsButton).setOnClickListener {
            startActivity(Intent(this, SettingsActivity::class.java))
        }

        findViewById<Button>(R.id.selectAll).setOnClickListener {
            val parent = findViewById<LinearLayout>(R.id.recordingLinearLayout)
            val anyUnchecked = parent.children.any { child ->
                !child.findViewById<CheckBox>(R.id.recordingCheckbox).isChecked
            }

            parent.children.forEach { child ->
                child.findViewById<CheckBox>(R.id.recordingCheckbox).isChecked = anyUnchecked
            }
            updateSelectAllButtonText()
        }

        findViewById<Button>(R.id.deleteButton).setOnClickListener {
            val parent = findViewById<LinearLayout>(R.id.recordingLinearLayout)
            val toDelete = parent.children.filter { child ->
                child.findViewById<CheckBox>(R.id.recordingCheckbox).isChecked
            }.toList()

            if (toDelete.isEmpty()) return@setOnClickListener

            val count = toDelete.size
            var totalDuration = 0.0
            toDelete.forEach { child ->
                val folderName = (child.tag as? Recording)?.name
                totalDuration += displayedRecordings[folderName]?.duration ?: 0.0
            }

            val durationText = formatDuration(totalDuration)
            
            val message = if (count == 1) {
                "Do you want to delete this recording with a length of $durationText?"
            } else {
                "Do you want to delete $count recordings with a total length of $durationText?"
            }

            androidx.appcompat.app.AlertDialog.Builder(this)
                .setTitle(if (count == 1) "Delete Recording" else "Delete Recordings")
                .setMessage(message)
                .setPositiveButton("Delete") { _, _ ->
                    toDelete.forEach { child ->
                        deleteRecording(child)
                    }
                }
                .setNegativeButton("Cancel", null)
                .show()
        }

        findViewById<Button>(R.id.uploadButton).setOnClickListener {
            val selectedRecordingViews = findViewById<LinearLayout>(R.id.recordingLinearLayout).children.filter {
                val checkbox = it.findViewById<CheckBox>(R.id.recordingCheckbox)
                checkbox.isChecked
            }.toList()

            for (recView in selectedRecordingViews) {
                lifecycleScope.launch(Dispatchers.IO) {
                    val recording: Recording = recView.tag as Recording
                    val progressBar = recView.findViewById<View>(R.id.uploadProgressBar)

                    withContext(Dispatchers.Main) {
                        recView.findViewById<CheckBox>(R.id.recordingCheckbox).isEnabled = false
                        progressBar.layoutParams.width = 0
                        recView.findViewById<View>(R.id.uploadProgressBar).requestLayout()
                    }

                    val result = viewModel.httpCommunicationService.sendRecording(recording) { current, total ->
                        lifecycleScope.launch(Dispatchers.Main) {
                            val progress = current.toFloat() / total
                            val progressBar = recView.findViewById<View>(R.id.uploadProgressBar)
                            progressBar.layoutParams.width = (recView.width * progress).toInt()
                            progressBar.requestLayout()
                        }
                    }

                    withContext(Dispatchers.Main) {
                        if (result.startsWith("Failure") || result.startsWith("Error")) {
                            Toast.makeText(this@MainActivity, result, Toast.LENGTH_SHORT).show()
                            recView.findViewById<CheckBox>(R.id.recordingCheckbox).isEnabled = true
                            progressBar.layoutParams.width = 0
                            recView.findViewById<View>(R.id.uploadProgressBar).requestLayout()
                        } else {
                            // Ensure full green
                            val progressBar = recView.findViewById<View>(R.id.uploadProgressBar)
                            progressBar.layoutParams.width = recView.width
                            progressBar.requestLayout()

                            Toast.makeText(
                                this@MainActivity,
                                "Upload succeeded: ${recording.name}. Deleting in 5s.",
                                Toast.LENGTH_SHORT
                            ).show()

                            launch {
                                delay(5.seconds)
                                deleteRecording(recView)
                            }
                        }
                        updateSelectAllButtonText()
                    }
                }
            }
        }
    }

    private fun formatDuration(duration: Double): String {
        val totalSeconds = duration.toLong()
        val hours = totalSeconds / 3600
        val mins = (totalSeconds % 3600) / 60
        val secs = totalSeconds % 60
        return if (hours > 0) {
            String.format(java.util.Locale.getDefault(), "%d:%02d:%02d", hours, mins, secs)
        } else {
            String.format(java.util.Locale.getDefault(), "%02d:%02d", mins, secs)
        }
    }

    private fun updateSelectAllButtonText() {
        val parent = findViewById<LinearLayout>(R.id.recordingLinearLayout)
        val selectAllButton = findViewById<Button>(R.id.selectAll)

        val anyUnchecked = parent.children.any { child ->
            !child.findViewById<CheckBox>(R.id.recordingCheckbox).isChecked
        }

        if (anyUnchecked || parent.children.none()) {
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
        if (!displayedRecordings.containsKey(recording.name)) {
            displayedRecordings[recording.name] = recording
            val view = recording.getView(this, parent)
            view.findViewById<CheckBox>(R.id.recordingCheckbox).setOnClickListener {
                updateSelectAllButtonText()
            }
            parent.addView(view)
        }
    }

    fun deleteRecording(recordingView: View) {
        // We use the 'tag' we set in Recording.getView to get the exact folder name
        val folderName = (recordingView.tag as? Recording)?.name
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
