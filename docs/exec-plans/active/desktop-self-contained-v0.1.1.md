# Thikra Studio self-contained Windows release v0.1.1

Status: implementation and validation in progress.

- Freeze the FastAPI service as a PyInstaller 6.21 one-folder runtime with migrations and keyring data.
- Verify and bundle pinned GPL FFmpeg/FFprobe plus Noto fonts and notices.
- Let Tauri choose the loopback port, own the runtime through a Windows Job Object, monitor readiness, recover from failure, and enforce single instance.
- Keep Studio media local by default with optional Credential-Manager-backed B2 copies and provider-readable reference handoff.
- Build and audit MSI/NSIS installers, run frozen-runtime and WebDriver smoke tests, publish SHA-256 hashes, and replace the incomplete preview with v0.1.1.

Completion requires the full backend/frontend/structural suites, frozen runtime smoke with developer tools removed from `PATH`, both installer formats, release asset verification, and public v0.1.1 publication.
