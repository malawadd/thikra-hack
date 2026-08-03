# Desktop releases

## v0.1.1 — self-contained Windows x64

The MSI and NSIS setup EXE package the Tauri/Svelte app, frozen FastAPI engine,
SQLite migrations, FFmpeg/FFprobe with libx264 and libass, Noto fonts, and
third-party notices. DEMO mode starts without credentials or developer tools.
Provider keys and optional B2 settings are entered inside the app and stored in
Windows Credential Manager. The release publishes `SHA256SUMS.txt` beside both
installers and is currently unsigned.

The reproducible build is `pnpm build:desktop`. It verifies all downloaded
runtime hashes before Tauri runs. `pnpm smoke:desktop:runtime` launches the
frozen API with Python, Node, uv, and system FFmpeg absent from `PATH`, then
checks first-run project creation and restart persistence. The release workflow
runs application tests, structural guards, bundle audit, and Tauri WebDriver
smoke before publishing.

## v0.1.0 — incomplete preview

v0.1.0 is preserved for history but only packaged the desktop shell. It still
required a separately launched Python API and system FFmpeg, so it is not a
self-contained end-user release. Install v0.1.1 or later instead.
