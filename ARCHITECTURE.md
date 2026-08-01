# Thikra architecture

FastAPI owns policy and state. SvelteKit is a same-origin presentation/BFF layer and never receives server secrets. Existing Genblaze catalog, pipeline, storage sink, lineage, and ffmpeg composition code remain the media execution path.

```mermaid
flowchart LR
    U["User or Brand Manager"] --> B["Creative Brief"]
    B --> M["Mandate Compiler"]
    M --> C["Confirmed Creative Mandate"]
    C --> A["Thikra Procurement Agent"]
    A --> D["Provider Discovery and Routing"]
    D --> P["Prava Authorization and Payment State"]
    P --> G["Genblaze Orchestrator"]
    G --> I["Image Providers"]
    G --> V["Video Providers"]
    G --> T["Voice Providers"]
    G --> S["Music Providers"]
    I --> B2["Backblaze B2"]
    V --> B2
    T --> B2
    S --> B2
    B2 --> E["Verification Engine"]
    E --> F["Deterministic File Checks"]
    E --> L["Language and Audio Checks"]
    E --> X["Multimodal Semantic Checks"]
    E --> R["Rights and Provenance Checks"]
    F --> DEC{"Acceptable Delivery?"}
    L --> DEC
    X --> DEC
    R --> DEC
    DEC -->|"Retry permitted"| G
    DEC -->|"Uncertain"| H["Human Review"]
    DEC -->|"Pass"| FIN["Final Delivery"]
    DEC -->|"Reject"| CASE["Redress Case"]
    H -->|"Approve"| FIN
    H -->|"Retry"| G
    H -->|"Reject"| CASE
    FIN --> AUD["Audit and Evidence Graph"]
    CASE --> AUD
```

```mermaid
sequenceDiagram
    actor User
    participant Web as SvelteKit
    participant API as FastAPI
    participant Prava
    participant Genblaze
    participant Provider
    participant B2
    participant Verify as Verification Engine
    User->>Web: Submit creative brief and budget
    Web->>API: Compile mandate
    API-->>Web: Structured proposed mandate
    User->>Web: Confirm mandate
    Web->>API: Request provider strategy
    API-->>Web: Quotes and provider decision
    Web->>API: Create bounded authorization
    API->>Prava: Create sandbox session
    Prava-->>Web: Secure iframe approval flow
    User->>Prava: Approve with documented authentication
    Web->>API: Poll authorization result
    API->>Genblaze: Start accountable generation
    Genblaze->>Provider: Generate media
    Provider-->>Genblaze: Generated output
    Genblaze->>B2: Store assets and manifests
    Genblaze-->>API: Pipeline events
    API-->>Web: SSE progress
    API->>Verify: Evaluate stored delivery
    Verify->>B2: Read assets and metadata
    Verify-->>API: Pass, fail, warning, or review
    API-->>Web: Verification result
    User->>Web: Approve, retry, or reject
    Web->>API: Final decision
    API->>B2: Store evidence export
    API-->>Web: Completed run
```

## Boundaries

- `app/repo/provider_catalog.py` is the only provider-class import surface.
- `app/repo/pipelines.py` builds B0 reference, B1 keyframes, and best-effort B2 media and owns standalone OpenAI structured output.
- `app/repo/composer.py` is the only ffmpeg/ffprobe subprocess surface.
- `app/thikra/orchestration.py` resolves catalog entries, executes the preserved pipeline, attaches Thikra context, persists asset records, and starts verification.
- `app/thikra/storage.py` is the sole adapter for non-media JSON evidence; media goes through Genblaze's B2 sink.
- `app/thikra/api.py` exposes typed operations; domain decisions live in services/state policy rather than Svelte.

## State and money

Generation transitions are explicit and invalid changes return stable 409 errors. The confirmed mandate and authorized amount cap every retry. Money is integer minor units plus ISO currency; authorization, invocation, delivery, verification, acceptance, and redress remain separate fields/events.

## Audit integrity

Each event hashes canonical JSON plus the previous hash. UTC normalization keeps the chain stable across SQLite (which strips timezone metadata) and PostgreSQL. Exports contain mandate versions, provider decision, payment references, assets/hashes, evaluations, events, and cases, but never one-time credentials or model chain-of-thought.

## Modes and failure policy

DEMO materializes labeled fixtures. SANDBOX runs real configured Prava and Genblaze/B2 integrations. PRODUCTION performs startup validation and uses explicit CORS origins. Essential B0/B1 preflight remains enabled; B2 stays best-effort. Missing video can fall back to a keyframe, missing audio becomes a warning, but missing all visuals fails. Low confidence, rights uncertainty, policy thresholds, or explicit mandate rules produce human review.
