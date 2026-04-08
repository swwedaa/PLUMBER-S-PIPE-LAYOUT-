"""
Full Pipe Backend app.py — includes /process_room (room metrics).

Copy this file to C:\\pipe-layout-backend\\app.py (or merge carefully).
"""
import json
import re
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import open3d as o3d
import torch
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from glb_to_points import load_points_from_mesh_file, points_to_json_list
from pydantic import BaseModel, Field

APP_VERSION = "0.2.0"


def _select_torch_device() -> str:
    if torch.cuda.is_available():
        return "cuda"
    mps = getattr(torch.backends, "mps", None)
    if mps is not None and mps.is_available():
        return "mps"
    return "cpu"


DEVICE = _select_torch_device()


def _torch_gpu_label() -> Optional[str]:
    if DEVICE == "cuda" and torch.cuda.is_available():
        return torch.cuda.get_device_name(0)
    if DEVICE == "mps":
        return "mps"
    return None


THRESHOLDS = {
    "voxel_size": 0.003,
    "length_min_m": 0.2,
    "length_max_m": 20.0,
    "radius_min_m": 0.003,
    "radius_max_m": 0.20,
    "elongation_min": 1.2,
}

app = FastAPI(title="Pipe Backend")
BASE_DIR = Path(__file__).resolve().parent
SCANS_DIR = BASE_DIR / "scans"
SCANS_DIR.mkdir(exist_ok=True)


class ScanMeta(BaseModel):
    job_name: Optional[str] = None
    operator: Optional[str] = None
    site_address: Optional[str] = None
    room_voxel_m: Optional[float] = Field(
        default=None,
        gt=0,
        description="Voxel size (m) for /process_room downsampling",
    )
    room_height_z_percentile_low: Optional[float] = Field(
        default=None,
        ge=0,
        le=25,
        description="Lower percentile for vertical span (after floor align), default 1.0",
    )
    room_height_z_percentile_high: Optional[float] = Field(
        default=None,
        ge=75,
        le=100,
        description="Upper Z percentile for height (default 97; lower 95 trims more ceiling outliers)",
    )


class ScanRequest(BaseModel):
    points: List[List[float]] = Field(..., min_length=200)
    meta: Optional[ScanMeta] = None


class SaveScanRequest(BaseModel):
    points: List[List[float]] = Field(..., min_length=1)
    tag: Optional[str] = None
    meta: Optional[ScanMeta] = None


class ProcessScanFileRequest(BaseModel):
    file_path: str


def _meta_dict(meta: Optional[ScanMeta]) -> Dict[str, Any]:
    if meta is None:
        return {}
    return {k: v for k, v in meta.model_dump().items() if v not in (None, "")}


def _validate_points(points: List[List[float]]) -> np.ndarray:
    pts = np.asarray(points, dtype=np.float32)
    if pts.ndim != 2 or pts.shape[1] != 3:
        raise ValueError("points must be [[x,y,z], ...]")
    if pts.shape[0] < 1:
        raise ValueError("points cannot be empty")
    return pts


def _normalize(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v)
    if n < 1e-12:
        raise ValueError("zero-length normal")
    return (v / n).astype(np.float64)


def _rotation_align_a_to_b(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """3x3 rotation R with R @ a aligned to b (a, b unit vectors)."""
    a = _normalize(a)
    b = _normalize(b)
    v = np.cross(a, b)
    c = float(np.dot(a, b))
    s = np.linalg.norm(v)
    if s < 1e-12:
        if c > 0:
            return np.eye(3, dtype=np.float64)
        orth = np.array([1.0, 0.0, 0.0], dtype=np.float64)
        if abs(float(np.dot(a, orth))) > 0.9:
            orth = np.array([0.0, 1.0, 0.0], dtype=np.float64)
        orth = _normalize(np.cross(a, orth))
        return o3d.geometry.get_rotation_matrix_from_axis_angle(orth * np.pi)

    vx = np.array(
        [[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]], dtype=np.float64
    )
    return np.eye(3, dtype=np.float64) + vx + vx @ vx * ((1.0 - c) / (s * s))


def _height_from_z_percentiles(
    cloud: np.ndarray, z_p_low: float, z_p_high: float
) -> tuple[float, float, float, float]:
    """
    Vertical extent from Z percentiles (reduces ceiling/floor mesh outliers vs raw min/max).
    Returns (height_m, z_low, z_high, height_minmax_m).
    """
    z = cloud[:, 2].astype(np.float64)
    if z.size < 10:
        z_lo = z_hi = float(np.median(z))
        span = float(z.max() - z.min()) if z.size else 0.0
        return max(0.0, z_hi - z_lo), z_lo, z_hi, span
    z_lo = float(np.percentile(z, z_p_low))
    z_hi = float(np.percentile(z, z_p_high))
    if z_hi <= z_lo:
        z_hi = z_lo + 1e-6
    span = float(z.max() - z.min())
    return z_hi - z_lo, z_lo, z_hi, span


def run_room_metrics(
    points: np.ndarray,
    *,
    voxel_m: float = 0.02,
    max_points: int = 200_000,
    world_up: Optional[np.ndarray] = None,
    z_percentile_low: float = 1.0,
    z_percentile_high: float = 97.0,
) -> Dict[str, Any]:
    if world_up is None:
        world_up = np.array([0.0, 1.0, 0.0], dtype=np.float64)
    world_up = _normalize(world_up.astype(np.float64))

    z_pl = float(np.clip(z_percentile_low, 0.0, 49.0))
    z_ph = float(np.clip(z_percentile_high, 51.0, 100.0))
    if z_ph <= z_pl:
        z_ph = min(100.0, z_pl + 1.0)

    t0 = time.perf_counter()
    debug: Dict[str, Any] = {
        "input_points": int(points.shape[0]),
        "downsampled_points": 0,
        "used_voxel_m": float(voxel_m),
        "plane_attempts": 0,
        "floor_inliers": 0,
        "plane_model": None,
        "floor_normal_world": None,
        "height_z_percentile_low": z_pl,
        "height_z_percentile_high": z_ph,
    }

    wpcd = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(points.astype(np.float64)))
    voxel_cur = float(voxel_m)
    ds_pcd = wpcd.voxel_down_sample(voxel_size=voxel_cur)
    ds = np.asarray(ds_pcd.points, dtype=np.float64)
    if ds.shape[0] < 50:
        raise ValueError("too few points after room downsample")

    while ds.shape[0] > max_points:
        voxel_cur *= 1.35
        ds_pcd = wpcd.voxel_down_sample(voxel_size=voxel_cur)
        ds = np.asarray(ds_pcd.points, dtype=np.float64)
        debug["used_voxel_m"] = float(voxel_cur)
    debug["downsampled_points"] = int(ds.shape[0])

    pcd_iter = ds_pcd
    floor_ok = False
    normal_w: Optional[np.ndarray] = None
    plane_model: Optional[Any] = None
    inliers_np = np.array([], dtype=np.int64)

    for attempt in range(5):
        debug["plane_attempts"] = attempt + 1
        if len(pcd_iter.points) < 50:
            break
        pm, inliers = pcd_iter.segment_plane(
            distance_threshold=0.03, ransac_n=3, num_iterations=2000
        )
        inliers_np = np.asarray(inliers, dtype=np.int64)
        a, b, c, d = pm
        n = _normalize(np.array([a, b, c], dtype=np.float64))
        if abs(float(np.dot(n, world_up))) < 0.85:
            pcd_iter = pcd_iter.select_by_index(inliers, invert=True)
            continue
        if float(np.dot(n, world_up)) < 0:
            n = -n
        normal_w = n
        plane_model = pm
        floor_ok = True
        break

    def _box_from_xy(cloud: np.ndarray) -> Dict[str, float]:
        span_x = float(cloud[:, 0].max() - cloud[:, 0].min())
        span_y = float(cloud[:, 1].max() - cloud[:, 1].min())
        length_m = max(span_x, span_y)
        width_m = min(span_x, span_y)
        height_m, _, _, height_minmax = _height_from_z_percentiles(cloud, z_pl, z_ph)
        debug["height_m_span_minmax"] = height_minmax
        return {
            "length_m": length_m,
            "width_m": width_m,
            "height_m": height_m,
            "floor_area_m2": length_m * width_m,
            "volume_m3": length_m * width_m * height_m,
        }

    if not floor_ok or normal_w is None:
        b = _box_from_xy(ds)
        debug["floor_inliers"] = int(len(inliers_np))
        debug["plane_model"] = list(plane_model) if plane_model is not None else None
        elapsed = int((time.perf_counter() - t0) * 1000)
        return {
            "room": {
                **b,
                "floor_ok": False,
                "up_axis": "y",
            },
            "debug": debug,
            "processing_ms": elapsed,
            "device": DEVICE,
        }

    R = _rotation_align_a_to_b(
        normal_w, np.array([0.0, 0.0, 1.0], dtype=np.float64)
    )
    pts_all = (R @ ds.T).T
    inlier_pts = np.asarray(pcd_iter.points)[inliers_np]
    inlier_rot = (R @ inlier_pts.T).T
    floor_z = float(np.median(inlier_rot[:, 2]))
    pts_all[:, 2] -= floor_z

    b = _box_from_xy(pts_all)
    debug["floor_inliers"] = int(len(inliers_np))
    debug["plane_model"] = list(plane_model) if plane_model is not None else None
    debug["floor_normal_world"] = [
        float(normal_w[0]),
        float(normal_w[1]),
        float(normal_w[2]),
    ]

    elapsed = int((time.perf_counter() - t0) * 1000)
    return {
        "room": {
            **b,
            "floor_ok": True,
            "up_axis": "y",
        },
        "debug": debug,
        "processing_ms": elapsed,
        "device": DEVICE,
    }


def _safe_tag(tag: Optional[str]) -> str:
    raw = (tag or "scan").strip()
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", raw)
    return cleaned[:40] or "scan"


def pca_pipe(cluster: np.ndarray) -> Optional[Dict[str, Any]]:
    if cluster.shape[0] < 30:
        return None

    x = torch.from_numpy(cluster.astype(np.float32)).to(DEVICE)
    center = x.mean(dim=0)
    y = x - center
    cov = (y.T @ y) / max(1, y.shape[0] - 1)
    eigvals, eigvecs = torch.linalg.eigh(cov)

    axis = eigvecs[:, 2]
    proj = y @ axis

    p0 = center + axis * torch.min(proj)
    p1 = center + axis * torch.max(proj)
    length = torch.linalg.norm(p1 - p0).item()

    projected = center + torch.outer(proj, axis)
    radial = torch.linalg.norm(x - projected, dim=1)
    radius = torch.quantile(radial, 0.5).item()
    diameter = radius * 2.0

    major = torch.sqrt(torch.clamp(eigvals[2], min=1e-9)).item()
    minor = torch.sqrt(torch.clamp((eigvals[0] + eigvals[1]) * 0.5, min=1e-9)).item()
    elongation = major / (minor + 1e-6)

    if not (THRESHOLDS["length_min_m"] <= length <= THRESHOLDS["length_max_m"]):
        return None
    if not (THRESHOLDS["radius_min_m"] <= radius <= THRESHOLDS["radius_max_m"]):
        return None
    if elongation < THRESHOLDS["elongation_min"]:
        return None

    confidence = float(
        max(0.1, min(0.99, (elongation / 10.0) + min(0.2, cluster.shape[0] / 50000.0)))
    )

    return {
        "start": [float(p0[0]), float(p0[1]), float(p0[2])],
        "end": [float(p1[0]), float(p1[1]), float(p1[2])],
        "length_meters": float(length),
        "diameter_meters": float(diameter),
        "elongation": float(elongation),
        "confidence": confidence,
    }


def run_detection(points: np.ndarray) -> Dict[str, Any]:
    t0 = time.time()

    pcd = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(points))
    pcd = pcd.voxel_down_sample(voxel_size=THRESHOLDS["voxel_size"])
    ds = np.asarray(pcd.points, dtype=np.float32)

    if ds.shape[0] < 30:
        raise ValueError("too few points after downsample")

    pipes: List[Dict[str, Any]] = []
    debug: Dict[str, Any] = {
        "input_points": int(points.shape[0]),
        "downsampled_points": int(ds.shape[0]),
        "cluster_count": 0,
        "used_eps": None,
        "used_min_points": None,
        "fallback_whole_cloud": False,
    }

    for eps, min_pts in [(0.02, 12), (0.03, 10), (0.05, 8), (0.08, 6)]:
        labels = np.array(pcd.cluster_dbscan(eps=eps, min_points=min_pts, print_progress=False))
        valid = [lb for lb in np.unique(labels) if lb >= 0]
        if not valid:
            continue

        debug["cluster_count"] = len(valid)
        debug["used_eps"] = eps
        debug["used_min_points"] = min_pts

        for lb in valid:
            c = ds[labels == lb]
            candidate = pca_pipe(c)
            if candidate:
                pipes.append(candidate)

        if pipes:
            break

    if not pipes:
        whole = pca_pipe(ds)
        if whole:
            pipes.append(whole)
            debug["fallback_whole_cloud"] = True

    pipes.sort(key=lambda p: p["confidence"], reverse=True)
    elapsed = int((time.time() - t0) * 1000)

    return {
        "pipe_count": len(pipes),
        "pipes": pipes[:20],
        "processing_ms": elapsed,
        "device": DEVICE,
        "debug": debug,
    }


@app.get("/health")
def health():
    return {
        "ok": True,
        "app_version": APP_VERSION,
        "device": DEVICE,
        "gpu": _torch_gpu_label(),
    }


@app.get("/version")
def version():
    return {
        "app_version": APP_VERSION,
        "device": DEVICE,
        "thresholds": THRESHOLDS,
        "scans_dir": str(SCANS_DIR),
    }


@app.get("/list_scans")
def list_scans(limit: int = 20):
    files = sorted(SCANS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    out = []
    for p in files[: max(1, min(limit, 200))]:
        out.append(
            {
                "file_path": str(p),
                "size_bytes": p.stat().st_size,
                "modified_utc": datetime.utcfromtimestamp(p.stat().st_mtime).isoformat()
                + "Z",
            }
        )
    return {"count": len(out), "scans": out}


@app.post("/save_scan")
def save_scan(req: SaveScanRequest):
    try:
        pts = _validate_points(req.points)
        tag = _safe_tag(req.tag)
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
        file_path = SCANS_DIR / f"{tag}_{ts}.json"

        meta = _meta_dict(req.meta)
        payload = {
            "points": pts.tolist(),
            "meta": {
                "created_utc": datetime.utcnow().isoformat() + "Z",
                "point_count": int(pts.shape[0]),
                "tag": tag,
                **meta,
            },
        }

        with file_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f)

        return {
            "ok": True,
            "file_path": str(file_path),
            "point_count": int(pts.shape[0]),
            "meta": meta,
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


def run_pipe_detection(
    points: List[List[float]], meta: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Core pipe detection: Python lists + meta → result dict.
    """
    meta = meta or {}
    pts = _validate_points(points)
    if pts.shape[0] < 200:
        raise ValueError("need at least 200 points")
    result = run_detection(pts)
    result["meta"] = meta
    return result


@app.post("/process_scan")
def process_scan(req: ScanRequest):
    try:
        meta_dict = _meta_dict(req.meta)
        return run_pipe_detection(req.points, meta_dict)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/process_room")
def process_room(req: ScanRequest):
    try:
        meta_dict = _meta_dict(req.meta)
        pts = _validate_points(req.points)
        if pts.shape[0] < 200:
            raise ValueError("need at least 200 points")
        voxel = 0.02
        z_pl, z_ph = 1.0, 97.0
        if req.meta is not None:
            if req.meta.room_voxel_m is not None:
                voxel = float(req.meta.room_voxel_m)
            if req.meta.room_height_z_percentile_low is not None:
                z_pl = float(req.meta.room_height_z_percentile_low)
            if req.meta.room_height_z_percentile_high is not None:
                z_ph = float(req.meta.room_height_z_percentile_high)
        out = run_room_metrics(
            pts.astype(np.float64),
            voxel_m=voxel,
            z_percentile_low=z_pl,
            z_percentile_high=z_ph,
        )
        out["meta"] = meta_dict
        return out
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/process_scan_file")
def process_scan_file(req: ProcessScanFileRequest):
    try:
        p = Path(req.file_path)
        if not p.is_absolute():
            p = (BASE_DIR / p).resolve()

        if not p.exists():
            raise ValueError(f"file not found: {p}")

        with p.open("r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, dict) and "points" in data:
            points = data["points"]
            source_meta = data.get("meta", {})
        elif isinstance(data, list):
            points = data
            source_meta = {}
        else:
            raise ValueError("JSON must be a list of points or an object with 'points'")

        pts = _validate_points(points)
        if pts.shape[0] < 200:
            raise ValueError("need at least 200 points")

        result = run_detection(pts)
        result["source_file"] = str(p)
        result["meta"] = source_meta if isinstance(source_meta, dict) else {}
        return result

    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/process_glb")
async def process_glb(
    file: UploadFile = File(..., description="MetaRoom or other GLB/GLTF export"),
    meta: Optional[str] = Form(None),
    max_points: int = Form(50_000),
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing filename")

    suffix = Path(file.filename).suffix.lower()
    if suffix not in {".glb", ".gltf"}:
        raise HTTPException(
            status_code=400,
            detail="Expected .glb or .gltf (MetaRoom export)",
        )

    meta_dict: Dict[str, Any] = {}
    if meta:
        try:
            meta_dict = json.loads(meta)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid meta JSON: {exc}") from exc

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty file")

    tmp_path: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(raw)
            tmp_path = tmp.name

        pts = load_points_from_mesh_file(
            tmp_path, max_points=max(200, min(max_points, 200_000))
        )
        points = points_to_json_list(pts)
        result = run_pipe_detection(points, meta_dict)
        if isinstance(result, dict):
            result.setdefault("source", "glb_mesh_sample")
            result.setdefault("sampled_points", len(points))
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)
