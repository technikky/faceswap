# Verification Checklist

Perform on a fresh offline machine using only the provided deliverables
(PRS section 8). Tick each item.

## Install
- [ ] Copied the project folder (with populated `offline-sdk/`) to the target.
- [ ] Ran `install.bat` — completed without errors.
- [ ] `install.bat` verification step printed **ALL CHECKS PASSED**.
- [ ] `Launch Face Replacement.bat` was created at the project root.

## Headless verification
- [ ] `installer\verify.ps1` → **VERIFICATION PASSED**.

## Live verification (USB camera connected)
- [ ] Launched the app; camera preview appears within ~5 s.
- [ ] **Camera**: device list is populated; changing resolution/FPS + *Apply* works.
- [ ] **Camera**: 4K (3840×2160) selectable when the camera supports it.
- [ ] Face is detected and tracked under normal head movement.

### 2D image (FR-004)
- [ ] Mode = *2D image* → load `test\sample-face-1.png` → mask tracks the face.
- [ ] Load a `.jpg` → renders correctly.
- [ ] Opacity / Size / Scale / Rotation / Offset sliders behave as expected.

### 3D model (FR-004)
- [ ] Mode = *3D model* → load `test\sample-model-2.obj` → model tracks the head.
- [ ] Rotate X/Y/Z, Size, Scale, Offset sliders calibrate alignment.
- [ ] (If applicable) FBX model loads with the assimp runtime present.

### Audio (FR-007)
- [ ] Load `.mp3` / `.wav` / `.ogg` → **Play / Pause / Stop / Loop** work.
- [ ] **Volume** and **Pitch (0.5×–2.0×)** change the sound in real time.

### Recording (FR-008 / FR-009)
- [ ] Start recording → move in front of the camera → Stop.
- [ ] Output MP4 appears in `recordings\` and plays back.
- [ ] MP4 contains **synchronized video and audio**.
- [ ] **Snapshot** writes an image to `snapshots\`.

## Stability (spot check)
- [ ] App runs for an extended session without crashing.
- [ ] Disconnecting the camera shows an error and recovers on reconnect (no crash).

## Offline guarantee
- [ ] The entire workflow above completed with **no internet connection**.
