# Background uploads

`UploadService` owns one queue consumer. Its process-wide mutex also covers cancellation
cleanup from a previous service instance. The Activity sends recording names and observes
the journal; neither the Activity nor ViewModel owns an upload coroutine. Multiple taps
and repeated service starts do not add duplicate active recordings.

## Memory and protocol

The old path read a complete encrypted file, allocated an unused `ShortArray`, and made
Base64, serialized JSON, and UTF-8 request-body copies. It was sequential within each
recording, but launched one Activity coroutine per selected recording.

The new path is encrypted file -> 16 KiB buffer -> Android `Base64OutputStream` -> OkHttp
buffered socket. Files are opened only by the current request's `writeTo`. Base64 padding,
the wrapped encryption key, chunk indexes, and the existing `/upload` JSON format are
preserved. There are no complete audio arrays, Base64 strings, JSON payload strings, or
prebuilt requests. Tink encryption is unchanged. Payload memory stays bounded even for
one unusually large chunk. File/index metadata grows with the number of chunks, but
audio memory does not. Do not add a BODY logging interceptor to this client: it could
buffer the streaming request again.

One shared client uses 60-second connect/read/write timeouts and a five-minute whole-call
limit. Responses are closed inside `use`, before returning to the consumer. Upload
responses are limited to 4 KiB; status responses to 256 KiB. Cancellation cancels the
OkHttp call and waits for callback cleanup before releasing the consumer mutex.

## Queue and recovery

The atomic DataStore journal is `noBackupFilesDir/upload-queue.json`. It contains names,
the pinned server URL, timestamps, file names/sizes, acknowledged indexes, attempt
counts, and status. It never contains audio or keys. A success is persisted only after
a successful server response. Recording files are checked against the original manifest.

Before resuming, `GET /api/uploads/{timestamp}` reconciles server-held indexes. The
additive `recordingComplete` response field confirms that all chunks arrived, including
when a previous acknowledgment was lost. Older servers returning 404 for the status
endpoint fall back to local acknowledgments. Updating the server is recommended for
reconciliation after server restarts.

Network/timeout errors, HTTP 408/429, and 5xx get at most three attempts, with 2- and
4-second delays. Other 4xx fail immediately. Attempt counts survive process recreation.
After exhaustion that recording is marked failed and the next queued recording may run.
Restore connectivity and tap Upload to resume failed/cancelled recordings. Successful
chunks are skipped; a server restart that lost its partial recording necessarily requires
resending those lost chunks. Local files remain available for explicit deletion because
receipt is distinct from successful server transcription/storage. **Delete uploaded (N)**
on the Recordings screen removes all completed local uploads after confirmation. The
operation rechecks the durable queue under its transaction before deleting each folder;
pending, failed, and cancelled recordings are excluded. Server data is unaffected.

`START_STICKY` allows Android to recreate the service and recover its queue. Opening the
app also restarts pending work. Force-stop, reboot, device shutdown, and vendor battery
restrictions can interrupt execution; reopening the app recovers pending uploads.
No boot receiver attempts to start a data-sync foreground service.

## Android versions

Minimum SDK 24 and target/compile SDK 37 are unchanged. The manifest declares
`FOREGROUND_SERVICE`, `FOREGROUND_SERVICE_DATA_SYNC`, and `dataSync`. On Android 13+,
notification permission is needed for normal notification-drawer visibility. Starts
originate from the visible app or a notification action. A bounded partial wake lock
supports screen-off execution and is released on service destruction.

Android 15+ limits background data-sync foreground services to six hours in a 24-hour
period. `onTimeout` cancels work and stops promptly, retaining the journal. Opening the
app allows pending work to resume. See the [Android timeout documentation](https://developer.android.com/develop/background-work/services/fgs/timeout).

## Verification

Run from `android`:

```sh
./gradlew app:assembleDebug
./gradlew app:testDebugUnitTest app:connectedDebugAndroidTest
```

Tests cover 91 chunks followed by another recording, duplicate enqueueing, failure at
chunk 73, bounded retries, permanent 4xx, lost acknowledgments, server restarts, changed
files, persisted attempt budgets, cancellation, and reopening the queue from disk.
Device tests verify Base64 padding/content lengths, socket reuse, response limits,
real HTTP cancellation, and prevention of OkHttp's implicit 503 replay.

On the Android 17 emulator, the real service uploaded 91 synthetic 1,280,000-byte chunks
and a second recording while the Activity was recreated, destroyed, and the screen
locked. The notification continued reporting progress; a simulated 503 at chunk 73
was retried in order, and the service stopped after completion. A separate streaming
test processed three sets of 91 chunks with no cumulative payload retention, then
streamed a 1 GiB sparse file. The sink buffer never exceeded 64 KiB.

These fixtures do not replace testing the original encrypted one-hour recording on
the affected physical phone against the deployed server. Repeat that test with a real
network outage and restored connection, screen lock, Activity recreation, and several
consecutive recordings. Track Java/native memory with Android Studio or `adb shell
dumpsys meminfo com.paganini.voxvault`; verify all 91 indexes and final processing on the
server. Forced Doze and manufacturer battery restrictions also need physical-device
coverage. The server still parses Base64 JSON and buffers decrypted recordings for
processing; this change bounds Android upload memory, not server processing memory.
