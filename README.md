# VoxVault

VoxVault is a privacy-focused voice archive that turns personal recordings into a searchable AI-powered knowledge base.

The goal is simple: capture thoughts, conversations, and ideas through voice, while keeping ownership of the data.

## Why VoxVault?

Modern AI makes it possible to transform voice recordings into structured knowledge:

- Record audio from your phone
- Automatically transcribe speech
- Extract metadata and context
- Search previous recordings using semantic similarity

Unlike many cloud-based assistants, VoxVault is designed around self-hosting and user-controlled data.

## Core Architecture

![Architecture](docs/architecture.png)

The system consists of four main parts:

1. **Mobile application**
   - Records audio
   - Collects metadata
   - Uploads recordings

2. **Backend API**
   - Receives recordings
   - Coordinates processing
   - Provides search access

3. **AI processing pipeline**
   - Speech-to-text transcription
   - Embedding generation
   - Optional analysis and summarization

4. **Storage layer**
   - Stores recordings
   - Stores transcripts
   - Stores embeddings and metadata

## Data Flow

![Data Flow](docs/data-flow.png)

A typical recording follows this path:

1. Audio is captured on the phone
2. The recording is uploaded securely
3. Speech recognition creates a transcript
4. AI processing creates searchable representations
5. Metadata, transcripts and embeddings are stored
6. The user can search and retrieve previous recordings

## Deployment and Networking

![Deployment](docs/deployment.png)

VoxVault can be deployed on a personal server, homelab, or cloud infrastructure.

The reference deployment uses:

- Ubuntu server
- Docker containers
- Caddy as reverse proxy
- Tailscale for private networking

Example routing:

Phone → Tailscale → Caddy → VoxVault API → Processing Services

This is only one possible setup. The networking layer is intentionally flexible. Users can instead use:

- Traditional HTTPS with public domains
- VPN solutions other than Tailscale
- Cloud reverse proxies
- Local-only deployments

The internal service architecture does not depend on a specific networking solution.

## Privacy

VoxVault follows a privacy-first approach:

- No mandatory external cloud services
- User-controlled storage
- Self-hosted processing
- Private communication channels

## Repository Structure

```
VoxVault/
├── android/
├── backend/
├── docs/
│   ├── architecture.png
│   ├── data-flow.png
│   └── deployment.png
└── README.md
```

## Roadmap

### Recording
- Android recording client
- Upload management
- Recording library

### AI Processing
- Speech recognition
- Transcript generation
- Embeddings

### Knowledge System
- Semantic search
- Summaries
- Personal knowledge retrieval

## License

To be decided.
