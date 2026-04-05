# Pipe layout prototype — runbook (half page)

**Three roles:** **PC** = Windows + GPU backend. **Mac** = Flutter dev / optional `curl` tests. **iPhone** = Pipe Layout app + MetaRoom (scan) + same Wi‑Fi as PC.

---

## Before each job

1. **PC and iPhone on the same LAN** (same Wi‑Fi or Ethernet).
2. On **PC**, note **IPv4** (Command Prompt or PowerShell: `ipconfig` → Wireless/Ethernet adapter).
3. **PC firewall** allows inbound **TCP 8000** (Private network) or allows **Python / uvicorn** when prompted.
4. **Backend running** on PC (see below).

---

## PC — start the API

**Typical folder:** `C:\pipe-layout-backend` (contains `app.py`, `glb_to_points.py`, `.venv`).

**PowerShell:**

```powershell
cd C:\pipe-layout-backend
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned   # once, if Activate.ps1 is blocked
.\.venv\Scripts\Activate.ps1
python -m uvicorn app:app --host 0.0.0.0 --port 8000
```

**Or** from a repo copy of the Flutter project, if `scripts/run_backend.ps1` exists:

```powershell
powershell -ExecutionPolicy Bypass -File C:\path\to\pipe_layout_flutter\scripts\run_backend.ps1
```

**Sanity check (PC or Mac):** open `http://YOUR_PC_IPV4:8000/docs` or run `Invoke-RestMethod http://YOUR_PC_IPV4:8000/version` (PowerShell) / `curl http://.../health`.

**Replace** `YOUR_PC_IPV4` with the address from `ipconfig` (example shape: `192.168.4.70`).

---

## iPhone — Pipe Layout app

1. Install/run the app (USB debug or future TestFlight).
2. **Backend URL** field: `http://YOUR_PC_IPV4:8000`  
   - No trailing slash.  
   - Use **`http`** on LAN (your dev server). **`https`** only if you terminate TLS.
3. **Health check** — confirms phone reaches PC.
4. **MetaRoom** — export room as **`.glb`** (save where Files can see it; avoid fancy Unicode names if possible, e.g. `job_site.glb`).
5. **Preview GLB** — checks the model on-device (local).
6. **Send GLB to PC** — uploads GLB; PC samples mesh → **`/process_glb`** → pipe list + measurements in app.
7. **Load scan JSON / process** — optional path when you have a **point cloud JSON** from elsewhere, not only GLB.

---

## Quick troubleshooting

| Symptom | Check |
|--------|--------|
| Health fails from phone | Same Wi‑Fi, correct IP, backend running, firewall **8000**, PC not sleeping. |
| GLB preview works, send fails | Timeout → try smaller GLB or lower `max_points`; confirm `/process_glb` on PC (see `/docs` on PC). |
| `pipe_count` 0 or nonsense | Mesh vs point cloud differs — tune PC detection / export denser GLB; see backend `debug` in JSON. |
| Developer enrollment pending | TestFlight later; use **USB + Xcode** or signed dev build until Apple account is active. |

---

## Files reference (optional)

- **Backend:** `app.py`, `glb_to_points.py`, `requirements.txt`, `.venv`
- **Flutter:** `lib/main.dart`, `ios/Runner.xcworkspace`
- **Integration notes:** `metaroom_integration/README.md` (in this repo)

**Last updated:** fill when you change IP, paths, or bundle ID.
