package com.paganini.voxvault.service

import androidx.test.ext.junit.runners.AndroidJUnit4
import com.paganini.voxvault.dataClass.Chunk
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import okhttp3.OkHttpClient
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import org.junit.runner.RunWith
import java.util.concurrent.TimeUnit

@RunWith(AndroidJUnit4::class)
class HttpCommunicationServiceTest {
    @get:Rule val directory = TemporaryFolder()
    private fun chunk() = Chunk(123, 1, 0, "wrapped-key", directory.newFile().apply { writeBytes(byteArrayOf(1, 2, 3)) })

    @Test fun requestsReuseConnectionAndCloseBodies() = runBlocking {
        MockWebServer().use { server ->
            server.start()
            repeat(2) { server.enqueue(MockResponse.Builder().body("{\"recordingComplete\":true}").build()) }
            val network = HttpCommunicationService()
            repeat(2) { assertEquals(true, network.sendChunk(server.url("/upload").toString(), chunk())) }
            val first = server.takeRequest(5, TimeUnit.SECONDS)!!
            val second = server.takeRequest(5, TimeUnit.SECONDS)!!
            assertEquals(first.connectionIndex, second.connectionIndex)
            assertEquals(first.exchangeIndex + 1, second.exchangeIndex)
        }
    }

    @Test fun cancellationClosesAnInFlightResponseBeforeReturning() = runBlocking {
        MockWebServer().use { server ->
            server.start()
            server.enqueue(MockResponse.Builder().body("{\"recordingComplete\":true}")
                .bodyDelay(2, TimeUnit.SECONDS).build())
            val client = OkHttpClient.Builder().retryOnConnectionFailure(false).build()
            val network = HttpCommunicationService(client)
            val worker = launch(Dispatchers.IO) { network.sendChunk(server.url("/upload").toString(), chunk()) }
            assertNotNull(server.takeRequest(5, TimeUnit.SECONDS))
            withTimeout(1000) { worker.cancelAndJoin() }
            assertTrue(worker.isCancelled)
            server.enqueue(MockResponse.Builder().body("{\"recordingComplete\":true}").build())
            assertEquals(true, network.sendChunk(server.url("/upload").toString(), chunk()))
            assertEquals(2, server.requestCount)
        }
    }

    @Test fun serviceDoesNotReplay503EvenWithRetryAfterZero() = runBlocking {
        MockWebServer().use { server ->
            server.start()
            server.enqueue(MockResponse.Builder().code(503).addHeader("Retry-After", "0").build())
            server.enqueue(MockResponse.Builder().body("{\"recordingComplete\":true}").build())
            try {
                HttpCommunicationService().sendChunk(server.url("/upload").toString(), chunk())
                fail("Expected HTTP failure")
            } catch (error: HttpFailure) {
                assertEquals(503, error.status)
            }
            assertEquals(1, server.requestCount)
        }
    }

    @Test fun oversizedResponseIsRejectedWithoutRetainingIt() = runBlocking {
        MockWebServer().use { server ->
            server.start()
            server.enqueue(MockResponse.Builder().body("x".repeat(8192)).build())
            try {
                HttpCommunicationService().sendChunk(server.url("/upload").toString(), chunk())
                fail("Expected bounded-response failure")
            } catch (error: java.io.IOException) {
                assertTrue(error.message!!.contains("exceeds"))
            }
        }
    }
}
