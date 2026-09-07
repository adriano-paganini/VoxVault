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
   key in the app.
2. Click **Next step**, tap **Listen** on the phone, and read the English
   passage at a natural pace, approximately 60-90 seconds. Keep pauses short
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
is created automatically without changing existing tables. Keys remain in
the configured host key directory, and downloaded models persist in the
`voxvault-models` volume. Rebuilding containers preserves these volumes;
`sudo docker compose down -v` removes database and model volumes, but
preserves the private key because its directory is a host bind mount.

## Conversation Explorer

After setup, select **Explore conversations**, or open `/ui/explorer`.
The explorer uses the existing recordings, transcription words, chunks, and
people; no database migration or reprocessing is needed.

- Search all chunks with a free-text semantic query, or select text matching
  for literal case-insensitive search. An empty query browses the archive.
  Results are paginated and can be filtered by assignment status.
- Word colors show saved transcription confidence, including a separate
  unknown-confidence state. Chunk details include recording time, language,
  offsets, and speaker labels.
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
first request while loading the model. Text matching does not need inference.

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
the app. The updated server must be reachable from the phone. Search by text
or meaning, inspect voice similarity, create or rename people, and assign or
unassign chunks there. Android Back closes the current dialog before returning
to recordings. Archive browsing does not require microphone permission.

The trash action on each chunk asks for confirmation. The
`DELETE /api/explorer/chunks/{chunk_id}` endpoint returns `204` on success and
`404` for a missing chunk. It deletes the chunk's transcript, word timings,
and text/voice embeddings, then recomputes the assigned person's profile in
the same transaction. Removing the last sample clears the profile but keeps
the person. The recording identity stays in the database to prevent a repeat
upload from recreating deleted chunks. Local recordings on the phone are
managed separately from archive chunks.

### Model Processing

The first recording downloads models and may take several minutes on CPU.
Enrollment uses the existing SpeechBrain speaker model to create a
normalized, word-weighted voice embedding; it does not retrain model weights.
It expects one speaker, 30-180 seconds of audio, and at least 50 aligned
words. Transcription and embedding failures appear on the setup page with
retry options. Refreshing the page resumes the saved step. If the container
restarts during an upload or processing, the page prompts for a resend.
Chunk buffers are held in memory, so run one Uvicorn worker, as in the
provided Dockerfile.

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
```

These check deletion, failure recovery, speaker management, and narrow and wide
layouts. Android build, URL handling tests, and lint run from `android/` with
`./gradlew :app:assembleDebug :app:testDebugUnitTest :app:lintDebug`.
