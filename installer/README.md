# Offline Installation Manual

This describes the two-machine workflow for installing the application with **no
internet access on the target** (PRS sections 6, 12, 16).

```
  [ connected machine ]                         [ offline target ]
  bundle_offline_sdk.ps1   --- copy folder --->  install.bat
        |                                              |
   fills offline-sdk/                          sets up runtime + deps,
                                               verifies, writes launcher
```

## A. Bundle (once, on a connected Windows x64 machine with Python 3.11)

```powershell
# from the project root
installer\bundle_offline_sdk.ps1
```

This downloads into `offline-sdk/`:
- **python-wheels/** — every wheel for `requirements.txt` (incl. FFmpeg via
  `imageio-ffmpeg`), plus pip/setuptools/wheel.
- **python/embed/** — the Windows embeddable Python runtime, and `get-pip.py`.
- **vcredist/** — the Visual C++ 2015–2022 x64 runtime.
- **manifest.json** — versions and file list.

> Bundle with the **same Python minor version** (3.10 or 3.11, 64-bit) the target
> will use. Wheels are version- and platform-specific.

Optional flags: `-PythonVersion`, `-EmbedUrl`, `-GetPipUrl`, `-VcRedistUrl` to
pin exact versions / mirrors.

## B. Deploy

Copy the **entire project folder** (including the populated `offline-sdk/`) to
the target machine — USB drive, network share, etc. Or build a single ZIP:

```powershell
installer\make_release.ps1 -IncludeSdk     # dist\FaceReplacement-<date>.zip
```

## C. Install (on the offline target — no internet)

Double-click **`install.bat`** (or run `installer\install.ps1`). It:
1. Installs the VC++ runtime quietly (skips if already present).
2. Provisions Python — the bundled embeddable runtime into `runtime\`
   (fallback: an existing 3.10/3.11 → `.venv\`).
3. Installs all dependencies **offline** (`pip --no-index --find-links`).
4. Runs verification (`test\smoke_test.py`).
5. Writes **`Launch Face Replacement.bat`** at the project root.

Flags: `-SystemPython` (force an installed Python instead of the embeddable one),
`-SkipVerify`.

## D. Verify & launch

```powershell
installer\verify.ps1          # re-run the headless checks any time
```
Then connect a USB camera and double-click **`Launch Face Replacement.bat`**.
Follow the checklist in `../docs/VERIFICATION_CHECKLIST.md`.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `offline-sdk\python-wheels not found` | Run `bundle_offline_sdk.ps1` first (step A). |
| pip install fails: "No matching distribution" | Bundle was built with a different Python minor version or platform. Re-bundle on 3.10/3.11 x64. |
| App starts but 3D mode shows nothing / GL error | GPU/OpenGL driver missing on target. Update the GPU driver; 2D mode still works. |
| Audio error on Play | No default output device / PortAudio issue. Check Windows sound settings. |
| FBX won't load | FBX needs the assimp runtime — add `pyassimp` before bundling, or convert to glTF/OBJ. |
| `install.bat` closes instantly | Run from a terminal to read the error: `powershell -ExecutionPolicy Bypass -File installer\install.ps1`. |
