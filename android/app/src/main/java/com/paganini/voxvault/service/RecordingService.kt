package com.paganini.voxvault.service

class RecordingService {
    var isRecording = false

    var onRecordingStart: (() -> Unit)? = null
    var onRecordingEnd: (() -> Unit)? = null
    fun toggle() {
        if (isRecording){
            onRecordingEnd?.invoke()
        }else{
            onRecordingStart?.invoke()
        }
        isRecording = !isRecording
    }
}