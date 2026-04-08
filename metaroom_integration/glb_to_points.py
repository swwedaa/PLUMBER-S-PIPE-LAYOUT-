"""
Sample a point cloud from a GLB/GLTF file using Open3D.

Designed for Open3D 0.19.x (same family as pipe-layout-backend on Windows).
Copy this file next to app.py and import load_points_from_mesh_file.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

try:
    import open3d as o3d
except ImportError as exc:  # pragma: no cover
    raise ImportError("open3d is required for GLB sampling") from exc


def load_points_from_mesh_file(
    path: str | Path,
    *,
    max_points: int = 50_000,
    min_points: int = 1_000,
) -> np.ndarray:
    """
    Load a mesh from disk and uniformly sample surface points.

    Returns an (N, 3) float64 numpy array of XYZ points in the mesh's
    coordinate system (same units as the source file — often meters).

    Raises ValueError if the file cannot be read or the mesh is empty.
    """
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in {".glb", ".gltf", ".obj", ".ply", ".stl"}:
        # Open3D may still read other formats; we mainly document GLB for MetaRoom.
        pass

    mesh = o3d.io.read_triangle_mesh(str(path))
    if mesh.is_empty() or len(mesh.vertices) == 0:
        raise ValueError(f"Empty or invalid mesh: {path}")

    mesh.compute_vertex_normals()

    # Aim for enough points for DBSCAN; cap for HTTP / mobile uploads.
    n_tri = len(mesh.triangles)
    heuristic = max(min_points, min(max_points, max(min_points, n_tri * 8)))
    n_samples = int(min(max_points, max(heuristic, min_points)))

    pcd = mesh.sample_points_uniformly(number_of_points=n_samples)
    pts = np.asarray(pcd.points, dtype=np.float64)
    if pts.size == 0:
        raise ValueError(f"Sampling produced no points: {path}")
    return pts


def points_to_json_list(pts: np.ndarray) -> list[list[float]]:
    """Convert (N,3) ndarray to nested Python floats for FastAPI / JSON."""
    if pts.ndim != 2 or pts.shape[1] != 3:
        raise ValueError("pts must have shape (N, 3)")
    return pts.astype(float).tolist()
