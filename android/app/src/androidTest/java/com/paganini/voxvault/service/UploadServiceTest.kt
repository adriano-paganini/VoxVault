package com.paganini.voxvault.service

import android.app.ActivityManager
import android.app.Notification
import android.app.NotificationManager
import android.os.ParcelFileDescriptor
import androidx.test.core.app.ActivityScenario
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.paganini.voxvault.MainActivity
import com.paganini.voxvault.dataClass.Recording
import com.paganini.voxvault.upload.UploadPhase
import com.paganini.voxvault.upload.UploadQueue
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import mockwebserver3.Dispatcher
import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import mockwebserver3.RecordedRequest
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File
import java.io.RandomAccessFile
import java.util.Collections
import java.util.concurrent.TimeUnit

@RunWith(AndroidJUnit4::class)
class UploadServiceTest {
    @Suppress("DEPRECATION")
    @Test fun ninetyOneChunksSurviveActivityRecreationDestructionAndScreenLock() = runBlocking {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val context = instrumentation.targetContext
        val queue = UploadQueue.get(context)
        assumeTrue("Do not interfere with existing uploads", queue.state.first().entries.none { it.pending })
        val names = listOf("upload-test-${System.nanoTime()}-A", "upload-test-${System.nanoTime()}-B")
        val counts = listOf(91, 3)
        val received = mutableMapOf<Long, MutableSet<Int>>()
        val requests = Collections.synchronizedList(mutableListOf<Pair<Long, Int>>())
        val times = Collections.synchronizedList(mutableListOf<Long>())
        var failedOnce = false
        var scenario: ActivityScenario<MainActivity>? = null
        fun shell(command: String) {
            ParcelFileDescriptor.AutoCloseInputStream(instrumentation.uiAutomation.executeShellCommand(command)).use { it.readBytes() }
        }
        MockWebServer().use { server ->
            // The fixture server retains only JSON metadata, never the 116 MB workload.
            server.bodyLimit = 512
            server.dispatcher = object : Dispatcher() {
                override fun dispatch(request: RecordedRequest): MockResponse {
                    if (request.method == "GET") {
                        val timestamp = request.url.pathSegments.last().toLong()
                        val status = RemoteUploadStatus(receivedChunks = received[timestamp]?.toSet() ?: emptySet())
                        return MockResponse.Builder().body(Json.encodeToString(status)).build()
                    }
                    val metadata = Json.parseToJsonElement(request.body!!.utf8().substringBefore(",\"data\":") + "}").jsonObject
                    val timestamp = metadata.getValue("timestamp").jsonPrimitive.content.toLong()
                    val index = metadata.getValue("chunkIndex").jsonPrimitive.int
                    val total = metadata.getValue("totalChunks").jsonPrimitive.int
                    requests.add(timestamp to index)
                    times.add(System.nanoTime())
                    if (index == 72 && !failedOnce) {
                        failedOnce = true
                        return MockResponse.Builder().code(503).build()
                    }
                    val indexes = received.getOrPut(timestamp) { mutableSetOf() }
                    indexes.add(index)
                    return MockResponse.Builder().body("{\"recordingComplete\":${indexes.size == total}}")
                        .headersDelay(100, TimeUnit.MILLISECONDS).build()
                }
            }
            server.start()
            try {
                names.forEachIndexed { position, name ->
                    val folder = File(context.filesDir, "recordings/$name").apply { mkdirs() }
                    File(folder, "metadata.json").writeText(Json.encodeToString(Recording(name, "wrapped-key", 3600.0, 9000L + position)))
                    repeat(counts[position]) { index ->
                        RandomAccessFile(File(folder, "chunk_${(index + 1).toString().padStart(3, '0')}.pcm"), "rw")
                            .use { it.setLength(1280000) }
                    }
                }
                shell("pm grant ${context.packageName} android.permission.RECORD_AUDIO")
                shell("pm grant ${context.packageName} android.permission.POST_NOTIFICATIONS")
                shell("input keyevent KEYCODE_WAKEUP")
                shell("wm dismiss-keyguard")
                scenario = ActivityScenario.launch(MainActivity::class.java)
                queue.enqueue(names, server.url("/upload").toString())
                scenario.onActivity { UploadService.resume(it) }
                queue.enqueue(names + names, server.url("/upload").toString())
                withTimeout(20000) { queue.state.first { it.entries.first { entry -> entry.name == names[0] }.uploaded.size >= 3 } }
                scenario.recreate()
                scenario.close()
                scenario = null
                shell("input keyevent KEYCODE_HOME")
                shell("input keyevent KEYCODE_SLEEP")
                val before = queue.state.first().entries.first { it.name == names[0] }.uploaded.size
                withTimeout(20000) { queue.state.first { it.entries.first { entry -> entry.name == names[0] }.uploaded.size >= before + 3 } }
                val notification = context.getSystemService(NotificationManager::class.java).activeNotifications
                    .first { it.id == 2002 }.notification
                assertTrue(notification.flags and Notification.FLAG_ONGOING_EVENT != 0)
                assertTrue(notification.extras.getString(Notification.EXTRA_TEXT)!!.contains("Chunk"))
                withTimeout(60000) {
                    queue.state.first { journal -> names.all { name -> journal.entries.any { it.name == name && it.phase == UploadPhase.COMPLETED } } }
                }
                val expected = (0 until 91).map { 9000L to it }.toMutableList().apply { add(73, 9000L to 72) } +
                    (0 until 3).map { 9001L to it }
                assertEquals(expected, requests.toList())
                assertEquals(91, received[9000L]!!.size)
                assertTrue(times.zipWithNext().all { (previous, next) -> next - previous >= TimeUnit.MILLISECONDS.toNanos(90) })
                withTimeout(5000) {
                    while (context.getSystemService(ActivityManager::class.java).getRunningServices(100)
                            .any { it.service.className == UploadService::class.java.name }) delay(50)
                }
            } finally {
                shell("input keyevent KEYCODE_WAKEUP")
                shell("wm dismiss-keyguard")
                scenario?.close()
                if (queue.state.first().entries.any { it.name in names && it.pending }) {
                    UploadService.cancel(context)
                    withTimeout(5000) { queue.state.first { it.entries.none { entry -> entry.name in names && entry.pending } } }
                }
                names.forEach { name ->
                    queue.forget(name)
                    File(context.filesDir, "recordings/$name").deleteRecursively()
                }
            }
        }
    }
}
