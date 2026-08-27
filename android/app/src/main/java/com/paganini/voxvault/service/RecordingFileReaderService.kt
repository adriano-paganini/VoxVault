package com.paganini.voxvault.service

import android.app.Application
import com.paganini.voxvault.dataClass.Recording
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import java.io.File

class RecordingFileReaderService(private val application: Application) {

    private val baseDir = File(application.filesDir, "recordings")

    fun getAllRecordings(): List<Recording> {
        if (!baseDir.exists()) {
            return emptyList()
        }

        val recordingFolders = baseDir.listFiles { f -> f.isDirectory } ?: return emptyList()

        return recordingFolders.mapNotNull { folder ->
            val metadataFile = File(folder, "metadata.json")

            if (metadataFile.exists()) {
                try {
                    val jsonText = metadataFile.readText()
                    val fieldCount = Json.parseToJsonElement(jsonText).jsonObject.size
                    if(fieldCount==5){
                        // Convert JSON string directly to Recording object
                        Json.decodeFromString<Recording>(jsonText)
                    } else {
                        null
                    }
                } catch (e: Exception) {
                    null // Skip files that fail to parse
                }
            } else {
                null
            }
        }
    }
}
