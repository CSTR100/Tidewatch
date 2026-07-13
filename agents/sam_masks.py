"""
sam_masks.py — per-detection SAM mask extraction for Tidewatch (Task 5, stretch).

For each detection, crop a window around the contact from the SAR scene, run
Segment Anything (SAM) prompted at the detection point, save the binary mask,
and return a mask_ref (path) plus a refined length in metres from the mask's
major axis. Purely additive: if SAM isn't available it returns None and the
detector keeps the xView3 head's length — nothing breaks.

RUNTIME (GPU box):
    pip install segment-anything torch rasterio numpy
    download a SAM checkpoint (e.g. sam_vit_h) -> TIDEWATCH_SAM_CKPT
The GPU-free parts (crop math, mask->length via PCA, mask persistence) are
unit-testable without the checkpoint.
"""

from __future__ import annotations

import os
from typing import Optional

import numpy as np

DATA_DIR = os.environ.get("TIDEWATCH_DATA_DIR", "data")
CROP_PX = int(os.environ.get("TIDEWATCH_SAM_CROP_PX", "256"))
SAR_RES_M = 10.0


def _mask_dir() -> str:
    d = os.path.join(DATA_DIR, "masks")
    os.makedirs(d, exist_ok=True)
    return d


def crop_window(h: int, w: int, row: int, col: int, size: int = CROP_PX):
    """Clamped (r0,c0,r1,c1) crop centered on (row,col). GPU-free."""
    half = size // 2
    r0, c0 = max(row - half, 0), max(col - half, 0)
    r1, c1 = min(row + half, h), min(col + half, w)
    return r0, c0, r1, c1


def mask_length_m(mask: np.ndarray, res_m: float = SAR_RES_M) -> Optional[float]:
    """Major-axis length (m) of a binary mask via PCA on its pixel coords.
    GPU-free, unit-testable."""
    ys, xs = np.nonzero(mask)
    if len(xs) < 2:
        return None
    pts = np.stack([xs, ys], axis=1).astype("float64")
    pts -= pts.mean(axis=0)
    cov = np.cov(pts.T)
    evals, evecs = np.linalg.eigh(cov)
    axis = evecs[:, np.argmax(evals)]
    proj = pts @ axis
    return float((proj.max() - proj.min()) * res_m)


def save_mask(mask: np.ndarray, vessel_id: str) -> str:
    """Persist a binary mask as .npy and return its path (mask_ref)."""
    path = os.path.join(_mask_dir(), f"{vessel_id}.npy")
    np.save(path, mask.astype("uint8"))
    return path


def _load_sam():
    """Load SAM predictor. Needs torch + segment-anything + checkpoint + GPU."""
    ckpt = os.environ.get("TIDEWATCH_SAM_CKPT")
    if not ckpt or not os.path.exists(ckpt):
        raise RuntimeError(
            f"SAM checkpoint not found at {ckpt!r}. Set TIDEWATCH_SAM_CKPT "
            f"to a Segment-Anything checkpoint to enable mask extraction."
        )
    import torch
    from segment_anything import sam_model_registry, SamPredictor
    model_type = os.environ.get("TIDEWATCH_SAM_TYPE", "vit_h")
    sam = sam_model_registry[model_type](checkpoint=ckpt)
    sam.to("cuda" if torch.cuda.is_available() else "cpu")
    return SamPredictor(sam)


def extract_mask(scene_crop: np.ndarray, point_rc, predictor=None):
    """Run SAM at the given (row,col) point on a 2D crop; return a binary mask.
    Heavy path (torch + checkpoint)."""
    predictor = predictor or _load_sam()
    # SAM expects HxWx3 uint8; SAR crop is single-channel -> stack to RGB
    img = np.stack([scene_crop] * 3, axis=-1)
    if img.dtype != np.uint8:
        img = (255 * (img - img.min()) / (np.ptp(img) + 1e-9)).astype("uint8")
    predictor.set_image(img)
    r, c = point_rc
    masks, scores, _ = predictor.predict(
        point_coords=np.array([[c, r]]),  # SAM uses (x,y)
        point_labels=np.array([1]),
        multimask_output=True,
    )
    return masks[int(np.argmax(scores))].astype("uint8")


def add_masks_to_detections(scene2d: np.ndarray, detections: list[dict],
                            row_col_of, vessel_id_of) -> list[dict]:
    """For each detection, extract + save a mask and set mask_ref + refined
    length_m. Additive: on any failure the detection is left unchanged."""
    try:
        predictor = _load_sam()
    except Exception:
        return detections  # SAM unavailable -> keep xView3 lengths, no masks
    H, W = scene2d.shape
    for det in detections:
        try:
            row, col = row_col_of(det)
            r0, c0, r1, c1 = crop_window(H, W, row, col)
            crop = scene2d[r0:r1, c0:c1]
            mask = extract_mask(crop, (row - r0, col - c0), predictor)
            det["mask_ref"] = save_mask(mask, vessel_id_of(det))
            L = mask_length_m(mask)
            if L:
                det["length_m"] = round(L, 1)
        except Exception:
            continue
    return detections
