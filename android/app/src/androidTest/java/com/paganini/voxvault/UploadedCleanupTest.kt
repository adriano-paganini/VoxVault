package com.paganini.voxvault

import android.graphics.Bitmap
import android.widget.Button
import androidx.test.core.app.ActivityScenario
import androidx.test.espresso.Espresso.onView
import androidx.test.espresso.action.ViewActions.click
import androidx.test.espresso.assertion.ViewAssertions.matches
import androidx.test.espresso.matcher.ViewMatchers.*
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.paganini.voxvault.dataClass.Recording
import com.paganini.voxvault.upload.UploadPhase
import com.paganini.voxvault.upload.UploadQueue
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import org.hamcrest.Matchers.not
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File

@RunWith(AndroidJUnit4::class)
class UploadedCleanupTest {
    @Test fun visibleBulkActionConfirmsDeletionAndPreservesUnsuccessfulRecordings() = runBlocking<Unit> {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val context = instrumentation.targetContext
        val queue = UploadQueue.get(context)
        assumeTrue("Cleanup UI tests require an empty fixture queue", queue.state.first().entries.isEmpty())
        val phases = listOf(UploadPhase.COMPLETED, UploadPhase.COMPLETED, UploadPhase.FAILED, UploadPhase.CANCELLED, UploadPhase.UPLOADING)
        val names = phases.indices.map { "cleanup-test-${System.nanoTime()}-$it" }
        ActivityScenario.launch(MainActivity::class.java).use { scenario ->
            try {
                names.forEachIndexed { index, name ->
                    val folder = File(context.filesDir, "recordings/$name").apply { mkdirs() }
                    File(folder, "metadata.json").writeText(Json.encodeToString(Recording(name, "wrapped-key", 60.0, 1700000000000 + index * 1000L)))
                    File(folder, "chunk_001.pcm").writeBytes(byteArrayOf(1, 2, 3))
                    queue.enqueue(listOf(name), "http://localhost:1/upload")
                    queue.update(name) { it.copy(phase = phases[index]) }
                }
                scenario.onActivity { activity ->
                    androidx.lifecycle.ViewModelProvider(activity)[com.paganini.voxvault.viewModel.MainViewModel::class.java].refreshRecordings()
                }
                withTimeout(5000) {
                    while (true) {
                        var ready = false
                        scenario.onActivity { ready = it.findViewById<Button>(R.id.deleteUploadedButton).text == "Delete uploaded (2)" }
                        if (ready) break
                        delay(25)
                    }
                }
                onView(withId(R.id.deleteUploadedButton)).check(matches(isDisplayed()))
                onView(withId(R.id.deleteUploadedButton)).perform(click())
                onView(withText(R.string.delete_uploaded_title)).check(matches(isDisplayed()))
                instrumentation.uiAutomation.takeScreenshot()?.let { screenshot ->
                    File(context.externalCacheDir ?: context.cacheDir, "delete-uploaded-confirmation.png").outputStream().use {
                        screenshot.compress(Bitmap.CompressFormat.PNG, 100, it)
                    }
                    screenshot.recycle()
                }
                onView(withId(android.R.id.button2)).perform(click())
                names.forEach { assertTrue(File(context.filesDir, "recordings/$it/chunk_001.pcm").exists()) }
                onView(withId(R.id.deleteUploadedButton)).perform(click())
                onView(withId(android.R.id.button1)).perform(click())
                withTimeout(5000) { queue.state.first { it.entries.none { entry -> entry.name in names.take(2) } } }
                names.take(2).forEach { assertFalse(File(context.filesDir, "recordings/$it").exists()) }
                names.drop(2).forEach { assertTrue(File(context.filesDir, "recordings/$it/chunk_001.pcm").exists()) }
                onView(withId(R.id.deleteUploadedButton)).check(matches(not(isEnabled())))
            } finally {
                queue.cancelPending()
                names.forEach {
                    queue.forget(it)
                    File(context.filesDir, "recordings/$it").deleteRecursively()
                }
            }
        }
    }
}
