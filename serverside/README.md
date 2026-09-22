# Docker Setup

Run from `serverside`:

```sh
sudo docker compose up -d --build
```

The commands below use `sudo` for Docker daemon access on this host. The
default private-key directory is `/etc/voxvault`, which is root-owned;
creating or deleting files there from the host also requires `sudo`.
The container runs as root and reads/writes this directory through its
`/data` mount. Set `VOXVAULT_KEY_DIR_HOST` in `.env` to use another host
directory. Keep the project files and `.env` owned by your normal user.

Open `http://localhost:6100/ui` (or the port set by `VOXVAULT_PORT` in `.env`).
The root URL also opens setup. Existing private keys are reused.

1. Create server keys if needed, then install the VoxVault Android app and
   accept its requested permissions. Scan the public-key QR code to save the
   key in the app. Select **Expected spoken languages** on the setup page and
   save, for example English and German. These settings remain editable on
   `/ui` after setup is complete.
2. Click **Next step**, tap **Listen** on the phone, and read the English
   passage if English is selected. Otherwise, read or speak in one of your
   selected languages about the suggested topics, approximately 60-90 seconds. Keep pauses short
   so the phone saves one recording. Tap **Stop** when finished, then click
   **I've stopped listening** on the website.
3. In the phone's **Settings**, enter the **Backend URL** and **Port**, and
   tap **Save**. Select only the new passage under **Recordings** and tap
   **Upload**. The website updates automatically every second while chunks
   arrive, are decrypted, transcribed, aligned, embedded, and saved.

Use a LAN IP or public hostname reachable from the phone. `localhost` on
the phone refers to the phone itself. By default, connection details are
derived from the browser URL, including the published port. For a different
external address, add an optional value to `.env`, for example:

```dotenv
VOXVAULT_PUBLIC_URL=http://192.168.1.50:6100
```

For an HTTPS proxy, use its public origin, for example
`VOXVAULT_PUBLIC_URL=https://voice.example.com`. The Android client expects
the API at the root of that origin. Recreate the service after changing
environment variables: `sudo docker compose up -d`.

Setup state, recordings, and the personal voice profile (`Me`) are stored
in PostgreSQL's existing `voxvault-postgres` volume. The `setup_state` table
is created automatically, and existing installations receive an
`expected_languages` JSON column with an English default. Keys remain in
the configured host key directory, and downloaded models persist in the
`voxvault-models` volume. Rebuilding containers preserves these volumes;
`sudo docker compose down -v` removes database and model volumes, but
preserves the private key because its directory is a host bind mount.

## Conversation Explorer

After setup, select **Explore conversations**, or open `/ui/explorer`.
The **Conversations** tab lists distinct processed recordings, newest first.
Opening a recording displays every surviving chunk in chronological order,
with full, readable transcripts on desktop, mobile browsers, and Android.

- Search semantically across individual transcript chunks. Results are grouped
  by conversation and ranked by matching chunk count times maximum similarity.
  An empty query browses all recordings. Assignment filters affect matches;
  conversation details always retain the surrounding dialogue.
- Search results open the full conversation with matching segments highlighted.
  Previous/next match controls, reloadable links, and Back navigation preserve
  the search context.
- Select a segment's speaker to assign, reassign, unassign, or create a person.
  Existing people appear in descending voice cosine similarity, with missing
  profiles last. The picker supports name filtering and pagination through all
  people. Manual assignment also works without a usable voice vector.
- The voice inspector retains word confidence, language, timings, and raw
  diarization labels for closer inspection.
- **Inspect Voice Embedding** opens the selected chunk alongside existing
  people and ranked voice matches. Select a person, rename them, inspect
  their known chunks, or create a named person from the selected sample.
- Assign, reassign, or remove a chunk association without reloading the page.
  Similarity is cosine similarity, not a calibrated probability of identity;
  candidate matches become confirmed associations only when assigned.

Each assignment recomputes the affected person's profile from all their
assigned chunk embeddings, weighted by stored spoken-word counts, then
normalizes the result to unit length. Reassignment also rebuilds the previous
person's profile; removing their last sample clears the profile. Repeating
an assignment does not count the sample twice. Enrollment shares this same
weighted-profile calculation.

The explorer API is documented under `/docs` at `/api/explorer`. Semantic
search uses the existing multilingual E5 model and may take longer on its
first request while loading the model.

Startup enables pgvector and creates the current tables and indexes directly
from `db/models.py` in one transaction. Repeated startup leaves existing tables
and data intact. Text embeddings belong to chunks; no conversation vectors are
computed during upload, deletion, or startup.

Conversation endpoints (camelCase JSON, recording IDs rather than upload chunk indexes):

| Endpoint | Response |
| --- | --- |
| `GET /api/explorer/conversations` | Paginated summaries with `matchedChunkIds`, maximum cosine `similarity`, and `matchScore` |
| `GET /api/explorer/conversations/{id}` | Recording metadata, all ordered `chunks`, and per-chunk `matched` flags |
| `GET /api/explorer/persons?chunk_id={id}` | Existing people ranked by voice cosine similarity |
| `PUT /api/explorer/chunks/{id}/person` | Assignment using `{"personId": 3}` or explicit `null` to unassign |
| `POST /api/explorer/persons` | Create and assign using `{"name": "Alex", "chunkId": 9}` |

Both conversation GET endpoints accept `q` and `assignment=all|assigned|unassigned`.
The list also accepts `limit` (1..100) and `offset`. The fixed cosine similarity
threshold is `0.80`. Every qualifying chunk contributes to `matchScore`, without
an approximate nearest-neighbor candidate cap; pagination happens after grouping
and ranking. `matchScore` can exceed 1, while `similarity` stays within -1..1.
Ties use newest recording timestamp, then recording ID. Empty queries require no
model and browse newest first. A failed model load returns `503`.
Semantic matching includes related wording and does not require literal query
occurrences. The legacy `/chunks` endpoint retains its separate search modes.

Run the optional real-model relevance regression with
`EXPLORER_RUN_MODEL_TESTS=1 .venv/bin/python -m unittest discover -s tests -p test_explorer.py`.
It uses the configured E5 model to compare banana discussion with unrelated topics.
Set `EXPLORER_TEST_DATABASE_URL` to exercise fresh PostgreSQL initialization and pgvector too.

Android's `HttpCommunicationService` exposes typed conversation retrieval,
speaker suggestions, assignment, and person creation using the same upload URL
prefix and cancellable OkHttp connection pool. Archive DTOs in `Conversation.kt`
are separate from the encrypted local `Recording` and transport `Chunk` classes.
The shared WebView consumes the same API, and Android Back closes a picker or
returns from a conversation to its previous results.

## Stop and Reset

Stop the service while keeping the database, downloaded models, and keys:

```sh
sudo docker compose down
```

For a **complete reset**, including the private key, run:

```sh
sudo ./reset.sh
```

The script stops the service, removes its Docker volumes, and deletes the
private key. It uses the Compose configuration and `.env` beside the script,
so it also works when invoked by its full path from another directory. It
stops on any error and leaves the service stopped after a successful reset.

This permanently removes recordings, setup state, voice profiles, downloaded
models, and the server private key. Recordings still on the phone that were
encrypted with the old public key cannot be decrypted after its private key
is deleted. Back up the key first if you need access to those recordings.

The `reset` profile is an explicit opt-in for key deletion. `reset-keys`
deletes only `server-privatekey.key` inside `VOXVAULT_KEY_DIR_HOST` (default
`/etc/voxvault`), leaving the directory and any other files intact. It reuses
the local `voxvault-server:latest` image and does not start the application
or database. If that image has been removed, rebuild it first with
`sudo docker compose build voxvault`; `reset.sh` does this automatically
before removing any data. Normal startup does not run the reset service.

Start again after a reset:

```sh
sudo docker compose up -d --build
```

Open `/ui`, generate new keys, scan the new public-key QR code in the Android
app, and repeat the voice setup with a new recording.

## Processing and Troubleshooting

### Archive on Android

Open **Archive** from the phone's Recordings screen. It uses the server address
and port saved in Settings and opens the shared conversation explorer inside
the app. The updated server must be reachable from the phone. Search by text,
inspect voice similarity, create or rename people, and assign or
unassign chunks there. Android Back closes the current dialog before returning
to recordings. Archive browsing does not require microphone permission.
The initial chunk view shows Unassigned; the Association filter also offers
All chunks and Assigned. Meaning search has been removed.

In Speaker inspection, select a word, drag over a phrase, or Shift-click a
range, then enter replacement words and apply the checkmark. Corrections stay
light blue, including after reopening the conversation. Closing the inspector
(including Escape or Android Back) saves all edited chunks and recomputes their
text embeddings together. A failed save leaves the inspector and edits open
for retry. Reloading or leaving the page with pending edits prompts before
discarding them. Existing databases gain the word correction flag on startup.

The trash action on each chunk asks for confirmation. The
`DELETE /api/explorer/chunks/{chunk_id}` endpoint returns `204` on success and
`404` for a missing chunk. It deletes the chunk's transcript, word timings,
and text/voice embeddings, then recomputes the assigned person's profile in
the same transaction. Removing the last sample clears the profile but keeps
the person. The recording identity stays in the database to prevent a repeat
upload from recreating deleted chunks. Local recordings on the phone are
managed separately from archive chunks.
Use **Delete uploaded (N)** on the phone's Recordings screen to remove all
successfully uploaded local recordings after confirmation. Failed, cancelled,
and pending uploads are excluded. This action does not delete server data.

### Model Processing

The first recording downloads models and may take several minutes on CPU.
Enrollment uses the existing SpeechBrain speaker model to create a
normalized, word-weighted voice embedding; it does not retrain model weights.
It expects one speaker, 30-180 seconds of audio, and at least 50 aligned
words. Transcription and embedding failures appear on the setup page with
retry options. Refreshing the page resumes the saved step. If the container
restarts during an upload or processing, the page prompts for a resend.
Run exactly one Uvicorn process (`--workers 1`), as in the provided Dockerfile.
The application owns one background audio worker, which processes completed
recordings sequentially outside the HTTP thread pool. Startup is lazy: WhisperX
and its VAD, Pyannote diarization, SpeechBrain, and multilingual E5 each load on
first use and are reused. E5 initialization and inference are serialized.

Completed PCM recordings wait in a private temporary directory on disk; the
queue holds only paths and recording metadata. Files are deleted after each
job, including failed jobs. Graceful shutdown stops accepting jobs and drains
the queue before joining the worker. Allow enough shutdown time for queued
inference to finish. This is an in-process queue, not a durable task broker:
after a forced termination, resend unfinished recordings from the phone.
Orphaned `voxvault-processing-*` directories may be removed while the server
is stopped. Incomplete uploads still use the existing in-memory chunk buffers.

`VOXVAULT_PROCESSING_DIR` optionally selects an existing writable spool parent
directory; by default it is the server working directory (the container's
`/app`). Use a disk filesystem, not a RAM-backed tmpfs, and reserve disk space
for the upload backlog. Do not expose the spool as static content or include
it in backups; it contains decrypted audio until each job finishes.

Expected languages use standard Whisper codes, saved through
`PUT /api/setup/languages`, for example `{"expectedLanguages":["en","de"]}`.
One expected language bypasses detection. With multiple languages, detection
uses up to 30 seconds of VAD speech and accepts any configured language even
when detection confidence is low. For unconfigured detections,
`WHISPERX_LANGUAGE_MIN_CONFIDENCE` (default `0.7`) distinguishes unreliable
from confident-but-unexpected results; both are skipped. Short speech is
allowed; silence, unconfigured languages, unsupported alignment languages,
and transcripts without usable aligned words are skipped without saving empty recordings.
Enrollment follows the same language rules. Setup offers its existing English
passage when English is selected, or topics to speak about in a selected
language otherwise; the existing duration and voice-profile checks still apply.

Alignment models load on demand only for accepted languages and stay cached
between recordings. Changes to expected languages take effect at the start of
the next job; models for removed languages are evicted then. Per-recording
waveforms, results, and embeddings stay local to the job, with inference mode
enabled and PCM views used for chunk embeddings. No per-job garbage collection
or CUDA cache flushing is used. Memory should plateau once the models used by
the configuration have loaded; actual peaks still depend on recording length,
batch size, device, and models. Queue, model initialization, skip, and completion
messages appear in the server logs; recent upload events include `skipped` or
`failed`, and enrollment failures retain the existing setup retry flow.

Inspect service health and processing errors with:

```sh
sudo docker compose ps
sudo docker compose logs -f voxvault
```

Backend regression checks use temporary keys and a temporary SQLite database
with model inference mocked, leaving deployment data untouched:

```sh
.venv/bin/python -m unittest discover -s tests -v
```

To run the explorer checks against actual pgvector queries, set
`EXPLORER_TEST_DATABASE_URL` to a PostgreSQL test database and run
`.venv/bin/python -m unittest discover -s tests -p test_explorer.py -v`.
The tests create and remove their own schemas and need permission to create
the vector extension and schemas in that database.

Browser regression checks use intercepted fixture responses and never access
deployment data. With Playwright installed in an external tools directory:

```sh
npm install --prefix /tmp/voxvault-browser-tests playwright
/tmp/voxvault-browser-tests/node_modules/.bin/playwright install chromium
NODE_PATH=/tmp/voxvault-browser-tests/node_modules node tests/explorer.browser.cjs
NODE_PATH=/tmp/voxvault-browser-tests/node_modules node tests/setup.browser.cjs
```

These check deletion, failure recovery, speaker management, and narrow and wide
layouts. Android build, URL handling tests, and lint run from `android/` with
`./gradlew :app:assembleDebug :app:testDebugUnitTest :app:lintDebug`.
