# Offline Real-Time Face Replacement

A fully offline Windows desktop app that captures live USB-camera video, detects
and tracks a face in real time, replaces the face with either a **2D image
(PNG/JPG)** or a **3D model (FBX/OBJ/glTF)**, plays music with **pitch/volume**
control, and records the composited result (with the audio you hear) to an
**H.264 MP4**.

> **Status.** Implemented: camera capture, face detection & tracking, 2D image
> replacement, 3D model replacement, audio playback + pitch, snapshots, and
> video recording with muxed audio. **Not yet:** the single-file installer /
> offline-sdk bundle. See [Roadmap](#roadmap).

---

## Requirements

- **Windows 10/11 (64-bit)**
- **Python 3.10 or 3.11** — required. MediaPipe has no wheels for 3.12+/3.14,
  and the stack pins `numpy < 2`.
- A USB camera (UVC / DirectShow compatible)
- NVIDIA GPU recommended but not required for the MVP (runs on CPU)

## Quick start

```powershell
# From the project root:
./run.ps1
```

`run.ps1` creates a local `.venv`, installs `requirements.txt`, and launches the
app. To do it manually:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python source\run_app.py
```

## Using the app

**2D image replacement**
1. Launch — the camera preview starts automatically.
2. **Replacement Object → Mode = 2D image**, then **2D Image → Load PNG/JPG…** —
   pick `test\sample-face-1.png` (bundled).
3. The image tracks your face (position, scale, in-plane rotation). Adjust
   **Opacity / Size / Scale / Rotation / Offset X-Y**.

**3D model replacement**
1. **Replacement Object → Mode = 3D model**, then **3D Model → Load
   FBX/OBJ/glTF…** — pick `test\sample-model-2.obj` (bundled).
2. The model tracks head yaw/pitch/roll. Use **Rotate X/Y/Z**, **Size**,
   **Scale**, **Offset X/Y** to calibrate alignment for your model.
   *(FBX needs the assimp runtime — see [3D models](#3d-models-fb--obj--gltf).)*

**Audio + recording**
1. **Audio → Load MP3/WAV/OGG…**, press **▶ Play**. Set **Volume**, **Pitch
   (0.5x–2.0x)**, **Loop**.
2. **Camera** group — change device / resolution (up to 4K) / FPS, then *Apply*.
3. **Recording → Start Recording** — move around — **Stop**. The MP4 (with the
   audio you heard, muxed in) is written to `recordings\`. **Snapshot** writes a
   PNG to `snapshots\`.

Settings persist between sessions in `config\*.json`. Logs are in
`logs\YYYY-MM-DD.log`.

### Pitch behaviour
Pitch is **varispeed** resampling: 2.0x is an octave up and plays ~2x faster,
0.5x an octave down and slower — the classic musical pitch effect. Pitch that
preserves tempo (phase vocoder / Rubber Band) can be added behind the same
control later.

### 3D models (FBX / OBJ / glTF)
OBJ, glTF and GLB load with no extra native dependencies. **FBX** additionally
needs the **assimp** runtime (`pip install pyassimp` plus the native assimp
library, bundled into `offline-sdk/` for air-gapped installs). If assimp is
absent, convert the FBX to glTF or OBJ once (e.g. in Blender) for a
dependency-free path. 3D rendering uses OpenGL via `moderngl`, so a working GPU
driver is required (the RTX target machine is fine).

## Verify the install (headless)

```powershell
python test\smoke_test.py          # checks overlay + recording + config
python test\make_sample_assets.py  # (re)generate sample-face-1.png / .jpg
```

## Project layout

```
faceswap/
  config/            camera.json, render.json, recording.json, detection.json
  source/
    faceswap/
      capture/       USB camera enumeration + threaded grabber (FR-001)
      detection/     face detection + 6DoF head pose (FaceMesh+solvePnP) (FR-002/003)
      render/        2D overlay + 3D moderngl model renderer (FR-004/005/006)
      recording/     H.264 MP4 recorder + snapshot + audio mux (FR-008/009)
      audio/         playback with pitch/volume/loop + capture tap (FR-007)
      ui/            PySide6 window, worker thread (FR-010)
      engine.py      capture -> detect -> composite -> record pipeline
      config.py, logging_setup.py, paths.py
    run_app.py       launcher
  test/              sample assets (PNG, OBJ) + smoke_test.py
  run.ps1            one-command setup + launch
  requirements.txt
```

## Offline installation (air-gapped target)

Two-machine, fully script-driven — the target needs **nothing** pre-installed
(Python, VC++ runtime and all wheels are bundled).

```powershell
# 1) On a connected Windows x64 machine with Python 3.11:
installer\bundle_offline_sdk.ps1          # fills offline-sdk\

# 2) Copy the whole folder to the offline target, then double-click:
install.bat                                # sets up runtime + deps, verifies

# 3) Launch:
"Launch Face Replacement.bat"
```

Full details, flags and troubleshooting: **[installer/README.md](installer/README.md)**
(the Offline Installation Manual). Verification steps:
**[docs/VERIFICATION_CHECKLIST.md](docs/VERIFICATION_CHECKLIST.md)**.

FFmpeg is provided by the `imageio-ffmpeg` wheel (bundled `ffmpeg.exe`), so no
separate FFmpeg install is required; if ever missing, the recorder falls back to
OpenCV's `mp4v` writer. Bundled third-party licenses:
**[THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md)**.

### Package a single distributable
```powershell
installer\make_release.ps1 -IncludeSdk     # dist\FaceReplacement-<date>.zip
```

## Roadmap

| Requirement | Status |
|---|---|
| FR-001 Camera capture (to 4K, 30/60 FPS) | ✅ done |
| FR-002/003 Face detection & tracking (single face) | ✅ done |
| FR-004 2D image replacement (PNG/JPG) | ✅ done |
| FR-004 3D models (OBJ/glTF/GLB) | ✅ done |
| FR-004 3D models (FBX) | ✅ done (needs assimp runtime) |
| FR-005/006 Alignment & real-time rendering | ✅ done (2D + 3D) |
| FR-007 Audio playback + pitch (0.5x–2.0x, varispeed) | ✅ done |
| FR-008 MP4 H.264 recording with muxed audio | ✅ done |
| FR-009 Snapshot (PNG/JPG) | ✅ done |
| FR-010 UI (preview/controls/status) | ✅ done |
| Single-file installer, offline-sdk bundle | ⬜ planned |
| Multi-face support; pitch preserving tempo; FBX textures | ⬜ future |
