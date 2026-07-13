"""
xview3_infer.py — real xView3 first-place inference for Tidewatch.

Wraps Eugene Khvedchenya's winning solution
(https://github.com/DIUx-xView/xView3_first_place), which ships as a traced
TorchScript ensemble (`traced_ensemble.jit`, a GitHub release asset). This
module turns a Sentinel-1 GRD scene (VV+VH) into vessel detections with
lat/lon and length, matching the repo's documented pipeline:

    2-channel SAR (VV,VH) -> custom sigmoid normalization -> 2048px tiles
    (step 1536, overlap) -> traced ensemble + flip-LR TTA -> accumulate
    objectness/vessel/fishing/length maps -> CenterNet-style NMS -> candidates
    -> global thresholds -> pixel->geo conversion.

RUNTIME REQUIREMENTS (run on a GPU box, not the workspace sandbox):
    pip install torch rasterio numpy
    TIDEWATCH_XVIEW3_CKPT=/path/to/traced_ensemble.jit
    (download once from the repo's v1.0 release)

The functions are split so the GPU-free parts (normalization, tiling,
pixel->geo, NMS) are unit-testable without weights; only `load_model` and
the forward pass need torch + the checkpoint + (ideally) a GPU.
"""

from __future__ import annotations

import os
from typing import Optional

import numpy as np

# Repo-documented inference constants (README + configs/inference).
TILE = 2048
STEP = 1536              # overlap to reduce tile-boundary artifacts
# Global thresholds are tuned hyper-params in the winning config; expose them
# as env so the sprint can match the repo's YAML without code edits.
OBJ_THRESH = float(os.environ.get("XVIEW3_OBJ_THRESH", "0.5"))
VESSEL_THRESH = float(os.environ.get("XVIEW3_VESSEL_THRESH", "0.5"))
FISHING_THRESH = float(os.environ.get("XVIEW3_FISHING_THRESH", "0.5"))
# SAR sigmoid normalization (repo: sea saturates low ~0.2, ships mid/high->1).
# out = sigmoid(NORM_SLOPE * (DN - NORM_MIDPOINT)). Defaults calibrated so a
# typical S1 GRD amplitude DN maps sea~0.2, vessels mid-to-high. Tune per the
# repo's normalization YAML on the GPU box if detections look off.
NORM_MIDPOINT = float(os.environ.get("XVIEW3_NORM_MIDPOINT", "1500.0"))
NORM_SLOPE = float(os.environ.get("XVIEW3_NORM_SLOPE", "0.001"))
SAR_RES_M = 10.0        # Sentinel-1 GRD ~10 m/pixel -> length_px * 10 = metres


def sar_normalize(arr: np.ndarray) -> np.ndarray:
    """uint16 SAR -> [0,1] via sigmoid, per the winning solution's scheme.
    Missing data (<=0) filled with zeros. GPU-free, unit-testable."""
    a = arr.astype("float32")
    z = NORM_SLOPE * (a - NORM_MIDPOINT)
    out = 1.0 / (1.0 + np.exp(-z))
    out[a <= 0] = 0.0  # missing / no-data filled with zeros (repo behavior)
    return out


def iter_tiles(h: int, w: int, tile: int = TILE, step: int = STEP):
    """Yield (row0, col0, row1, col1) overlapping windows covering the scene."""
    rows = list(range(0, max(h - tile, 0) + 1, step)) or [0]
    cols = list(range(0, max(w - tile, 0) + 1, step)) or [0]
    if rows[-1] != max(h - tile, 0):
        rows.append(max(h - tile, 0))
    if cols[-1] != max(w - tile, 0):
        cols.append(max(w - tile, 0))
    for r in rows:
        for c in cols:
            yield r, c, min(r + tile, h), min(c + tile, w)


def nms_peaks(objectness: np.ndarray, radius: int = 3, thresh: float = OBJ_THRESH):
    """CenterNet-style peak NMS on the objectness map: keep local maxima above
    thresh. Pure-numpy so it runs and tests without a GPU.
    Returns list of (row, col, score)."""
    from scipy.ndimage import maximum_filter  # lazy import
    mx = maximum_filter(objectness, size=2 * radius + 1, mode="constant")
    peaks = (objectness == mx) & (objectness >= thresh)
    ys, xs = np.nonzero(peaks)
    return [(int(y), int(x), float(objectness[y, x])) for y, x in zip(ys, xs)]


def pixel_to_lonlat(row: float, col: float, transform) -> tuple[float, float]:
    """Affine (rasterio) transform: pixel (col,row) -> (lon,lat)."""
    lon, lat = transform * (col + 0.5, row + 0.5)
    return lon, lat


def load_model(ckpt_path: str):
    """Load the traced TorchScript ensemble. Needs torch + (ideally) a GPU."""
    import torch  # lazy import so GPU-free parts stay importable
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = torch.jit.load(ckpt_path, map_location=device)
    model.eval()
    return model, device


def infer_scene(vv_path: str, vh_path: str, bbox=None, ckpt_path: Optional[str] = None):
    """
    Full inference over a Sentinel-1 GRD scene. Returns list of dicts:
        {lat, lon, confidence, length_m, is_vessel, is_fishing}
    Heavy path: torch + rasterio + checkpoint + GPU.
    """
    ckpt_path = ckpt_path or os.environ.get("TIDEWATCH_XVIEW3_CKPT")
    if not ckpt_path or not os.path.exists(ckpt_path):
        raise RuntimeError(
            f"traced_ensemble.jit not found at {ckpt_path!r}. Download the "
            f"xView3 first-place release asset and set TIDEWATCH_XVIEW3_CKPT."
        )
    import torch
    import rasterio
    from rasterio.windows import Window

    model, device = load_model(ckpt_path)

    with rasterio.open(vv_path) as vv_ds, rasterio.open(vh_path) as vh_ds:
        H, W = vv_ds.height, vv_ds.width
        transform = vv_ds.transform
        obj_acc = np.zeros((H, W), dtype="float32")
        vsl_acc = np.zeros((H, W), dtype="float32")
        fsh_acc = np.zeros((H, W), dtype="float32")
        len_acc = np.zeros((H, W), dtype="float32")
        cnt_acc = np.zeros((H, W), dtype="float32")

        for r0, c0, r1, c1 in iter_tiles(H, W):
            win = Window(c0, r0, c1 - c0, r1 - r0)
            vv = sar_normalize(vv_ds.read(1, window=win))
            vh = sar_normalize(vh_ds.read(1, window=win))
            x = np.stack([vv, vh], axis=0)[None]  # (1,2,h,w)
            xt = torch.from_numpy(x).to(device)
            with torch.no_grad():
                # flip-LR TTA (as in the winning config)
                out = model(xt)
                out_f = model(torch.flip(xt, dims=[3]))
            def to_np(o, flip=False):
                a = o.detach().cpu().numpy()[0]
                return a[:, :, ::-1] if flip else a
            o0, o1 = to_np(out), to_np(out_f, flip=True)
            heads = (o0 + o1) / 2.0  # (C,h,w): obj,vessel,fishing,length
            hh, ww = r1 - r0, c1 - c0
            obj_acc[r0:r1, c0:c1] += heads[0, :hh, :ww]
            vsl_acc[r0:r1, c0:c1] += heads[1, :hh, :ww]
            fsh_acc[r0:r1, c0:c1] += heads[2, :hh, :ww]
            len_acc[r0:r1, c0:c1] += heads[3, :hh, :ww]
            cnt_acc[r0:r1, c0:c1] += 1.0

        cnt_acc[cnt_acc == 0] = 1.0
        obj = obj_acc / cnt_acc
        vsl = vsl_acc / cnt_acc
        fsh = fsh_acc / cnt_acc
        length_px = len_acc / cnt_acc

        dets = []
        for row, col, score in nms_peaks(obj, radius=3, thresh=OBJ_THRESH):
            if vsl[row, col] < VESSEL_THRESH:
                continue
            lon, lat = pixel_to_lonlat(row, col, transform)
            if bbox and not (bbox[0] <= lon <= bbox[2] and bbox[1] <= lat <= bbox[3]):
                continue
            dets.append({
                "lat": lat, "lon": lon,
                "confidence": round(score, 4),
                "length_m": round(float(length_px[row, col]) * SAR_RES_M, 1),
                "is_vessel": True,
                "is_fishing": bool(fsh[row, col] >= FISHING_THRESH),
            })
        return dets
