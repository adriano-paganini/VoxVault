package com.paganini.voxvault

import android.location.Location
import android.os.Bundle
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.ToggleButton
import androidx.appcompat.app.AppCompatActivity
import androidx.activity.enableEdgeToEdge
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import com.paganini.voxvault.dataClass.Recording
import com.paganini.voxvault.service.RecordingService
import java.util.Date

class MainActivity : AppCompatActivity() {

    val recordingService = RecordingService()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        setContentView(R.layout.activity_main)
        ViewCompat.setOnApplyWindowInsetsListener(findViewById(R.id.main)){ v, insets ->
            val systemBars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            v.setPadding(systemBars.left,systemBars.top,systemBars.right,systemBars.bottom)
            insets
        }

        recordingService.onRecordingStart = {
            addTestRecordings()
        }

        recordingService.onRecordingEnd = {
            clearTestRecordings()
        }

        val recordToggleButton = findViewById<ToggleButton>(R.id.record_toggle)
        recordToggleButton.setOnClickListener{
            recordingService.toggle()
        }
    }

    fun clearTestRecordings(){
        val parent = findViewById<LinearLayout>(R.id.recordingLinearLayout)
        parent.removeAllViews()
    }

    fun addTestRecordings(){
        val recordings = listOf(
            Recording(Date(), Date(), Location("gps"), "Test1"),
            Recording(Date(), Date(), Location("gps"), "Test2"),
            Recording(Date(), Date(), Location("gps"), "Test3"),
            Recording(Date(), Date(), Location("gps"), "Test4"),
            Recording(Date(), Date(), Location("gps"), "Test5"),
            Recording(Date(), Date(), Location("gps"), "Test6"),
            Recording(Date(), Date(), Location("gps"), "Test7"),
            Recording(Date(), Date(), Location("gps"), "Test1"),
            Recording(Date(), Date(), Location("gps"), "Test2"),
            Recording(Date(), Date(), Location("gps"), "Test3"),
            Recording(Date(), Date(), Location("gps"), "Test4"),
            Recording(Date(), Date(), Location("gps"), "Test5"),
            Recording(Date(), Date(), Location("gps"), "Test6"),
            Recording(Date(), Date(), Location("gps"), "Test7"),
            Recording(Date(), Date(), Location("gps"), "Test1"),
            Recording(Date(), Date(), Location("gps"), "Test2"),
            Recording(Date(), Date(), Location("gps"), "Test3"),
            Recording(Date(), Date(), Location("gps"), "Test4"),
            Recording(Date(), Date(), Location("gps"), "Test5"),
            Recording(Date(), Date(), Location("gps"), "Test6"),
            Recording(Date(), Date(), Location("gps"), "Test7"),
            Recording(Date(), Date(), Location("gps"), "Test8")
        )

        for (recording in recordings){
            addToScrollableList(recording)
        }
    }

    fun addToScrollableList(recording: Recording){
        val parent = findViewById<LinearLayout>(R.id.recordingLinearLayout)

        val text = TextView(this)
        text.text = recording.title
        text.setPadding(0,100,0,0)

        parent.addView(text)

    }

}

