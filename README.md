# VoxVault

**A private, self-hosted archive for your conversations.**

VoxVault records speech on Android, encrypts it locally, sends selected recordings to your own server, transcribes them, and makes the resulting conversations searchable.

## What & Why

Commercial AI recorders and transcription services already exist. VoxVault was created around one simple idea:

> **Your conversations should stay under your control.**

Conversations can contain some of the most personal information we produce. VoxVault therefore keeps recording and processing under your control instead of relying on a cloud transcription service.

With VoxVault:

* your phone automatically detects and records speech;
* recordings are encrypted on the Android device;
* processing and transcription happen on your own server;
* conversations can be searched through the web interface and Android app;
* speakers can be assigned to transcript segments;
* voice embeddings can help recognize previously assigned speakers;
* speaker diarization separates recordings into individual speakers.

> [!IMPORTANT]
> Self-hosting does not automatically make a system secure. Protect your server, phone, database, encryption keys and backups appropriately.

## Setup

### 1. Install the Android App

Download the latest APK from:

**[GitHub Releases](https://github.com/adriano-paganini/VoxVault/releases)**

Install the APK and accept the permissions requested by VoxVault, particularly:

* **Microphone**
* **Notifications / foreground service**

Android may ask you to allow installation from unknown sources when installing the APK manually.

You can finish configuring the app after the backend is running.

### 2. Download the Server Files

Put [`serverside/.env.example`](serverside/.env.example), [`serverside/docker-compose.prod.yml`](serverside/docker-compose.prod.yml), and [`serverside/compose.sh`](serverside/compose.sh) in the same server directory. Then run:

```bash
mv docker-compose.prod.yml docker-compose.yml
cp .env.example .env
```

### 3. Configure `.env`

Each variable is explained by the comment above it in `.env`. Set `POSTGRES_PASSWORD` before the first startup, set `VOXVAULT_PUBLIC_URL` to the address your phone can reach, and choose where `VOXVAULT_KEY_DIR_HOST` will store the server key. `VOXVAULT_PUBLIC_URL` only suggests an address during setup; the Android app stores its own backend URL.

The supplied `WHISPERX_DEVICE=cpu` must stay as `cpu`. GPU support is not available yet. Other speech and database settings can usually stay at their supplied values. `HUGGINGFACE_TOKEN` may be empty if you do not need speaker diarization.

To enable speaker diarization, [accept access to the model](https://huggingface.co/pyannote/speaker-diarization-community-1), then [create a read token](https://huggingface.co/settings/tokens) and put it in `HUGGINGFACE_TOKEN`. Accepting access is required before the token can download the model.



### 4. Prepare the Encryption-Key Directory

The supplied `VOXVAULT_KEY_DIR_HOST` stores the server key under `/etc/voxvault`. Choose one of these setup options:

#### Option A: Dedicated `voxvault` User

For the supplied `/etc/voxvault` key directory, create a dedicated user and directory:

```bash
sudo useradd \
  --system \
  --user-group \
  --create-home \
  --home-dir /var/lib/voxvault \
  --shell /usr/sbin/nologin \
  voxvault
sudo usermod -aG docker voxvault
sudo install -d \
  -o voxvault \
  -g voxvault \
  -m 700 \
  /etc/voxvault

sudo install -d \
  -o voxvault \
  -g voxvault \
  -m 750 \
  /opt/voxvault
sudo install \
  -o voxvault \
  -g voxvault \
  -m 640 \
  .env docker-compose.yml compose.sh \
  /opt/voxvault/
```

Switch to the `voxvault` user before entering its private deployment directory. Stay in this shell for step 5:

```bash
sudo -u voxvault -H sh
cd /opt/voxvault
```

#### Option B: Use Your Normal User

If your user can access Docker, store the key in a private directory you own:

```bash
mkdir -p ~/.local/share/voxvault
chmod 700 ~/.local/share/voxvault
```

Set `VOXVAULT_KEY_DIR_HOST` in `.env` to that directory's absolute path, for example:

```env
VOXVAULT_KEY_DIR_HOST=/home/YOUR_USER/.local/share/voxvault
```

Keep the key directory private and out of public sync services.

### 5. Start VoxVault

From the server directory, start Compose:

```bash
sh compose.sh up -d
sh compose.sh ps
```

If you chose the dedicated `voxvault` user, run these commands inside the shell opened in step 4; you do not need another `sudo -u`. With your normal Docker-enabled user, run them from the directory containing `.env` and `compose.sh`.

Use `sh compose.sh logs -f` to follow the logs. Run Compose through `compose.sh` so values from `.env` take precedence over exported shell variables. Type `exit` when finished with the `voxvault` shell.

### 6. Finish Setup

Open `http://localhost:6100/ui` on the server, using your `VOXVAULT_PORT` if you changed it. Follow the setup interface to create the server key, connect Android, choose languages, and save a voice profile.

On the phone, use an address it can reach, such as `http://192.168.1.50:6100` or `http://your-server.your-tailnet.ts.net:6100`. Use your configured port. `localhost` on the phone refers to the phone itself.

## Resource Usage

Transcription and speaker processing run on the server's CPU. The supplied `large-v3` model can need several gigabytes of RAM; choose a smaller `WHISPERX_MODEL` or send shorter recordings on a limited server.

## Encryption & Backups

Back up the private key at `VOXVAULT_KEY_DIR_HOST/server-privatekey.key` securely and separately from the server. If it is lost, recordings encrypted for it cannot be recovered. Back up the database too if you want to preserve transcripts, speakers and metadata.

> [!IMPORTANT]
> Server-side transcripts, metadata and voice embeddings are not themselves encrypted by VoxVault. Protect access to the server and database accordingly.

## License

VoxVault is licensed under the [MIT License](LICENSE). Third-party libraries, models, and assets retain their respective licenses.

## Legal Disclaimer

VoxVault is an experimental project and is provided **as is**.

Breaking changes, processing failures, software bugs, incompatibilities or data loss may occur.

Always keep backups of important data and encryption keys.

### Recording Laws

Laws governing the recording of conversations differ between countries and jurisdictions.

Depending on where you are, recording may require:

* consent from one participant;
* consent from every participant;
* prior notification;
* additional requirements in workplaces, healthcare, education or other protected environments.

**You are responsible for determining whether you are legally permitted to record a conversation before using VoxVault to record it.**

You are also responsible for the lawful recording, processing, transcription, storage, speaker identification, sharing, retention and deletion of data processed with VoxVault.

VoxVault is **not intended for unlawful surveillance, eavesdropping, stalking or prohibited covert recording**.

Voice recordings, transcripts and voice embeddings may constitute personal data and may be subject to privacy or data-protection requirements.

Self-hosting does not remove these obligations.

The developer does not determine whom users record, what data they process or whether a particular use of VoxVault is lawful.

To the maximum extent permitted by applicable law, VoxVault is provided without warranties regarding reliability, availability, security, fitness for a particular purpose or legal compliance.

**This documentation does not constitute legal advice.**
