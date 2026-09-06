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
