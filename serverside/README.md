# Docker Setup

Run from `serverside`:

```sh
docker compose up -d --build
```

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
environment variables: `docker compose up -d`.

Setup state, recordings, and the personal voice profile (`Me`) are stored
in PostgreSQL's existing `voxvault-postgres` volume. The `setup_state` table
is created automatically without changing existing tables. Keys remain in
the configured host key directory, and downloaded models persist in the
`voxvault-models` volume. Rebuilding containers preserves these volumes;
`docker compose down -v` removes database and model volumes.

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
docker compose ps
docker compose logs -f voxvault
```

Backend regression checks use temporary keys and a temporary SQLite database
with model inference mocked, leaving deployment data untouched:

```sh
.venv/bin/python -m unittest discover -s tests -v
```
