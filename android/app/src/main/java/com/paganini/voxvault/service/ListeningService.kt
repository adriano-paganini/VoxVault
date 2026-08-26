package com.paganini.voxvault.service

class ListeningService {
    var isListening = false

    fun toggle() {
        if (isListening){
            endListening()
        }else{
            startListening()
        }
        isListening = !isListening
    }

    fun startListening(){

    }

    fun endListening(){

    }
}