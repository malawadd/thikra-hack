from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

root = Path(SPECPATH)
datas = [
    (str(root / "alembic.ini"), "."),
    (str(root / "migrations"), "migrations"),
]
binaries = []
hiddenimports = collect_submodules("keyring.backends")
datas += collect_data_files("mcp")
hiddenimports += collect_submodules("mcp", filter=lambda name: not name.startswith("mcp.cli"))

for package in (
    "alembic",
    "fastapi",
    "uvicorn",
    "genblaze_core",
    "genblaze_s3",
    "genblaze_openai",
    "genblaze_google",
    "genblaze_decart",
    "genblaze_nvidia",
    "genblaze_gmicloud",
    "genblaze_replicate",
    "genblaze_runway",
    "genblaze_luma",
    "genblaze_elevenlabs",
    "genblaze_lmnt",
    "genblaze_hume",
):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

a = Analysis(
    [str(root / "desktop_entry.py")],
    pathex=[str(root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["pytest", "ruff", "tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="thikra-api",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="thikra-api",
)
