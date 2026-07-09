"""3D model loading and offscreen rendering (FR-004 3D, FR-006).

Loads OBJ / glTF / GLB natively via ``trimesh`` (FBX additionally needs the
``assimp`` runtime). The model is normalised to a unit size and rendered to an
offscreen RGBA framebuffer with ``moderngl``, rotated to match the head pose and
lit by a camera headlight. The resulting transparent sprite is composited onto
the camera frame at the face centre — so screen position/scale come from robust
2D face measurements while yaw/pitch/roll come from the 3D head pose.

The OpenGL context and GPU buffers are created lazily on the thread that first
calls :meth:`render`, because a moderngl context is bound to its creating thread
(the processing worker thread here).
"""
from __future__ import annotations

import math
import threading
from pathlib import Path
from typing import Optional

import numpy as np

from ..detection.head_pose import HeadPose
from ..logging_setup import get_logger
from .compositor import warp_and_blend

log = get_logger("model3d")

_SUPPORTED = {".obj", ".gltf", ".glb", ".fbx", ".ply", ".stl"}

_VERT_SHADER = """
#version 330
uniform mat4 mvp;
uniform mat3 normal_mat;
in vec3 in_pos;
in vec3 in_norm;
in vec3 in_col;
out vec3 v_norm;
out vec3 v_col;
void main() {
    gl_Position = mvp * vec4(in_pos, 1.0);
    v_norm = normalize(normal_mat * in_norm);
    v_col = in_col;
}
"""

_FRAG_SHADER = """
#version 330
in vec3 v_norm;
in vec3 v_col;
out vec4 f_col;
uniform vec3 light_dir;
void main() {
    float d = max(dot(normalize(v_norm), normalize(light_dir)), 0.0);
    float ambient = 0.35;
    float lit = ambient + (1.0 - ambient) * d;
    f_col = vec4(v_col * lit, 1.0);
}
"""


class ModelAsset:
    """CPU-side geometry: interleaved (pos, normal, color) + indices."""

    def __init__(self, interleaved: np.ndarray, indices: np.ndarray, name: str) -> None:
        self.interleaved = interleaved   # (N, 9) float32
        self.indices = indices           # (M,) uint32
        self.name = name

    @classmethod
    def load(cls, path: str | Path) -> "ModelAsset":
        path = Path(path)
        if path.suffix.lower() not in _SUPPORTED:
            raise ValueError(f"Unsupported model type '{path.suffix}'.")
        try:
            import trimesh
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(f"trimesh is required to load 3D models: {exc}")

        try:
            loaded = trimesh.load(str(path), force="mesh", process=False)
        except Exception as exc:
            hint = ""
            if path.suffix.lower() == ".fbx":
                hint = (" FBX requires the assimp runtime (pip install pyassimp with "
                        "the native assimp library), or convert the model to glTF/OBJ.")
            raise ValueError(f"Could not load model '{path.name}': {exc}.{hint}")

        if loaded is None or not hasattr(loaded, "vertices") or len(loaded.vertices) == 0:
            raise ValueError(f"Model '{path.name}' contains no geometry.")

        verts = np.asarray(loaded.vertices, dtype=np.float32)
        faces = np.asarray(loaded.faces, dtype=np.uint32)
        # Compute smooth normals ourselves rather than reading
        # loaded.vertex_normals: trimesh's normal path uses scipy.sparse and
        # prints a fallback traceback when scipy (an optional dep) is absent.
        # Our area-weighted accumulation is equivalent and dependency-free.
        normals = cls._estimate_normals(verts, faces)

        colors = cls._extract_colors(loaded, len(verts))

        # Normalise: centre at origin, fit into a unit sphere.
        centre = verts.mean(axis=0)
        verts = verts - centre
        radius = float(np.linalg.norm(verts, axis=1).max()) or 1.0
        verts = verts / (2.0 * radius)  # radius -> 0.5

        interleaved = np.hstack([verts, normals, colors]).astype("f4")
        log.info("Loaded model '%s' (%d verts, %d faces)", path.name, len(verts), len(faces))
        return cls(interleaved, faces.reshape(-1).astype("u4"), path.name)

    @staticmethod
    def _estimate_normals(verts: np.ndarray, faces: np.ndarray) -> np.ndarray:
        normals = np.zeros_like(verts)
        tris = verts[faces]
        fn = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
        for k in range(3):
            np.add.at(normals, faces[:, k], fn)
        lens = np.linalg.norm(normals, axis=1, keepdims=True)
        lens[lens == 0] = 1.0
        return (normals / lens).astype(np.float32)

    @staticmethod
    def _extract_colors(mesh, n: int) -> np.ndarray:
        default = np.tile(np.array([0.75, 0.72, 0.70], np.float32), (n, 1))
        visual = getattr(mesh, "visual", None)
        if visual is None:
            return default
        try:
            vc = getattr(visual, "vertex_colors", None)
            if vc is not None and len(vc) == n:
                return (np.asarray(vc, np.float32)[:, :3] / 255.0)
            # Solid material base colour.
            mat = getattr(visual, "material", None)
            base = getattr(mat, "baseColorFactor", None) if mat else None
            if base is not None:
                c = np.asarray(base, np.float32)[:3]
                if c.max() > 1.0:
                    c = c / 255.0
                return np.tile(c, (n, 1))
        except Exception:  # pragma: no cover
            pass
        return default


# -- matrix helpers (OpenGL M @ vec convention) ------------------------------
def _perspective(fovy_rad: float, aspect: float, near: float, far: float) -> np.ndarray:
    f = 1.0 / math.tan(fovy_rad / 2.0)
    m = np.zeros((4, 4), np.float64)
    m[0, 0] = f / aspect
    m[1, 1] = f
    m[2, 2] = (far + near) / (near - far)
    m[2, 3] = (2 * far * near) / (near - far)
    m[3, 2] = -1.0
    return m


def _look_at(eye, target, up) -> np.ndarray:
    eye, target, up = map(lambda v: np.asarray(v, np.float64), (eye, target, up))
    f = target - eye
    f /= np.linalg.norm(f)
    s = np.cross(f, up)
    s /= np.linalg.norm(s)
    u = np.cross(s, f)
    m = np.eye(4)
    m[0, :3], m[1, :3], m[2, :3] = s, u, -f
    m[:3, 3] = -m[:3, :3] @ eye
    return m


def _euler_matrix(rx, ry, rz) -> np.ndarray:
    rx, ry, rz = map(math.radians, (rx, ry, rz))
    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)
    Rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    Rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return Rz @ Ry @ Rx


# Convert an OpenCV rotation (Y down, +Z into scene) to OpenGL (Y up, -Z).
_CV2GL = np.diag([1.0, -1.0, -1.0])


class ModelRenderer:
    """Renders the active model to an RGBA sprite and composites it."""

    def __init__(self, render_size: int = 512) -> None:
        self.render_size = int(render_size)
        self._asset: Optional[ModelAsset] = None
        self._asset_dirty = False
        self._lock = threading.Lock()

        self._ctx = None
        self._prog = None
        self._fbo = None
        self._vao = None
        self._vbo = None
        self._ibo = None
        self._gl_failed: Optional[str] = None

        # Fixed camera: unit model fills most of the frame.
        self._proj = _perspective(math.radians(45.0), 1.0, 0.1, 10.0)
        self._view = _look_at((0, 0, 1.8), (0, 0, 0), (0, 1, 0))

    # -- model management ----------------------------------------------------
    def set_model(self, asset: ModelAsset) -> None:
        with self._lock:
            self._asset = asset
            self._asset_dirty = True

    @property
    def has_model(self) -> bool:
        return self._asset is not None

    # -- GL lifecycle (lazy, on the render thread) ---------------------------
    def _ensure_gl(self) -> bool:
        if self._gl_failed:
            return False
        if self._ctx is None:
            try:
                import moderngl
                self._ctx = moderngl.create_standalone_context()
                self._prog = self._ctx.program(
                    vertex_shader=_VERT_SHADER, fragment_shader=_FRAG_SHADER
                )
                size = (self.render_size, self.render_size)
                self._color = self._ctx.texture(size, 4)
                self._depth = self._ctx.depth_renderbuffer(size)
                self._fbo = self._ctx.framebuffer(
                    color_attachments=[self._color], depth_attachment=self._depth
                )
                self._ctx.enable(moderngl.DEPTH_TEST)
            except Exception as exc:
                self._gl_failed = str(exc)
                log.error("Could not create OpenGL context for 3D rendering: %s", exc)
                return False

        if self._asset_dirty and self._asset is not None:
            self._upload_asset()
        return self._vao is not None

    def _upload_asset(self) -> None:
        import moderngl  # noqa: F401
        if self._vbo is not None:
            self._vbo.release()
            self._ibo.release()
            self._vao.release()
        asset = self._asset
        self._vbo = self._ctx.buffer(asset.interleaved.tobytes())
        self._ibo = self._ctx.buffer(asset.indices.tobytes())
        self._vao = self._ctx.vertex_array(
            self._prog,
            [(self._vbo, "3f 3f 3f", "in_pos", "in_norm", "in_col")],
            index_buffer=self._ibo,
        )
        self._asset_dirty = False

    # -- rendering -----------------------------------------------------------
    def render_sprite(self, rot_cv: np.ndarray, rot_offset=(0.0, 0.0, 0.0)) -> Optional[np.ndarray]:
        """Render the model to an RGBA uint8 image, or ``None`` if GL failed."""
        if not self._ensure_gl():
            return None

        r_gl = _CV2GL @ np.asarray(rot_cv, np.float64) @ _CV2GL
        model = np.eye(4)
        model[:3, :3] = _euler_matrix(*rot_offset) @ r_gl

        mv = self._view @ model
        mvp = self._proj @ mv
        normal_mat = mv[:3, :3]

        self._fbo.use()
        self._ctx.clear(0.0, 0.0, 0.0, 0.0)
        self._prog["mvp"].write(np.ascontiguousarray(mvp.T, dtype="f4").tobytes())
        self._prog["normal_mat"].write(np.ascontiguousarray(normal_mat.T, dtype="f4").tobytes())
        self._prog["light_dir"].value = (0.0, 0.0, 1.0)  # camera headlight
        self._vao.render()

        raw = self._fbo.read(components=4, dtype="f1")
        img = np.frombuffer(raw, np.uint8).reshape(self.render_size, self.render_size, 4)
        return np.flipud(img).copy()  # GL origin is bottom-left

    def composite(
        self,
        frame_bgr: np.ndarray,
        pose: HeadPose,
        *,
        opacity: float = 1.0,
        scale: float = 1.0,
        size_factor: float = 1.6,
        offset_x: float = 0.0,
        offset_y: float = 0.0,
        rot_offset=(0.0, 0.0, 0.0),
    ) -> np.ndarray:
        rgba = self.render_sprite(pose.rotation_matrix, rot_offset)
        if rgba is None:
            return frame_bgr
        src_bgr = rgba[:, :, [2, 1, 0]].astype(np.float32)
        src_alpha = (rgba[:, :, 3:4].astype(np.float32)) / 255.0

        target_w = max(pose.width * size_factor * scale, 1.0)
        cx = pose.cx + offset_x * pose.width
        cy = pose.cy + offset_y * pose.width
        # Head roll is already applied in 3D, so no extra 2D rotation.
        return warp_and_blend(frame_bgr, src_bgr, src_alpha, cx, cy, target_w, 0.0, opacity)

    def shutdown(self) -> None:
        for obj in (self._vao, self._vbo, self._ibo, self._fbo):
            try:
                if obj is not None:
                    obj.release()
            except Exception:  # pragma: no cover
                pass
        try:
            if self._ctx is not None:
                self._ctx.release()
        except Exception:  # pragma: no cover
            pass
