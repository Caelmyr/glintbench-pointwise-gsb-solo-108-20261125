"""Refine a hand-filled mirror-sphere rim from photographic edge evidence.

The saved calibration is treated as a rough mark. Edge samples are collected
only around the coarse circumference (±15% of its radius), fitted circles are
scored by independently supported arc coverage plus rim-fit residual, and a
candidate is adoptable only when at least three quadrants carry real rim arcs.
A pedestal's straight top edge that merely passes near the circle is detected
as a straight run and never counted as rim. Competing close contours are then
replayed through the same saved reflection point and target tolerance: if they
disagree or one pushes the point outside the sphere, the rim is not replaced.
"""

import math

import numpy as np
from scipy.ndimage import gaussian_filter, gaussian_filter1d, map_coordinates

BAND = 0.15
BIN_DEGREES = 3
BINS = 360 // BIN_DEGREES
MIN_QUADRANT_BINS = 4
MIN_SUPPORT_BINS = 24
MIN_QUADRANTS = 3
SAMPLE_STEP = 320
TOLERANCE_MIN = 1.0
TOLERANCE_MAX = 3.0
MAX_CONTOURS = 18
SEED = 17
RIVAL_SCORE_GAP = 0.2
RIVAL_COVERAGE_RATIO = 0.6
RIVAL_CENTRE_GAP = 0.05
RIVAL_RADIUS_GAP = 0.08
FLAT_RUN_DEGREES = 12.0
FLAT_RUN_POINTS = 7
FLAT_LINE_ERROR = 0.3
FLAT_CIRCLE_ERROR = 0.4


def luma(image):
    return np.asarray(image, dtype=np.float32) @ np.array([0.2126, 0.7152, 0.0722], np.float32)


def tolerance(radius):
    return float(np.clip(radius * 0.009, TOLERANCE_MIN, TOLERANCE_MAX))


def rim_edges(gray, cx, cy, radius):
    """Collect sub-pixel luminance-gradient maxima along radial rays around the coarse rim."""
    height, width = gray.shape
    step = float(np.clip(radius / SAMPLE_STEP, 0.35, 1.0))
    radii = np.arange(radius * (1 - BAND), radius * (1 + BAND) + step * 0.5, step)
    count = int(np.clip(round(2 * math.pi * radius / 1.2), 240, 1440))
    angles = np.arange(count) * (2 * math.pi / count)
    cosines, sines = np.cos(angles), np.sin(angles)
    xs = cx + np.outer(cosines, radii)
    ys = cy - np.outer(sines, radii)
    inside = (xs >= 0) & (xs <= width - 1) & (ys >= 0) & (ys <= height - 1)
    valid = inside.all(axis=1)
    seen = angles[valid]
    if not seen.size:
        return np.empty((0, 6)), 0, count
    smooth = gaussian_filter(gray, 0.9)
    values = map_coordinates(smooth, [ys[valid].ravel(), xs[valid].ravel()],
                             order=1, mode="nearest").reshape(-1, len(radii))
    derivative = gaussian_filter1d(np.gradient(values, radii, axis=1), 1.1, axis=1)
    strength = np.abs(derivative)
    peak = np.zeros_like(strength, dtype=bool)
    peak[:, 1:-1] = (strength[:, 1:-1] >= strength[:, :-2]) & (strength[:, 1:-1] > strength[:, 2:])
    baseline = np.median(strength, axis=1, keepdims=True)
    spread = np.median(np.abs(strength - baseline), axis=1, keepdims=True)
    threshold = np.maximum(0.35, baseline + 3.5 * spread)
    points = []
    for row in range(strength.shape[0]):
        found = np.flatnonzero(peak[row] & (strength[row] >= threshold[row, 0]))
        if found.size > 3:
            found = found[np.argsort(-strength[row, found])[:3]]
        if not found.size:
            continue
        # Keep only peaks comparable to the strongest: suppresses faint halo doubling.
        found = found[strength[row, found] >= 0.45 * strength[row, found].max()]
        for column in found:
            offset = 0.0
            if 0 < column < len(radii) - 1:
                left, middle, right = strength[row, column - 1:column + 2]
                curvature = left - 2 * middle + right
                if abs(curvature) > 1e-9:
                    offset = float(np.clip(0.5 * (left - right) / curvature, -0.5, 0.5))
            distance = radii[column] + offset * step
            angle = seen[row]
            points.append((cx + distance * math.cos(angle), cy - distance * math.sin(angle),
                           distance, angle, strength[row, column], derivative[row, column]))
    return np.array(points, dtype=np.float64).reshape(-1, 6), int(valid.sum()), count


def circle_through(points):
    """Circumcircles of many edge-sample triples, vectorised."""
    first, second, third = points[:, 0], points[:, 1], points[:, 2]
    determinant = 2 * (first[:, 0] * (second[:, 1] - third[:, 1])
                       + second[:, 0] * (third[:, 1] - first[:, 1])
                       + third[:, 0] * (first[:, 1] - second[:, 1]))
    usable = np.abs(determinant) > 1e-9
    safe = np.where(usable, determinant, 1.0)
    aa = first[:, 0] ** 2 + first[:, 1] ** 2
    bb = second[:, 0] ** 2 + second[:, 1] ** 2
    cc = third[:, 0] ** 2 + third[:, 1] ** 2
    cx = np.where(usable, (aa * (second[:, 1] - third[:, 1]) + bb * (third[:, 1] - first[:, 1])
                           + cc * (first[:, 1] - second[:, 1])) / safe, np.nan)
    cy = np.where(usable, (aa * (third[:, 0] - second[:, 0]) + bb * (first[:, 0] - third[:, 0])
                           + cc * (second[:, 0] - first[:, 0])) / safe, np.nan)
    return cx, cy, np.hypot(first[:, 0] - cx, first[:, 1] - cy)


def fit_circle(points):
    x, y = points[:, 0], points[:, 1]
    solution, *_ = np.linalg.lstsq(np.c_[2 * x, 2 * y, np.ones(len(x))], x * x + y * y, rcond=None)
    cx, cy = float(solution[0]), float(solution[1])
    return cx, cy, math.sqrt(max(solution[2] + cx * cx + cy * cy, 0.0))


def contour_seeds(points, cx, cy, radius, tol, trials=8000):
    """Sample plausible rim circles from the edge cloud, keeping distinct tight-fit ones."""
    random = np.random.default_rng(SEED)
    low, high = radius * (1 - BAND), radius * (1 + BAND)
    limit = BAND * radius
    triples = random.integers(0, len(points), (trials, 3))
    triples = triples[(triples[:, 0] != triples[:, 1]) & (triples[:, 1] != triples[:, 2])
                      & (triples[:, 0] != triples[:, 2])]
    centres_x, centres_y, radii = circle_through(points[triples])
    keep = (np.isfinite(centres_x) & (radii >= low) & (radii <= high)
            & (np.abs(centres_x - cx) <= limit) & (np.abs(centres_y - cy) <= limit))
    circles = np.stack([centres_x[keep], centres_y[keep], radii[keep]], axis=1)
    if not circles.size:
        return []
    support = np.empty(len(circles), dtype=int)
    for start in range(0, len(circles), 800):
        block = circles[start:start + 800]
        distance = np.abs(np.hypot(points[None, :, 0] - block[:, None, 0],
                                   points[None, :, 1] - block[:, None, 1]) - block[:, None, 2])
        support[start:start + 800] = (distance < tol).sum(axis=1)
    seeds = []
    for index in np.argsort(-support):
        if support[index] < 12:
            break
        candidate = circles[index, :3]
        if any(np.hypot(*(candidate[:2] - chosen[:2])) < 0.02 * radius
               and abs(candidate[2] - chosen[2]) < 0.02 * radius for chosen in seeds):
            continue
        seeds.append(candidate)
        if len(seeds) >= MAX_CONTOURS:
            break
    return seeds


def refine_contour(points, seed, cx, cy, radius, tol):
    lowest, highest = radius * (1 - BAND), radius * (1 + BAND)
    limit = BAND * radius
    current = list(map(float, seed))
    for _ in range(6):
        distance = np.abs(np.hypot(points[:, 0] - current[0], points[:, 1] - current[1]) - current[2])
        inliers = points[distance < tol]
        if len(inliers) < 8:
            break
        fitted = fit_circle(inliers)
        if not lowest <= fitted[2] <= highest:
            break
        if abs(fitted[0] - cx) > limit or abs(fitted[1] - cy) > limit:
            break
        moved = sum(abs(a - b) for a, b in zip(fitted, current))
        current = list(fitted)
        if moved < 1e-3:
            break
    return current


def straight_bins(points, cx, cy, radius, tol):
    """Angular bins covered by a straight inlier run: a pedestal edge, not sphere rim.

    A base top that merely touches the calibration circle can supply dozens of
    samples lying almost exactly on the round, so each short angular window is
    tested separately: a window whose samples are far straighter than the round
    (and whose distance still matches) belongs to the straight edge, not the rim.
    """
    damaged = np.zeros(BINS, dtype=bool)
    distance = np.abs(np.hypot(points[:, 0] - cx, points[:, 1] - cy) - radius)
    chosen = np.flatnonzero(distance < tol)
    if not chosen.size:
        return damaged, np.zeros(BINS, dtype=int)
    rings = np.degrees(points[chosen, 3]) % 360
    order = np.argsort(rings)
    chosen, rings = chosen[order], rings[order]
    if len(rings) > 1:
        gaps = np.r_[np.diff(rings), rings[0] + 360 - rings[-1]]
        cut = int(np.argmax(gaps))
        if gaps[cut] > 2.5:
            rings = np.r_[rings[cut + 1:], rings[:cut + 1] + 360]
            chosen = np.r_[chosen[cut + 1:], chosen[:cut + 1]]
    counts = np.bincount(np.floor((rings % 360) / BIN_DEGREES).astype(int) % BINS, minlength=BINS)
    for start in range(len(chosen)):
        end = start
        while end + 1 < len(chosen) and rings[end + 1] - rings[start] <= FLAT_RUN_DEGREES:
            end += 1
        if end - start + 1 < FLAT_RUN_POINTS:
            continue
        local = points[chosen[start:end + 1], :2]
        _, singular, _ = np.linalg.svd(local - local.mean(axis=0), full_matrices=False)
        line_error = float(singular[1] / math.sqrt(len(local)))
        round_error = float(np.sqrt(np.mean((np.hypot(local[:, 0] - cx, local[:, 1] - cy) - radius) ** 2)))
        if line_error <= FLAT_LINE_ERROR and round_error >= FLAT_CIRCLE_ERROR:
            bins = np.floor((points[chosen[start:end + 1], 3] * 180 / math.pi % 360) / BIN_DEGREES)
            damaged[bins.astype(int) % BINS] = True
    return damaged, counts


def evaluate(points, cx, cy, radius, tol):
    damaged, counts = straight_bins(points, cx, cy, radius, tol)
    support = (counts >= 2) & ~damaged
    quadrant = BINS // 4
    quadrants = [int(support[index * quadrant:(index + 1) * quadrant].sum()) for index in range(4)]
    distance = np.abs(np.hypot(points[:, 0] - cx, points[:, 1] - cy) - radius)
    inliers = distance < tol
    residual = (float(np.sqrt(np.mean((np.hypot(points[inliers, 0] - cx,
                                               points[inliers, 1] - cy) - radius) ** 2)))
                if inliers.any() else tol)
    coverage = float(support.sum() / BINS)
    # Confirmed circular coverage carries more weight than the rim-fit residual.
    score = 0.65 * coverage + 0.35 * max(0.0, 1.0 - min(1.0, residual / (tol * 0.9)))
    return {
        "cx": float(cx), "cy": float(cy), "radius": float(radius),
        "support_bins": int(support.sum()), "quadrants": quadrants,
        "supported_quadrants": sum(1 for value in quadrants if value >= MIN_QUADRANT_BINS),
        "coverage": coverage, "residual": residual, "inliers": int(inliers.sum()),
        "straight_bins": int(damaged.sum()), "score": score,
    }


def detect(image, cx, cy, radius):
    """Find adoptable rim circles inside the ±15% band around the coarse calibration."""
    gray = luma(image)
    points, usable, total = rim_edges(gray, cx, cy, radius)
    evidence = {"rays": int(total), "usable_rays": usable, "edge_points": int(len(points)),
                "band": BAND, "tolerance": tolerance(radius)}
    if len(points) < 16:
        return {"ready": False, "reason": "粗圆附近找不到足够的球缘边缘。", "evidence": evidence, "contours": []}
    tol = evidence["tolerance"]
    seeds = contour_seeds(points, cx, cy, radius, tol)
    order = np.lexsort((points[:, 3], -points[:, 4]))
    ranked = points[order]
    contours = []
    for seed in seeds:
        fitted = refine_contour(ranked, seed, cx, cy, radius, tol)
        if any(np.hypot(fitted[0] - entry["cx"], fitted[1] - entry["cy"]) < 0.015 * radius
               and abs(fitted[2] - entry["radius"]) < 0.015 * radius for entry in contours):
            continue
        contours.append(evaluate(ranked, *fitted, tol))
    contours.sort(key=lambda entry: -entry["score"])
    evidence["contours"] = len(contours)
    if not contours:
        return {"ready": False, "reason": "粗圆一带没有拟合出稳定的球缘圆弧。", "evidence": evidence, "contours": []}
    return {"ready": True, "evidence": evidence, "contours": contours, "tol": tol}


def adoptable(contours):
    """The best contour if it has rim support in at least three quadrants."""
    if not contours:
        return False
    best = contours[0]
    return best["supported_quadrants"] >= MIN_QUADRANTS and best["support_bins"] >= MIN_SUPPORT_BINS


def close_contours(contours):
    """Rival contours that could reasonably be the rim too.

    A round contests the adopted one when it keeps real multi-quadrant support and
    a rim-fit error of the same order; it needs at least 60% of the adopted round's
    confirmed coverage, so a short inner arc does not block an obviously better rim.
    Neighbouring radii of one thick or blurred edge collapse into a single contour;
    only a clearly distinct radius is treated as a separate contour.
    """
    if not contours:
        return []
    best = contours[0]
    limit = max(1.5 * best["residual"], 0.5 * tolerance(best["radius"]))
    rivals = []
    for entry in contours[1:]:
        if (entry["supported_quadrants"] < MIN_QUADRANTS
                or np.hypot(entry["cx"] - best["cx"], entry["cy"] - best["cy"]) > RIVAL_CENTRE_GAP * best["radius"]
                or abs(entry["radius"] - best["radius"]) <= RIVAL_RADIUS_GAP * best["radius"]
                or entry["support_bins"] < RIVAL_COVERAGE_RATIO * best["support_bins"]
                or entry["residual"] > limit
                or entry["score"] < best["score"] - RIVAL_SCORE_GAP):
            continue
        rivals.append(entry)
    return rivals


def distinction(contours, x, y, judge):
    """Decide whether rival close contours still leave the saved calibration trustworthy.

    ``judge(x, y, cx, cy, radius)`` replays the one saved reflection point and the
    existing target tolerance against a candidate round.
    """
    best = contours[0] if contours else None
    if best is None or not adoptable(contours):
        return {"status": "insufficient", "reason": "球缘弧还没有在至少三个象限得到实拍支持。手填标定保持不变。",
                "candidate": None, "rivals": []}
    verdicts = [dict(entry, aim=judge(x, y, entry["cx"], entry["cy"], entry["radius"]))
                for entry in [best, *close_contours(contours)]]
    if any(entry["aim"]["status"] == "outside" for entry in verdicts):
        return {"status": "insufficient", "reason": "相近轮廓会把已存反光点划到球外，目前轮廓不足以定准。手填标定保持不变。",
                "candidate": best, "rivals": verdicts}
    if len({entry["aim"]["status"] for entry in verdicts}) > 1:
        return {"status": "insufficient", "reason": "相近轮廓一项合格、一项超差，目前轮廓不足以定准。手填标定保持不变。",
                "candidate": best, "rivals": verdicts}
    return {"status": "adoptable", "reason": "", "candidate": verdicts[0], "rivals": verdicts}


class Judge:
    """Replay the one saved reflection point and target tolerance against a candidate round."""

    def __init__(self, aim):
        self.aim = aim

    def __call__(self, x, y, cx, cy, radius):
        return self.aim(x, y, cx, cy, radius)


def refine(image, config, judge):
    """Full rim refinement for one saved card; ``judge`` replays the stored point."""
    found = detect(image, config["cx"], config["cy"], config["radius"])
    outcome = distinction(found["contours"], config["seed_x"], config["seed_y"], judge)
    outcome["evidence"] = found["evidence"]
    outcome["reason"] = outcome["reason"] or found.get("reason", "")
    outcome["coarse"] = {"cx": config["cx"], "cy": config["cy"], "radius": config["radius"]}
    outcome["preview"] = outcome["candidate"]
    outcome["adoptable"] = outcome["status"] == "adoptable" and outcome["candidate"] is not None
    if not outcome["adoptable"]:
        outcome["candidate"] = None
    return outcome
