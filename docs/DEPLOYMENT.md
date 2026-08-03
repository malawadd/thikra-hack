# Deployment

Local development does not require Docker: copy `.env.example`, run `pnpm setup`, then `pnpm dev`.

## Containers

```bash
docker compose up --build
```

Compose runs PostgreSQL, FastAPI with ffmpeg/ffprobe, and the SvelteKit Node adapter. The web container talks to `http://api:43192`; only ports 43191 and 43192 are exposed. Persistent database data uses a named volume.

## Windows desktop distribution

Thikra Studio v0.1.1 is distributed separately as unsigned Windows x64 MSI and NSIS installers. The package includes the static renderer, frozen FastAPI/Uvicorn service, Alembic migrations, Windows keyring backend, GPL FFmpeg/FFprobe with libx264/libass, Noto fonts, and license/source notices. Tauri chooses an unused `127.0.0.1` port, launches the engine hidden, and owns the process tree with a Windows Job Object. Installed users do not install Python, uv, Node.js, Rust, or FFmpeg.

Packaged state lives under the Tauri application-data directory in `database/`, `assets/`, `proxies/`, `cache/`, and `logs/`. Packaged mode never reads the repository `.env`. Provider and optional B2 overrides use Windows Credential Manager. General shell and filesystem capabilities are not exposed to Svelte; Open Logs and Save As are fixed-purpose Rust commands.

For SANDBOX set `APP_MODE=SANDBOX` and provide Prava, B2, OpenAI, and selected-provider credentials. For PRODUCTION replace `SESSION_SECRET`, terminate TLS at an ingress, restrict `API_CORS_ORIGINS`, keep secrets in a secret manager, run Alembic before rollout, and use shared ingress rate limits in addition to the process guard. Production startup refuses missing core Prava/B2 values.

Health checks: `GET /health` (liveness/media dependencies), `GET /health/ready` (database/config readiness), and `GET /health/integrations` (sanitized integration status).

PostgreSQL URL format: `postgresql+psycopg://user:password@host:5432/thikra`. SQLite is appropriate only for local/demo single-process use. Scale media workers with the configured concurrency and run-duration caps; B2 objects remain durable if an API process restarts.
