package com.paganini.voxvault

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.longPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

val Context.dataStore: DataStore<Preferences> by preferencesDataStore(name = "settings")

class SettingsManager(private val context: Context) {

    companion object {
        val BACKEND_URL = stringPreferencesKey("backend_url")
        val MAX_SILENCE_TIME = longPreferencesKey("max_silence_time")
        val PRE_BUFFER_LENGTH = longPreferencesKey("pre_buffer_length")

        // Default values corresponding to AppConfig
        const val DEFAULT_BACKEND_URL = "<<url_placeholder>>"
        const val DEFAULT_MAX_SILENCE_TIME = 10000L
        const val DEFAULT_PRE_BUFFER_LENGTH = 1000L
    }

    val backendUrlFlow: Flow<String> = context.dataStore.data.map { preferences ->
        preferences[BACKEND_URL] ?: DEFAULT_BACKEND_URL
    }

    val maxSilenceTimeFlow: Flow<Long> = context.dataStore.data.map { preferences ->
        preferences[MAX_SILENCE_TIME] ?: DEFAULT_MAX_SILENCE_TIME
    }

    val preBufferLengthFlow: Flow<Long> = context.dataStore.data.map { preferences ->
        preferences[PRE_BUFFER_LENGTH] ?: DEFAULT_PRE_BUFFER_LENGTH
    }

    suspend fun updateBackendUrl(url: String) {
        context.dataStore.edit { preferences ->
            preferences[BACKEND_URL] = url
        }
    }

    suspend fun updateMaxSilenceTime(time: Long) {
        context.dataStore.edit { preferences ->
            preferences[MAX_SILENCE_TIME] = time
        }
    }

    suspend fun updatePreBufferLength(length: Long) {
        context.dataStore.edit { preferences ->
            preferences[PRE_BUFFER_LENGTH] = length
        }
    }
}
