"""Face-tracking reframe for fit=crop (`"focus": "track"`).

Samples the clip, follows the main face (the one nearest the previous position), smooths the
path like a camera operator would (median filter, dead zone, eased follow) and returns ffmpeg
crop expressions in pixels as functions of output time t.
"""
from __future__ import annotations

import numpy as np

from common import log

VERSION = 5


def _faces(frame_bgr):
    from intro import _largest_face  # noqa: F401  (ensures cascades load the same way)
    import cv2
    gray = cv2.equalizeHist(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY))
    found = []
    for name in ("haarcascade_frontalface_default.xml", "haarcascade_profileface.xml"):
        casc = cv2.CascadeClassifier(cv2.data.haarcascades + name)
        for flip in (False, True) if "profile" in name else (False,):
            g = cv2.flip(gray, 1) if flip else gray
            for (x, y, w, h) in casc.detectMultiScale(g, 1.1, 5, minSize=(gray.shape[0] // 9,) * 2):
                x = gray.shape[1] - x - w if flip else x
                found.append(((x + w / 2) / gray.shape[1], (y + h / 2) / gray.shape[0], w * h))
    return found


_HOG = []


def _people(frame_bgr):
    """Full-body pedestrian boxes (OpenCV HOG) as (cx, head_y, area): used when faces are
    too small to detect (wide shots)."""
    import cv2
    if not _HOG:
        h = cv2.HOGDescriptor()
        h.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
        _HOG.append(h)
    rects, weights = _HOG[0].detectMultiScale(frame_bgr, winStride=(8, 8), padding=(8, 8), scale=1.05)
    H_, W_ = frame_bgr.shape[:2]
    out = []
    for (x, y, w, h), wt in zip(rects, np.ravel(weights) if len(rects) else []):
        if wt < 0.5:
            continue
        out.append(((x + w / 2) / W_, (y + 0.15 * h) / H_, w * h * 0.05))  # head area, low weight
    return out


def _median(a, k=5):
    out = a.copy()
    r = k // 2
    for i in range(len(a)):
        out[i] = np.median(a[max(0, i - r): i + r + 1])
    return out


def _follow(target, dt, dead, ease=3.0):
    """Lazy camera: stays put inside the dead zone, otherwise eases toward the target."""
    cam = np.empty_like(target)
    cam[0] = target[0]
    a = 1 - np.exp(-ease * dt)
    for i in range(1, len(target)):
        diff = target[i] - cam[i - 1]
        goal = cam[i - 1] if abs(diff) <= dead else target[i] - np.sign(diff) * dead
        cam[i] = cam[i - 1] + (goal - cam[i - 1]) * a
    return cam


def _simplify(ts, xs, tol):
    """Ramer-Douglas-Peucker on (t, x) so the expression stays short."""
    keep = np.zeros(len(ts), bool)
    keep[0] = keep[-1] = True

    def rec(i, j):
        if j <= i + 1:
            return
        t0, t1, x0, x1 = ts[i], ts[j], xs[i], xs[j]
        line = x0 + (x1 - x0) * (ts[i + 1:j] - t0) / max(t1 - t0, 1e-6)
        err = np.abs(xs[i + 1:j] - line)
        k = int(np.argmax(err))
        if err[k] > tol:
            keep[i + 1 + k] = True
            rec(i, i + 1 + k)
            rec(i + 1 + k, j)
    rec(0, len(ts) - 1)
    return ts[keep], xs[keep]


def _expr(ts, xs):
    """Piecewise-linear ffmpeg expression of t through the key points."""
    if len(ts) == 1:
        return f"{xs[0]:.1f}"
    e = f"{xs[-1]:.1f}"
    for i in range(len(ts) - 2, -1, -1):
        t0, t1, x0, x1 = ts[i], ts[i + 1], xs[i], xs[i + 1]
        seg = f"{x0:.1f}+({x1 - x0:.1f})*(t-{t0:.3f})/{max(t1 - t0, 1e-3):.3f}"
        e = f"if(lt(t\\,{t1:.3f})\\,{seg}\\,{e})"
    return e


def track_crop(path, a, b, speed, W, H, sw, sh, rate=6.0, hint=None):
    """Return (x_expr, y_expr, info) for crop=W:H after scaling the source to cover W x H,
    or None when too few faces are found (caller falls back to a static crop)."""
    import cv2
    k = max(W / sw, H / sh)
    iw, ih = sw * k, sh * k
    if iw - W < 2 and ih - H < 2:
        return None
    cap = cv2.VideoCapture(path)
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 24
    cap.set(cv2.CAP_PROP_POS_MSEC, a * 1000)
    step = max(1, int(round(src_fps / rate)))
    ts, obs = [], []
    prev, misses = None, 0
    n = 0
    t = a
    while t < b:
        ok, fr = cap.read()
        if not ok:
            break
        if n % step == 0:
            small = cv2.resize(fr, (640, int(fr.shape[0] * 640 / fr.shape[1])))
            faces = _faces(small) or _people(small)
            pick = None
            if faces:
                if prev is None and hint is not None:  # who to follow: nearest to the hint
                    pick = min(faces, key=lambda f: abs(f[0] - hint))
                elif prev is None:
                    pick = max(faces, key=lambda f: f[2])
                else:  # stay on the same person; a big jump between samples is someone else
                    pick = min(faces, key=lambda f: (f[0] - prev[0]) ** 2 + (f[1] - prev[1]) ** 2 - 1e-6 * f[2])
                    if abs(pick[0] - prev[0]) > 0.12:
                        pick = None
            if pick is not None:
                prev, misses = pick, 0
            else:
                misses += 1
                if prev is not None and misses > 2 * rate:  # lost for 2 s: allow re-acquiring
                    prev = None
            ts.append((t - a) / speed)
            obs.append(pick)
        n += 1
        t = a + n / src_fps
    cap.release()
    hits = sum(1 for o in obs if o is not None)
    if len(obs) < 2 or hits < max(2, 0.3 * len(obs)):  # too few: a wrong path is worse than none
        return None
    ts = np.array(ts)
    cx = np.array([o[0] if o else np.nan for o in obs], float)
    cy = np.array([o[1] if o else np.nan for o in obs], float)
    idx = np.arange(len(cx))
    for arr in (cx, cy):  # fill gaps (hold at the ends)
        good = ~np.isnan(arr)
        arr[~good] = np.interp(idx[~good], idx[good], arr[good])
    cx, cy = _median(cx), _median(cy)
    dt = (ts[1] - ts[0]) if len(ts) > 1 else 1 / rate
    # desired crop offsets in pixels (face slightly above centre vertically)
    tx = np.clip(cx * iw - W / 2, 0, iw - W)
    ty = np.clip(cy * ih - H / 2 + 0.12 * H, 0, max(ih - H, 0))
    px = _follow(tx, dt, dead=0.06 * W)
    py = _follow(ty, dt, dead=0.06 * H)
    kx, vx = _simplify(ts, px, tol=3.0)
    ky, vy = _simplify(ts, py, tol=3.0)
    info = f"{hits}/{len(obs)} samples with a face, {len(kx)} x-keys, pan {px.max() - px.min():.0f}px"
    return _expr(kx, vx), _expr(ky, vy), info
