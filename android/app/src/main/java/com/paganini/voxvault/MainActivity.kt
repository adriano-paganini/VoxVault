package com.paganini.voxvault

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.CheckBox
import android.widget.LinearLayout
import android.widget.Toast
import android.widget.TextView
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.appcompat.widget.TooltipCompat
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.children
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.lifecycleScope
import com.google.android.material.button.MaterialButton
import com.google.android.material.button.MaterialButtonToggleGroup
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

        // Recording Observation
        setupRecordingObservation()
        viewModel.refreshRecordings()

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
        viewModel.refreshRecordings()
    }

    override fun onStop() {
        super.onStop()
        currentService?.speakingListener = null
        currentService?.onRecordingCompleted = null
        viewModel.unbindService()
    }
    // endregion

    // region Service Logic
    private fun setupServiceObservation() {
        val listeningToggleButton = findViewById<MaterialButton>(R.id.listeningToggle)

        viewModel.listeningService.observe(this) { service ->
            currentService = service
            if (service != null) {
                // Initial Sync
                syncUIWithService(service, listeningToggleButton)

                // Live Updates
                service.speakingListener = {
                    runOnUiThread {
                        syncUIWithService(service, listeningToggleButton)
                    }
                }
                
                service.onRecordingCompleted = {
                    runOnUiThread {
                        viewModel.refreshRecordings()
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
            settingsManager.backendPortFlow.collect { port ->
                AppConfig.Web.BACKEND_PORT = port
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

        lifecycleScope.launch {
            settingsManager.encryptionPublicKeyFlow.collect { key ->
                AppConfig.Encryption.ENCRYPTION_PUBLIC_KEY = key
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

    private fun setupRecordingObservation() {
        viewModel.recordings.observe(this) { recordings ->
            val parent = findViewById<LinearLayout>(R.id.recordingLinearLayout)
            val existing = parent.children.associateBy { (it.tag as Recording).name }
            parent.removeAllViews()
            displayedRecordings.clear()
            recordings.forEach { r ->
                val row = existing[r.name]
                if (row == null) addToScrollableList(r) else {
                    displayedRecordings[r.name] = r
                    parent.addView(row)
                }
            }
            findViewById<MaterialButtonToggleGroup>(R.id.sortGroup).check(
                if (viewModel.currentSortOrder == MainViewModel.SortOrder.DATE) R.id.sortByDate else R.id.sortByDuration
            )
            updateSelectAllButtonText()
        }
    }

    private fun syncUIWithService(service: ListeningService, toggle: MaterialButton) {
        toggle.setText(if (service.isListening) R.string.listening_on else R.string.listening_off)
        toggle.setIconResource(if (service.isListening) android.R.drawable.ic_media_pause else android.R.drawable.ic_btn_speak_now)
        findViewById<TextView>(R.id.listeningStatus).setText(when {
            !service.isListening -> R.string.listening_idle
            service.sharedSpeaking != 0 -> R.string.speech_active
            else -> R.string.listening_active
        })
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
        for (id in listOf(R.id.settingsButton, R.id.uploadButton, R.id.deleteButton)) {
            val button = findViewById<View>(id)
            TooltipCompat.setTooltipText(button, button.contentDescription)
        }
        val listeningToggle = findViewById<MaterialButton>(R.id.listeningToggle)
        listeningToggle.setOnClickListener {
            if (!hasCriticalPermissions()) {
                requestAppPermissions()
                return@setOnClickListener
            }
            if (currentService?.isListening != true && AppConfig.Encryption.ENCRYPTION_PUBLIC_KEY.isEmpty()){
                Toast.makeText(
                    this@MainActivity,
                    "Please setup the public encryption Key before recording!",
                    Toast.LENGTH_SHORT
                ).show()
                return@setOnClickListener
            }
            currentService?.let {
                it.toggle()
                syncUIWithService(it, listeningToggle)
            }
        }

        findViewById<View>(R.id.settingsButton).setOnClickListener {
            startActivity(Intent(this, SettingsActivity::class.java))
        }

        findViewById<View>(R.id.archiveButton).setOnClickListener {
            startActivity(Intent(this, ExplorerActivity::class.java))
        }

        findViewById<Button>(R.id.sortByDate).setOnClickListener {
            viewModel.setSortOrder(MainViewModel.SortOrder.DATE)
        }

        findViewById<Button>(R.id.sortByDuration).setOnClickListener {
            viewModel.setSortOrder(MainViewModel.SortOrder.DURATION)
        }

        findViewById<CheckBox>(R.id.selectAll).setOnClickListener {
            val parent = findViewById<LinearLayout>(R.id.recordingLinearLayout)
            val anyUnchecked = parent.children.any { child ->
                child.findViewById<CheckBox>(R.id.recordingCheckbox).let { it.isEnabled && !it.isChecked }
            }

            parent.children.forEach { child ->
                child.findViewById<CheckBox>(R.id.recordingCheckbox).let { if (it.isEnabled) it.isChecked = anyUnchecked }
            }
            updateSelectAllButtonText()
        }

        findViewById<View>(R.id.deleteButton).setOnClickListener {
            val parent = findViewById<LinearLayout>(R.id.recordingLinearLayout)
            val toDelete = parent.children.filter { child ->
                child.findViewById<CheckBox>(R.id.recordingCheckbox).let { it.isEnabled && it.isChecked }
            }.toList()

            if (toDelete.isEmpty()){
                Toast.makeText(
                    this@MainActivity,
                    "Please select at least one recording to delete.",
                    Toast.LENGTH_SHORT
                ).show()
                return@setOnClickListener
            }

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

        findViewById<View>(R.id.uploadButton).setOnClickListener {
            if (AppConfig.Web.BACKEND_ADDRESS.isEmpty()) {
                Toast.makeText(
                    this@MainActivity,
                    "Please update the Backend URL in the settings.",
                    Toast.LENGTH_SHORT
                ).show()
                return@setOnClickListener
            }
            val selectedRecordingViews =
                findViewById<LinearLayout>(R.id.recordingLinearLayout).children.filter {
                    val checkbox = it.findViewById<CheckBox>(R.id.recordingCheckbox)
                    checkbox.isEnabled && checkbox.isChecked
                }.toList()

            if (selectedRecordingViews.isEmpty()) {
                Toast.makeText(
                    this@MainActivity,
                    "Please select at least one recording to upload",
                    Toast.LENGTH_SHORT
                ).show()
                return@setOnClickListener
            }

            for (recView in selectedRecordingViews) {
                recView.findViewById<CheckBox>(R.id.recordingCheckbox).isEnabled = false
                updateSelectAllButtonText()
                lifecycleScope.launch(Dispatchers.IO) {
                    val recording: Recording = recView.tag as Recording
                    val progressBar = recView.findViewById<View>(R.id.uploadProgressBar)

                    withContext(Dispatchers.Main) {
                        recView.findViewById<CheckBox>(R.id.recordingCheckbox).isEnabled = false
                        progressBar.layoutParams.width = 0
                        recView.findViewById<View>(R.id.uploadProgressBar).requestLayout()
                    }

                    val result =
                        viewModel.httpCommunicationService.sendRecording(recording) { current, total ->
                            lifecycleScope.launch(Dispatchers.Main) {
                                val progress = current.toFloat() / total
                                val progressBar =
                                    recView.findViewById<View>(R.id.uploadProgressBar)
                                progressBar.layoutParams.width =
                                    (recView.width * progress).toInt()
                                progressBar.requestLayout()
                            }
                        }

                    withContext(Dispatchers.Main) {
                        if (result.startsWith("Failure") || result.startsWith("Error")) {
                            Toast.makeText(this@MainActivity, result, Toast.LENGTH_SHORT).show()
                            recView.findViewById<CheckBox>(R.id.recordingCheckbox).isEnabled =
                                true
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
        val available = parent.children.map { it.findViewById<CheckBox>(R.id.recordingCheckbox) }
            .filter { it.isEnabled }.toList()
        val selected = available.count { it.isChecked }
        findViewById<CheckBox>(R.id.selectAll).apply {
            isEnabled = available.isNotEmpty()
            isChecked = available.isNotEmpty() && selected == available.size
            text = if (selected > 0) getString(R.string.selected_recordings, selected) else getString(R.string.select_all)
        }
        for (id in listOf(R.id.uploadButton, R.id.deleteButton)) findViewById<View>(id).apply {
            isEnabled = selected > 0
            alpha = if (isEnabled) 1f else 0.35f
        }
        findViewById<View>(R.id.emptyRecordings).visibility = if (parent.childCount == 0) View.VISIBLE else View.GONE
        findViewById<TextView>(R.id.recordingSummary).text = getString(
            R.string.recording_summary, parent.childCount, formatDuration(displayedRecordings.values.sumOf { it.duration })
        )
    }
    // endregion

    // region Permissions
    private val requestMultiplePermissionsLauncher = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { _ ->
        if (hasCriticalPermissions()) {
            startListeningService()
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
