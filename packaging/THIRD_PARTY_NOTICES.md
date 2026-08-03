# Thikra Studio packaged runtime notices

The Windows installer includes FFmpeg from the BtbN FFmpeg Builds project.
The selected build enables GPL components, including libx264 and libass, and
is redistributed under GPL-3.0-or-later. The exact archive, checksum, build
source, and corresponding FFmpeg source link are recorded in
`windows-runtime.json` and shipped beside the binaries.

The frozen Python service contains the Python packages declared and locked in
`services/api/pyproject.toml` and `services/api/uv.lock`. Their package metadata
and license files are retained by the PyInstaller one-folder bundle where
provided by each dependency.
