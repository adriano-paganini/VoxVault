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

### 2. Create `.env`

Create a `.env` file in your VoxVault server directory:

```env
VOXVAULT_PORT=6100

VOXVAULT_PRIVATE_KEY_PATH=/data/server-privatekey.key
VOXVAULT_PRIVATE_KEY_HOST_PATH=/etc/voxvault/server-privatekey.key
VOXVAULT_KEY_DIR_HOST=/etc/voxvault

VOXVAULT_PUBLIC_URL=http://your-server-address:6100

WHISPERX_MODEL=large-v3
WHISPERX_DEVICE=cpu
WHISPERX_COMPUTE_TYPE=int8
WHISPERX_BATCH_SIZE=8
VOXVAULT_MODEL_IDLE_SECONDS=5

HUGGINGFACE_TOKEN=hf_your_token_here
```

#### Enable Speaker Diarization

VoxVault uses:

**[`pyannote/speaker-diarization-community-1`](https://huggingface.co/pyannote/speaker-diarization-community-1)**

for speaker diarization.

The model is free to access, but Hugging Face requires you to **accept its access conditions before VoxVault can download it**.

##### 1. Create a Hugging Face account

Create or sign in to your account at:

**[huggingface.co](https://huggingface.co/)**

##### 2. Accept access to the model

While logged in, open:

**[pyannote/speaker-diarization-community-1](https://huggingface.co/pyannote/speaker-diarization-community-1)**

The page will display:

> **You need to agree to share your contact information to access this model**

Accept the conditions to gain access to the model files.

> [!IMPORTANT]
> Creating a Hugging Face token without first accepting the model conditions is not sufficient.

##### 3. Create an access token

Go to:

**[Hugging Face Access Tokens](https://huggingface.co/settings/tokens)**

Create a token that can read the model.

Then add it to `.env`:

```env
HUGGINGFACE_TOKEN=hf_your_token_here
```

VoxVault will download the model automatically when it is first required.

#### Transcription Model

The default configuration uses:

```env
WHISPERX_MODEL=large-v3
WHISPERX_DEVICE=cpu
WHISPERX_COMPUTE_TYPE=int8
WHISPERX_BATCH_SIZE=8
```

`large-v3` provides high-quality transcription but requires considerably more resources than smaller Whisper models.

#### Backend URL

```env
VOXVAULT_PUBLIC_URL=http://your-server-address:6100
```

`VOXVAULT_PUBLIC_URL` is **not binding**.

It is only used to suggest/display a backend URL during setup.

### 3. Download Docker Compose

Download:

```text
docker-compose.prod.yml
```

from the repository and rename it:

```bash
mv docker-compose.prod.yml docker-compose.yml
```

Your directory should now contain:

```text
.
├── .env
└── docker-compose.yml
```

### 4. Prepare the Encryption-Key Directory

By default, VoxVault stores its server encryption key in:

```text
/etc/voxvault
```

The corresponding configuration is:

```env
VOXVAULT_PRIVATE_KEY_PATH=/data/server-privatekey.key
VOXVAULT_PRIVATE_KEY_HOST_PATH=/etc/voxvault/server-privatekey.key
VOXVAULT_KEY_DIR_HOST=/etc/voxvault
```

> [!CAUTION]
> **If the server private key is lost, recordings encrypted for it cannot be recovered.**

#### Option A: Dedicated `voxvault` User

When using `/etc/voxvault`, you can create a dedicated user for the deployment instead of running all Compose commands directly as root.

Create the user:

```bash
sudo useradd \
  --system \
  --user-group \
  --create-home \
  --home-dir /var/lib/voxvault \
  --shell /usr/sbin/nologin \
  voxvault
```

Allow it to use Docker:

```bash
sudo usermod -aG docker voxvault
```

Create the key directory:

```bash
sudo install -d \
  -o voxvault \
  -g voxvault \
  -m 700 \
  /etc/voxvault
```

Create a deployment directory:

```bash
sudo install -d \
  -o voxvault \
  -g voxvault \
  -m 750 \
  /opt/voxvault
```

Copy the configuration:

```bash
sudo install \
  -o voxvault \
  -g voxvault \
  -m 640 \
  .env docker-compose.yml \
  /opt/voxvault/
```

Then:

```bash
cd /opt/voxvault
```

#### Option B: Use Your Normal User

Alternatively, store the key somewhere your normal user controls:

```bash
mkdir -p ~/.local/share/voxvault
chmod 700 ~/.local/share/voxvault
```

Change `.env` accordingly:

```env
VOXVAULT_KEY_DIR_HOST=/home/YOUR_USER/.local/share/voxvault
VOXVAULT_PRIVATE_KEY_HOST_PATH=/home/YOUR_USER/.local/share/voxvault/server-privatekey.key
```

You can then run Docker Compose using your normal Docker-enabled user.

> [!WARNING]
> Wherever you store the key, ensure that it is not world-readable, publicly synchronized or included in insecure backups.

### 5. Start VoxVault

When using the dedicated `voxvault` user:

```bash
cd /opt/voxvault
sudo -u voxvault -H docker compose up -d
```

Check the containers:

```bash
sudo -u voxvault -H docker compose ps
```

View logs:

```bash
sudo -u voxvault -H docker compose logs -f
```

Alternatively, run:

```bash
sudo docker compose up -d
```

This is simpler, but runs Docker Compose with root privileges.

### 6. Finish Setup

With the default configuration, open:

```text
http://localhost:6100/ui
```

If you changed `VOXVAULT_PORT`, use the configured port instead.

Follow the instructions in the VoxVault setup interface.

The setup will guide you through:

1. creating the server encryption key;
2. importing its public key into Android;
3. configuring expected spoken languages;
4. configuring the backend URL in Android;
5. creating your initial voice profile.

#### Connect Android

The Android phone must use an address through which it can actually reach the server.

For example, on your LAN:

```text
http://192.168.1.50
```

with port:

```text
6100
```

Or through Tailscale:

```text
http://your-server.your-tailnet.ts.net
```

with port:

```text
6100
```

> [!IMPORTANT]
> Do **not** use `localhost` as the Backend URL on your phone.
>
> `localhost` on Android refers to the phone itself.

The **Backend URL configured inside the Android app** determines where VoxVault connects.

## Resource Usage

VoxVault performs transcription and speaker processing locally.

The example configuration uses:

```env
WHISPERX_MODEL=large-v3
```

Large models can consume **several gigabytes of RAM**.

Uploading multiple hours of recordings at once can additionally cause substantial temporary:

* RAM usage;
* CPU usage;
* disk usage;
* processing time.

If your server has limited resources, consider using a smaller Whisper model or uploading recordings in smaller batches.

> [!NOTE]
> Improvements to model memory management and processing of large recording queues are already in progress.

## Encryption & Backups

Recordings stored by the Android application are encrypted.

The server private key is required to decrypt recordings created for that server.

With the default configuration it is stored at:

```text
/etc/voxvault/server-privatekey.key
```

### Back Up the Private Key

Keep the backup:

* encrypted;
* access-controlled;
* preferably separate from the server.

If the private key is permanently lost, recordings encrypted for that key **cannot be recovered**.

Generating a new key does not restore access to old recordings.

You should also back up your VoxVault database if you want to preserve transcripts, speakers and metadata.

> [!IMPORTANT]
> Server-side transcripts, metadata and voice embeddings are not themselves encrypted by VoxVault.
>
> Protect access to the server and database accordingly.

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
