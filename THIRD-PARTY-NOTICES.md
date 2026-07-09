# Third-Party Notices

This application bundles the following third-party components (PRS section 13).
Each package's full license text is included in its `*.dist-info/` directory
inside `offline-sdk/python-wheels/` (or the installed `site-packages/`).

| Component | Purpose | License |
|---|---|---|
| Python (embeddable runtime) | Interpreter | PSF License |
| NumPy | Array math | BSD-3-Clause |
| OpenCV (opencv-python) | Camera capture, image ops | Apache-2.0 |
| MediaPipe | Face detection / face mesh | Apache-2.0 |
| PySide6 (Qt for Python) | GUI | LGPL-3.0 |
| imageio-ffmpeg (FFmpeg binary) | H.264 encode / audio mux | FFmpeg: LGPL-2.1+ / GPL-2+ (build-dependent); wrapper: BSD-2-Clause |
| soundfile (libsndfile) | Audio decode (WAV/OGG/MP3) | soundfile: BSD-3-Clause; libsndfile: LGPL-2.1 |
| sounddevice (PortAudio) | Audio playback | sounddevice: MIT; PortAudio: MIT |
| trimesh | 3D model loading (OBJ/glTF) | MIT |
| moderngl | OpenGL rendering | MIT |
| pyassimp / assimp (optional, FBX) | FBX model loading | assimp: BSD-3-Clause |

Notes:
- **Qt / PySide6 is LGPL-3.0.** This application uses PySide6 as a dynamically
  linked library without modification, consistent with the LGPL. If you
  redistribute, keep PySide6 replaceable and include its license.
- **FFmpeg licensing depends on the build** shipped by `imageio-ffmpeg`. Verify
  the specific build's license if you redistribute commercially; consider an
  LGPL FFmpeg build if GPL is a concern.
- To regenerate an exact, per-version license report after bundling:
  `python -m pip install pip-licenses && pip-licenses --format=markdown` (run on
  a connected machine, then copy the output here).
