# Step 2 — MetaRoom GLB → PC → pipe measurements

Your Flutter app previews `.glb` locally. This adds a **Windows FastAPI** path:

**`POST /process_glb`** — upload a `.glb` file → sample a point cloud with **Open3D** → run your **existing** `process_scan` logic (DBSCAN / PCA, etc.).

## 1. Windows backend — files

1. Copy `glb_to_points.py` into your backend folder (same place as `app.py`), e.g.  
   `C:\pipe-layout-backend\glb_to_points.py`.

2. Ensure dependencies:

```text
python-multipart
open3d==0.19.0
```

Install in your venv:

```powershell
.\.venv\Scripts\pip install python-multipart
```

## 2. Wire FastAPI (merge into `app.py`)

Add imports at the top (adjust if your app already imports some of these):

```python
import json
import tempfile
from pathlib import Path

from fastapi import File, Form, HTTPException, UploadFile

from glb_to_points import load_points_from_mesh_file, points_to_json_list
```

Assume you already have a function that takes `points: list[list[float]]` and `meta: dict` and returns the same dict you return from `POST /process_scan`. Examples of names: `detect_pipes`, `run_process_scan`, or inline body from your existing endpoint. **Replace `_YOUR_CORE_FUNCTION_`** below with that call.

```python
@app.post("/process_glb")
async def process_glb(
    file: UploadFile = File(..., description="MetaRoom or other GLB/GLTF export"),
    meta: str | None = Form(None),
    max_points: int = Form(50_000),
):
    """
    Accept multipart upload, sample mesh to points, reuse process_scan pipeline.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing filename")

    suffix = Path(file.filename).suffix.lower()
    if suffix not in {".glb", ".gltf"}:
        raise HTTPException(
            status_code=400,
            detail="Expected .glb or .gltf (MetaRoom export)",
        )

    meta_dict: dict = {}
    if meta:
        try:
            meta_dict = json.loads(meta)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid meta JSON: {exc}") from exc

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty file")

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(raw)
            tmp_path = tmp.name

        pts = load_points_from_mesh_file(tmp_path, max_points=max(200, min(max_points, 200_000)))
        points = points_to_json_list(pts)

        # --- call your existing scan pipeline (same as /process_scan body) ---
        result = _YOUR_CORE_FUNCTION_(points, meta_dict)
        # e.g. result = process_scan_points(points, meta_dict)
        if isinstance(result, dict):
            result.setdefault("source", "glb_mesh_sample")
            result.setdefault("sampled_points", len(points))
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)
```

**Integration note:** Open your current `POST /process_scan` handler, find where it parses `points` and `meta`, and extract that into one shared function so `/process_scan` and `/process_glb` both call it. That avoids duplicating detection logic.

## 3. Flutter — multipart upload button

Add `http.MultipartRequest` (you already use `package:http/http.dart`).

```dart
Future<void> _processGlbOnBackend(File glbFile) async {
  final uri = _uri('/process_glb');
  final req = http.MultipartRequest('POST', uri);
  req.files.add(await http.MultipartFile.fromPath('file', glbFile.path));
  req.fields['meta'] = json.encode(_metaPayload());
  req.fields['max_points'] = '50000';

  final streamed = await req.send().timeout(const Duration(seconds: 180));
  final resp = await http.Response.fromStream(streamed);
  final body = resp.body.isEmpty ? '{}' : resp.body;
  if (resp.statusCode < 200 || resp.statusCode >= 300) {
    throw Exception('process_glb failed HTTP ${resp.statusCode}: $body');
  }
  final decoded = json.decode(body) as Map<String, dynamic>;
  final enriched = _enrichWithRodMetadata(decoded);
  _updatePipeCardsFromResponse(enriched);
  await _setOutput({
    'endpoint': '/process_glb',
    'response': enriched,
  });
}
```

- After the file picker gives you a **path** to the GLB, pass that `File` here (or copy to temp with a **safe ASCII name** first, same as your ModelViewer fix).
- Increase timeout for large GLBs.

## 4. Caveats (plumber-friendly truth)

- Mesh exports are **not** the same as a raw LiDAR point cloud; sampling is **dense on surfaces**, not a full room scan unless the GLB includes all geometry.
- Scale/units follow the GLB; your PCA/length outputs assume consistent behavior with your current `/process_scan` JSON.
- Very large files: lower `max_points` (e.g. 25_000) to match your Flutter downsampling.

## 5. Quick test (Mac or Windows, venv active)

```bash
curl -s -X POST "http://127.0.0.1:8000/process_glb" \
  -F "file=@/path/to/model.glb" \
  -F 'meta={"job_name":"glb-test"}' \
  -F "max_points=20000" | head
```

(Use PowerShell-friendly multipart on Windows if `curl` differs.)

---

When `_YOUR_CORE_FUNCTION_` is wired and `/process_glb` returns the same shape as `/process_scan`, your iPhone can **Preview GLB** locally **and** **Send GLB to PC** for measurements in one workflow.
