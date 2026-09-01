package com.paganini.voxvault.dataClass

import android.content.Context
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import com.paganini.voxvault.Chunker
import com.paganini.voxvault.R
import kotlinx.serialization.InternalSerializationApi
import kotlinx.serialization.Serializable
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

@OptIn(InternalSerializationApi::class)
@Serializable
data class Recording(
    var name: String = "",
    var duration: Double = 0.0,
    val timestamp: Long = 0L,
) {

    fun getChunker(filesDir: java.io.File): Chunker {
        return Chunker(this, filesDir)
    }
    fun getView(context: Context, parent: ViewGroup? = null): View {
        val inflater = LayoutInflater.from(context)
        val view = inflater.inflate(R.layout.item_recording, parent, false)
        view.tag = this

        
        val dateView = view.findViewById<TextView>(R.id.recordingDate)
        val timeView = view.findViewById<TextView>(R.id.recordingTime)
        val durationView = view.findViewById<TextView>(R.id.recordingDuration)
        
        val date = Date(timestamp)
        
        // Date: MMM dd, yyyy
        val dateSdf = SimpleDateFormat("MMM dd, yyyy", Locale.getDefault())
        dateView.text = dateSdf.format(date)
        
        // Time: HH:mm
        val timeSdf = SimpleDateFormat("HH:mm", Locale.getDefault())
        timeView.text = timeSdf.format(date)
        
        // Duration: handles H:mm:ss or mm:ss
        val totalSeconds = duration.toLong()
        val hours = totalSeconds / 3600
        val mins = (totalSeconds % 3600) / 60
        val secs = totalSeconds % 60
        
        durationView.text = if (hours > 0) {
            String.format(Locale.getDefault(), "%d:%02d:%02d", hours, mins, secs)
        } else {
            String.format(Locale.getDefault(), "%02d:%02d", mins, secs)
        }
        
        return view
    }
}