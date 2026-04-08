# Mac backend + native Swift app (migration path)

Use this when the **Python API runs on the Mac** and you move the **iPhone UI from Flutter to Swift/SwiftUI** over time.

---

## Part 1 — Backend on Mac (hours, not weeks)

### 1. Copy backend from Windows

Copy the folder that contains **`app.py`**, **`glb_to_points.py`**, and your venv **or** recreate venv on Mac:

```bash
mkdir -p ~/pipe-layout-backend
# Copy from PC (AirDrop, USB, git): app.py, glb_to_points.py, requirements.txt if you have one
cd ~/pipe-layout-backend
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt   # or install fastapi uvicorn numpy open3d pydantic torch python-multipart
```

### 2. PyTorch on Apple Silicon

- There is **no CUDA** on Mac. PyTorch uses **CPU** or **MPS** (Apple GPU). Your pipe `DEVICE` may show `cpu` or `mps`; large clouds may be **slower** than an RTX 3090 — often still fine for development.

### 3. Start the server

From this repo (optional helper):

```bash
chmod +x /path/to/repo/scripts/run_backend_mac.sh
export PIPE_BACKEND_DIR="$HOME/pipe-layout-backend"
./scripts/run_backend_mac.sh
```

Or manually:

```bash
cd ~/pipe-layout-backend && source .venv/bin/activate
python -m uvicorn app:app --host 0.0.0.0 --port 8001
```

### 4. Mac firewall + IP for iPhone

- **System Settings → Network → Wi‑Fi → Details → TCP/IP** — note IPv4 (e.g. `192.168.1.x`).
- Allow incoming connections for **Python** / **Terminal** if prompted.
- On iPhone: base URL **`http://THAT_IP:8001`** (same Wi‑Fi).

Sanity check from Mac:

```bash
curl -s http://127.0.0.1:8001/health
```

---

## Part 2 — New Swift iOS app (phased; not a single “hours” rewrite)

### Phase 0 — New Xcode project (same day)

1. Xcode → **App** → **SwiftUI**, Interface `SwiftUI`, Language `Swift`.
2. **Deployment:** iOS 16+ (or match your Flutter target).
3. Add **Privacy** strings: **Camera**, **Motion**, **Local Network** (see your Flutter `Info.plist`).
4. **App Transport Security**: allow **HTTP** to LAN during dev (`NSAllowsArbitraryLoads` or domain exception) — same as Flutter.

### Phase 1 — Health + JSON POST (1–2 days)

- **`URLSession`** `GET /health`, `POST /process_scan` and `/process_room` with `JSONEncoder` body `{"points":[[Double]], "meta":{}}`.
- Simple **TextField** for base URL + **“Ping server”** button.

### Phase 2 — LiDAR mesh → points (larger lift)

- **ARKit** `ARWorldTrackingConfiguration` with **`sceneReconstruction = .mesh`** (LiDAR devices).
- Collect **`ARMeshAnchor`** vertices in **world space**, downsample, build `[[Double]]` — mirror your existing Flutter channel behavior.
- Optionally wrap in **`ARView`** (RealityKit) for preview later; **data path** is still mesh → points.

### Phase 3 — Parity with Flutter

- GLB preview (Quick Look / SceneKit / third-party).
- History, exports, polish — **weeks** if you match feature-for-feature.

**Pragmatic approach:** keep **Flutter** until Swift **Phase 1–2** proves full scan end-to-end; then cut over or maintain both briefly.

---

## Files to reuse

- **`glb_to_points.py`**, **`app.py`** — unchanged on Mac (same API contract).
- Flutter **`ARMeshScanViewController.swift`** — **reference** for mesh extraction when porting to pure Swift project.

---

## Commit / sync

If you use **git** for the backend, push from Windows and **pull** on Mac so both machines stay aligned.
