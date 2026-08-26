package com.paganini.voxvault

import android.Manifest
import android.content.pm.PackageManager
import android.location.Location
import android.os.Bundle
import android.widget.LinearLayout
import android.widget.ToggleButton
import androidx.appcompat.app.AppCompatActivity
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import com.paganini.voxvault.dataClass.Recording
import com.paganini.voxvault.service.ListeningService
import java.security.Permission
import java.util.Date

class MainActivity : AppCompatActivity() {

    val listeningService = ListeningService()

    private val requestPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ){
        isGranted: Boolean ->
        if (!isGranted) {
            finish()
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        setContentView(R.layout.activity_main)
        ViewCompat.setOnApplyWindowInsetsListener(findViewById(R.id.main)){ v, insets ->
            val systemBars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            v.setPadding(systemBars.left,systemBars.top,systemBars.right,systemBars.bottom)
            insets
        }

        val listeningToggleButton = findViewById<ToggleButton>(R.id.listeningToggle)
        listeningToggleButton.setOnClickListener{
            listeningService.toggle()
        }

        checkAndRequestPermission(Manifest.permission.RECORD_AUDIO,true)
    }

    private fun checkAndRequestPermission(permission: String, hardRequirement:Boolean) {
        when {
            ContextCompat.checkSelfPermission(this, permission)
                    == PackageManager.PERMISSION_GRANTED -> {
            }
            shouldShowRequestPermissionRationale(permission) -> {
            }
            else -> {
                requestPermissionLauncher.launch(permission)
            }
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
        parent.addView(recording.textView(this))

    }

}

