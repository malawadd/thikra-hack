# Thikra architecture

FastAPI owns policy and state. SvelteKit is a same-origin presentation/BFF layer and never receives server secrets. Existing Genblaze catalog, pipeline, storage sink, lineage, and ffmpeg composition code remain the media execution path.

Thikra Studio is a second Svelte 5 surface inside a restrictive Tauri 2 shell. Development uses `127.0.0.1:43192`; the packaged shell chooses an available loopback port and returns it through a narrow bootstrap command. The API never binds publicly. The Windows package owns a frozen Python API and bundled FFmpeg process tree through a kill-on-close Job Object, applies migrations before readiness, and keeps database, assets, proxies, cache, and rotating logs below the Tauri application-data directory. Semantic graph snapshots are immutable while positions and viewport are stored independently. Multi-track sequence snapshots follow the same rule: content and renders reference immutable revisions, while playhead, zoom, layout, and selection remain mutable view state.

```mermaid
flowchart LR
    UI["Tauri Generate + Edit workspaces"] --> API["Loopback /studio API"]
    API --> REV["Immutable workflow revisions"]
    API --> AG["Multimodal proposal agent"]
    API --> EX["Dirty-node executor + cache"]
    AG --> CHAT["genblaze_openai.chat"]
    EX --> CAT["Provider catalog capabilities"]
    CAT --> GEN["Genblaze pipelines"]
    GEN --> LOCAL["Hashed local Studio assets"]
    LOCAL -. "optional copy / reference handoff" .-> B2["Backblaze B2 via genblaze-s3"]
    EX --> COMP["composer.py only ffmpeg surface"]
    API --> SEQ["Immutable sequence revisions"]
    SEQ --> PREVIEW["Hash-keyed thumbnails + 720p proxies"]
    SEQ --> RENDER["Cancellable render jobs + SSE"]
    RENDER --> COMP
    API --> SQL["Local SQLite metadata"]
    API --> KR["Windows Credential Manager"]
    SHELL["Tauri lifecycle manager"] --> API
    SHELL --> BIN["Frozen API + FFmpeg + Noto resources"]
```

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
- `app/studio/` owns local projects, semantic revisions, canvas layout, proposals, imported assets, cost confirmation, node execution state, cache keys, and resumable execution events.
- `app/repo/studio_runtime.py` compiles validated executable graph nodes using catalog entries without importing provider classes.

## State and money

Generation transitions are explicit and invalid changes return stable 409 errors. The confirmed mandate and authorized amount cap every retry. Money is integer minor units plus ISO currency; authorization, invocation, delivery, verification, acceptance, and redress remain separate fields/events.

## Audit integrity

Each event hashes canonical JSON plus the previous hash. UTC normalization keeps the chain stable across SQLite (which strips timezone metadata) and PostgreSQL. Exports contain mandate versions, provider decision, payment references, assets/hashes, evaluations, events, and cases, but never one-time credentials or model chain-of-thought.

## Modes and failure policy

DEMO materializes labeled fixtures. SANDBOX runs real configured Prava and Genblaze/B2 integrations. PRODUCTION performs startup validation and uses explicit CORS origins. Essential B0/B1 preflight remains enabled; B2 stays best-effort. Missing video can fall back to a keyframe, missing audio becomes a warning, but missing all visuals fails. Low confidence, rights uncertainty, policy thresholds, or explicit mandate rules produce human review.

## External commerce flow

```mermaid
sequenceDiagram
    actor User
    participant Buyer as External Buyer Agent
    participant Gateway as Thikra Agent Gateway
    participant Catalog as Service Catalog
    participant Prava
    participant Fulfill as Fulfillment Engine
    participant Genblaze
    participant B2
    participant Verify as Verification Engine
    User->>Buyer: Create verified Arabic ad under $10
    Buyer->>Gateway: Discover services
    Gateway->>Catalog: List active offers
    Catalog-->>Buyer: Service definitions
    Buyer->>Gateway: Request quote
    Gateway-->>Buyer: Quote, mandate preview and expiry
    Buyer->>Gateway: Create order
    Gateway-->>Buyer: Order and payment action
    Buyer->>Prava: Request user authorization
    Prava->>User: Approve bounded payment
    User->>Prava: Approve
    Prava-->>Gateway: Authorization result
    Gateway->>Fulfill: Start exactly paid order
    Fulfill->>Genblaze: Execute media pipeline
    Genblaze->>B2: Store source and generated assets
    Genblaze-->>Fulfill: Generation events
    Fulfill->>Verify: Evaluate assets
    Verify->>B2: Read assets and manifests
    Verify-->>Fulfill: Pass, fail, warning or review
    alt Retry permitted
        Fulfill->>Genblaze: Retry failed component
        Genblaze->>B2: Store replacement asset
        Fulfill->>Verify: Re-evaluate
    end
    Fulfill->>B2: Store delivery receipt and evidence
    Fulfill-->>Gateway: Order delivered
    Gateway-->>Buyer: Deliverables and signed receipt
    Buyer-->>User: Final verified media awaiting acceptance
```

## Commercial object model

```mermaid
flowchart TD
    SO[Service Offer] --> SV[Service Version]
    SV --> Q[Quote]
    Q --> O[Order]
    BP[Buyer Principal] --> O
    BA[Buyer Agent] --> O
    APP[Developer Application] --> BA
    O --> PA[Payment Authorization]
    PA --> P[Payment]
    P --> F[Fulfillment Job]
    F --> M[Creative Mandate]
    F --> GR[Generation Run]
    GR --> A[Assets]
    A --> V[Verification Results]
    V --> D[Deliverables]
    D --> DR[Delivery Receipt]
    O --> DS[Dispute]
    DS --> RF[Refund Request or Supported Refund]
    O --> E[Audit and Evidence Graph]
    P --> E
    GR --> E
    V --> E
    DR --> E
    DS --> E
```

## Thikra's three economic roles

```mermaid
flowchart LR
    EXT[External Buyer Agent] -->|Pays for outcome| SELLER[Thikra as Seller]
    SELLER -->|Creates fulfillment mandate| SUPERVISOR[Thikra as Supervisor]
    SUPERVISOR -->|Selects and purchases capacity| BUYER[Thikra as Buyer]
    BUYER --> PROVIDERS[Generation Providers]
    PROVIDERS -->|Media assets| SUPERVISOR
    SUPERVISOR -->|Verified deliverable| SELLER
    SELLER -->|Delivery and receipt| EXT
```

REST, MCP, and SvelteKit call the same commercial domain services. Customer revenue and internal provider procurement remain different payment directions and records. The audit graph extends from principal/application/agent through service, quote, order, customer payment, fulfillment, media, verification, receipt, dispute, and redress.
