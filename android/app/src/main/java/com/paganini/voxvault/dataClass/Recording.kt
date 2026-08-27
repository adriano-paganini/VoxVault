package com.paganini.voxvault.dataClass

import android.location.Location
import android.widget.TextView
import com.paganini.voxvault.AppConfig
import com.paganini.voxvault.MainActivity
import java.util.Date

data class Recording(
    val startDate: Date,
    var endDate: Date,
    var location: Location,
    var title: String
){
    fun textView(activity: MainActivity): TextView {
        val text = TextView(activity)
        text.text = this.title
        text.setPadding(0, AppConfig.UI.LIST_ITEM_PADDING_TOP, 0, 0)
        return text
    }
}
