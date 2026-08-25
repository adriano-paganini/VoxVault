package com.paganini.voxvault.dataClass

import android.location.Location
import java.util.Date

data class Recording(
    val startDate: Date,
    var endDate: Date,
    var location: Location,
    var title: String
)
