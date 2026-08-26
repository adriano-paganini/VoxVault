package com.paganini.voxvault.ViewModel

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import com.paganini.voxvault.service.ListeningService

class MainViewModel(application: Application) : AndroidViewModel(application) {
    val listeningService = ListeningService(application.applicationContext)
}