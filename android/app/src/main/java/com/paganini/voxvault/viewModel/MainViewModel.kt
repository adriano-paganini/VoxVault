package com.paganini.voxvault.viewModel

import android.app.Application
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.os.IBinder
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.LiveData
import androidx.lifecycle.MutableLiveData
import androidx.lifecycle.viewModelScope
import com.paganini.voxvault.SettingsManager
import com.paganini.voxvault.dataClass.Recording
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch
import com.paganini.voxvault.upload.UploadCleanupResult
import com.paganini.voxvault.upload.UploadQueue
import com.paganini.voxvault.service.ListeningService
import com.paganini.voxvault.service.RecordingFileReaderService
import java.io.File

class MainViewModel(application: Application) : AndroidViewModel(application) {
    
    private val _listeningService = MutableLiveData<ListeningService?>()
    val listeningService: LiveData<ListeningService?> = _listeningService

    private val _recordings = MutableLiveData<List<Recording>>()
    val recordings: LiveData<List<Recording>> = _recordings

    enum class SortOrder { DATE, DURATION }
    var currentSortOrder = SortOrder.DATE
        private set

    private val settingsManager = SettingsManager(application)

    init {
        viewModelScope.launch {
            settingsManager.sortOrderFlow.collect { orderName ->
                currentSortOrder = try { SortOrder.valueOf(orderName) } catch (e: Exception) { SortOrder.DATE }
                _recordings.value?.let {
                    _recordings.value = sortList(it)
                }
            }
        }
    }

    var recordingFileReaderService = RecordingFileReaderService(application)

    fun refreshRecordings() {
        val list = recordingFileReaderService.getAllRecordings()
        _recordings.value = sortList(list)
    }

    fun setSortOrder(order: SortOrder) {
        viewModelScope.launch {
            settingsManager.updateSortOrder(order.name)
        }
    }

    private fun sortList(list: List<Recording>): List<Recording> {
        return when (currentSortOrder) {
            SortOrder.DATE -> list.sortedByDescending { it.timestamp }
            SortOrder.DURATION -> list.sortedByDescending { it.duration }
        }
    }

    val uploadQueue = UploadQueue.get(application)
    val uploadState = uploadQueue.state

    private val _deletingUploaded = MutableLiveData(false)
    val deletingUploaded: LiveData<Boolean> = _deletingUploaded
    private val _uploadCleanupResult = MutableLiveData<UploadCleanupResult?>()
    val uploadCleanupResult: LiveData<UploadCleanupResult?> = _uploadCleanupResult

    fun deleteUploadedRecordings(names: Set<String>) {
        if (_deletingUploaded.value == true) return
        _deletingUploaded.value = true
        viewModelScope.launch {
            try {
                _uploadCleanupResult.value = uploadQueue.deleteUploaded(
                    File(getApplication<Application>().filesDir, "recordings"), names,
                )
            } catch (error: CancellationException) {
                throw error
            } catch (error: Exception) {
                _uploadCleanupResult.value = UploadCleanupResult(error = error.message ?: "Could not delete recordings")
            } finally {
                refreshRecordings()
                _deletingUploaded.value = false
            }
        }
    }

    fun clearUploadCleanupResult() { _uploadCleanupResult.value = null }

    private val serviceConnection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName?, service: IBinder?) {
            val binder = service as ListeningService.LocalBinder
            _listeningService.value = binder.getService()
        }

        override fun onServiceDisconnected(name: ComponentName?) {
            _listeningService.value = null
        }
    }

    fun bindService() {
        val intent = Intent(getApplication(), ListeningService::class.java)
        getApplication<Application>().bindService(intent, serviceConnection, Context.BIND_AUTO_CREATE)
    }

    fun unbindService() {
        getApplication<Application>().unbindService(serviceConnection)
        _listeningService.value = null
    }

    override fun onCleared() {
        super.onCleared()
        // We usually don't unbind here if we want it to stay alive, 
        // but for this architecture, we'll let the Activity handle it.
    }
}
