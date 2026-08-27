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
import com.paganini.voxvault.service.ListeningService

class MainViewModel(application: Application) : AndroidViewModel(application) {
    
    private val _listeningService = MutableLiveData<ListeningService?>()
    val listeningService: LiveData<ListeningService?> = _listeningService

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
