package com.paganini.voxvault.dataClass

import android.content.Context
import android.view.View
import android.widget.TextView
import kotlinx.serialization.Serializable

@Serializable
data class Recording(
    var name: String = "",
    var duration: Double = 0.0,
    val latitude: Double = 0.0,
    val longitude: Double = 0.0,
    val timestamp: Long = 0L,
) {
    fun getView(context: Context): View {
        val textView = TextView(context)
        textView.text = String.format(
            java.util.Locale.US,
            "%.1fs - %s",
            duration,
            name
        )
        return textView
    }
}