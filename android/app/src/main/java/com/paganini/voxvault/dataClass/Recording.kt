package com.paganini.voxvault.dataClass

import android.content.Context
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
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
    val latitude: Double = 0.0,
    val longitude: Double = 0.0,
    val timestamp: Long = 0L,
) {
    fun getView(context: Context, parent: ViewGroup? = null): View {
        val inflater = LayoutInflater.from(context)
        val view = inflater.inflate(R.layout.item_recording, parent, false)
        
        val timestampView = view.findViewById<TextView>(R.id.recordingTimestamp)
        val durationView = view.findViewById<TextView>(R.id.recordingDuration)
        
        // Date and Time with Year, no Day Name
        val sdf = SimpleDateFormat("MMM dd, yyyy • HH:mm", Locale.getDefault())
        timestampView.text = sdf.format(Date(timestamp))
        
        // Duration formatted as mm:ss
        val minutes = (duration / 60).toInt()
        val seconds = (duration % 60).toInt()
        durationView.text = String.format(Locale.getDefault(), "%d min %d sec", minutes, seconds)
        
        return view
    }
}