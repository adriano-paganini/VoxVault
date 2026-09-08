package com.paganini.voxvault.service

import androidx.test.ext.junit.runners.AndroidJUnit4
import com.paganini.voxvault.dataClass.Chunk
import com.paganini.voxvault.dataClass.ConversationSearchMode
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

    @Test fun conversationRequestsPreservePrefixAndDecodeContext() = runBlocking {
        MockWebServer().use { server ->
            server.start()
            server.enqueue(MockResponse.Builder().body("""{"items":[{"id":7,"timestamp":1700000000000,"chunkCount":2,"wordCount":4,"durationMs":2000,"personCount":1,"matchedChunkIds":[9],"similarity":0.9}],"total":1,"limit":25,"offset":0}""").build())
            val network = HttpCommunicationService()
            val uploadUrl = server.url("/vault/upload").toString()
            val page = network.getConversations(uploadUrl, "budget & planning", ConversationSearchMode.CONVERSATION)
            assertEquals(7L, page.items.single().id)
            assertEquals(listOf(9L), page.items.single().matchedChunkIds)
            val search = server.takeRequest(5, TimeUnit.SECONDS)!!.url
            assertEquals("/vault/api/explorer/conversations", search.encodedPath)
            assertEquals("budget & planning", search.queryParameter("q"))
            assertEquals("conversation", search.queryParameter("mode"))
            assertEquals("all", search.queryParameter("assignment"))

            server.enqueue(MockResponse.Builder().body("""{"id":7,"timestamp":1700000000000,"chunkCount":2,"wordCount":4,"durationMs":2000,"personCount":1,"matchedChunkIds":[9],"chunks":[{"id":8,"recordingId":7,"recordingTimestamp":1700000000000,"chunkIndex":0,"text":"Some context","wordCount":2,"startMs":0,"endMs":1000},{"id":9,"recordingId":7,"recordingTimestamp":1700000000000,"chunkIndex":1,"text":"Budget planning","wordCount":2,"startMs":1000,"endMs":2000,"personId":3,"personName":"Alex","matched":true}]}""").build())
            val detail = network.getConversation(uploadUrl, 7, "budget & planning")
            assertEquals(listOf(8L, 9L), detail.chunks.map { it.id })
            assertFalse(detail.chunks.first().matched)
            assertTrue(detail.chunks.last().matched)
            assertEquals("Alex", detail.chunks.last().personName)
            val request = server.takeRequest(5, TimeUnit.SECONDS)!!.url
            assertEquals("/vault/api/explorer/conversations/7", request.encodedPath)
            assertEquals("budget & planning", request.queryParameter("q"))
        }
    }

    @Test fun speakerRequestsRetainRankingAndSerializeNullAssignment() = runBlocking {
        MockWebServer().use { server ->
            server.start()
            val network = HttpCommunicationService()
            val uploadUrl = server.url("/vault/upload").toString()
            server.enqueue(MockResponse.Builder().body("""{"items":[{"id":2,"name":"Closest","chunkCount":1,"recordingCount":1,"wordCount":2,"similarity":0.95,"hasVoiceEmbedding":true},{"id":3,"name":"Unknown","chunkCount":0,"recordingCount":0,"wordCount":0,"similarity":null}]}""").build())
            val people = network.getSpeakerSuggestions(uploadUrl, 9)
            assertEquals(listOf(2L, 3L), people.items.map { it.id })
            assertNull(people.items.last().similarity)
            assertEquals("9", server.takeRequest(5, TimeUnit.SECONDS)!!.url.queryParameter("chunk_id"))
            val chunk = """{"id":9,"recordingId":7,"recordingTimestamp":1700000000000,"chunkIndex":1,"text":"Hello","wordCount":1,"startMs":1000,"endMs":2000}"""
            server.enqueue(MockResponse.Builder().body("""{"chunk":$chunk,"person":null}""").build())
            assertNull(network.assignSpeaker(uploadUrl, 9, null).person)
            val assignment = server.takeRequest(5, TimeUnit.SECONDS)!!
            assertEquals("PUT", assignment.method)
            assertEquals("/vault/api/explorer/chunks/9/person", assignment.url.encodedPath)
            assertEquals("""{"personId":null}""", assignment.body!!.utf8())
        }
    }
}
