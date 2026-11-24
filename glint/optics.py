"""Measure apparent source directions from a calibrated spherical mirror photo."""

from io import BytesIO
import math

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

try:  # Flood labelling is an optimization; a small BFS fallback lives below.
    from scipy import ndimage
except ImportError:  # pragma: no cover - exercised only without scipy
    ndimage = None

from image_lab.validation import Invalid, integer, number


LUMA = np.array([0.2126, 0.7152, 0.0722])
MAX_IMAGE_BYTES = 18 * 1024 * 1024

# Angular slack (degrees) so a pixel sitting exactly on the tolerance circle is
# not failed by floating point noise.
ANGLE_EPS = 1e-7

CHECK_MODES = ("point", "patch")
MODE_SETTING_KEYS = ("check_mode", "verify_mode", "mode", "check", "method", "scope", "kind")
MODE_ALIASES = {
    "point": "point", "single": "point", "spot": "point", "one": "point",
    "single_point": "point", "single-point": "point", "singlepoint": "point",
    "pointwise": "point", "single-shot": "point", "单点": "point", "单点核对": "point",
    "单点测量": "point",
    "patch": "patch", "area": "patch", "whole": "patch", "strip": "patch",
    "region": "patch", "all": "patch", "entire": "patch", "whole_patch": "patch",
    "whole-patch": "patch", "wholepatch": "patch", "patch_check": "patch",
    "full_patch": "patch", "整片": "patch", "整片核对": "patch", "亮片": "patch",
    "整片核对方式": "patch", "整片测量": "patch",
}


def check_mode(value):
    """Normalize the adopted verification style; old cards default to one point."""
    if value is None:
        return "point"
    if isinstance(value, str):
        key = value.strip().lower()
        if key in MODE_ALIASES:
            return MODE_ALIASES[key]
        if value.strip() in MODE_ALIASES:
            return MODE_ALIASES[value.strip()]
    raise Invalid("核对方式取值不支持，请选择单点核对或整片核对")


def incoming_mode(value):
    """Pull a check mode out of a settings payload under any supported key."""
    if not isinstance(value, dict):
        return None
    for key in MODE_SETTING_KEYS:
        raw = value.get(key)
        if raw is not None:
            return check_mode(raw)
    return None


def png(image):
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def decode(data):
    if len(data) > MAX_IMAGE_BYTES:
        raise Invalid("单张原片不得超过18 MiB")
    try:
        with Image.open(BytesIO(data)) as photo:
            if photo.format not in {"PNG", "JPEG", "TIFF", "BMP", "WEBP"} or getattr(photo, "n_frames", 1) != 1:
                raise Invalid("请选择单帧 PNG、JPEG、TIFF、BMP 或 WebP")
            if min(photo.size) < 32 or max(photo.size) > 2600 or math.prod(photo.size) > 4_000_000:
                raise Invalid("原片边长须在32–2600 px内，总像素不超过400万")
            normal = ImageOps.exif_transpose(photo).convert("RGB")
            return normal.size, png(normal)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise Invalid("原片无法解码") from error


def pixels(path):
    with Image.open(path) as photo:
        return np.asarray(photo.convert("RGB"))


def settings(value, size):
    w, h = size
    mode = incoming_mode(value)
    if mode is None:
        mode = "point"
    return {
        "cx": number(value.get("cx"), "球心 X", 0, w - 1),
        "cy": number(value.get("cy"), "球心 Y", 0, h - 1),
        "radius": number(value.get("radius"), "球半径", 4, max(size)),
        "seed_x": integer(value.get("seed_x"), "反光点 X", 0, w - 1),
        "seed_y": integer(value.get("seed_y"), "反光点 Y", 0, h - 1),
        "target_yaw": number(value.get("target_yaw"), "目标方位", -180, 180),
        "target_pitch": number(value.get("target_pitch"), "目标俯仰", -90, 90),
        "tolerance": number(value.get("tolerance"), "目标容差", 1, 90),
        "floor": integer(value.get("floor"), "亮区读数下限", 1, 255),
        "check_mode": mode,
        # Alias kept alongside the canonical key for callers that name it "mode".
        "mode": mode,
    }


def defaults(size):
    w, h = size
    return settings({
        "cx": w // 2, "cy": h // 2, "radius": min(size) * 0.38,
        "seed_x": w // 2, "seed_y": h // 2, "target_yaw": 0,
        "target_pitch": 0, "tolerance": 12, "floor": 170,
    }, size)


def ray(x, y, config):
    """Return source unit ray (right, up, toward camera) using orthographic reflection."""
    nx = (x - config["cx"]) / config["radius"]
    ny = (config["cy"] - y) / config["radius"]
    square = nx * nx + ny * ny
    if square >= 1:
        return None
    nz = math.sqrt(1 - square)
    return (2 * nz * nx, 2 * nz * ny, 2 * nz * nz - 1)


def angles(direction):
    x, y, z = direction
    return {
        "yaw": math.degrees(math.atan2(x, z)),
        "pitch": math.degrees(math.asin(max(-1, min(1, y)))),
    }


def target_ray(config):
    yaw = math.radians(config["target_yaw"])
    pitch = math.radians(config["target_pitch"])
    return (math.sin(yaw) * math.cos(pitch), math.sin(pitch), math.cos(yaw) * math.cos(pitch))


def angular_gap(first, second):
    cosine = sum(a * b for a, b in zip(first, second))
    return math.degrees(math.acos(max(-1, min(1, cosine))))


def sample(image, x, y, config):
    rgb = image[y, x]
    level = float(rgb @ LUMA)
    direction = ray(x, y, config)
    return {
        "x": x, "y": y, "rgb": [int(channel) for channel in rgb], "level": level,
        "above_floor": bool(level >= config["floor"]), "direction": None if direction is None else angles(direction),
    }


def aiming(reading, config):
    """Judge the one saved manual reflection against the specified lighting target."""
    measured = ray(reading["x"], reading["y"], config)
    if measured is None:
        return {"status": "outside", "error_deg": None, "reselect": True,
                "reason": "outside", "mode": "point",
                "message": "反光点在标定圆外，请重新选点"}
    error = angular_gap(measured, target_ray(config))
    return {"status": "pass" if error <= config["tolerance"] + ANGLE_EPS else "miss",
            "error_deg": error, "reselect": False, "mode": "point"}


def sphere_grid(shape, config):
    """Pixel-grid normalized sphere coordinates and the strict inside mask."""
    height, width = shape[:2]
    yy, xx = np.indices((height, width), dtype=np.float64)
    nx = (xx - config["cx"]) / config["radius"]
    ny = (config["cy"] - yy) / config["radius"]
    square = nx * nx + ny * ny
    return nx, ny, square < 1.0


def patch_rays(xs, ys, config, nx=None, ny=None):
    """Vectorized orthographic reflection rays for arrays of pixel coordinates."""
    if nx is None or ny is None:
        nx = (xs.astype(np.float64) - config["cx"]) / config["radius"]
        ny = (config["cy"] - ys.astype(np.float64)) / config["radius"]
    else:
        nx = nx[ys, xs]
        ny = ny[ys, xs]
    square = nx * nx + ny * ny
    nz = np.sqrt(np.maximum(0.0, 1.0 - square))
    return np.stack((2 * nz * nx, 2 * nz * ny, 2 * nz * nz - 1), axis=1)


def recommend_center(rays, levels, floor):
    """Pick an actual patch pixel balancing bright core and angular coverage.

    The coverage anchor is the patch pixel with the smallest maximum angular
    distance to every patch pixel (spherical minimax).  The core anchor is the
    steeply brightness weighted ray centroid.  Their angular bisector is snapped
    to the nearest measured patch ray, guaranteeing the recommendation lies in
    the measured bright sheet.
    """
    count = len(rays)
    if count == 1:
        return 0

    excess = np.maximum(0.0, levels.astype(np.float64) - floor)
    peak = float(excess.max())
    weights = (excess / peak) ** 2 if peak > 1e-9 else np.ones(count)
    total = float(weights.sum())
    core_ray = (rays * weights[:, None]).sum(axis=0) / total
    core_ray /= max(np.linalg.norm(core_ray), 1e-12)

    # Evaluate the minimax radius for every pixel on ordinary patches; for very
    # large sheets evaluate a deterministic spatial/brightness shortlist with
    # one vector at a time so memory stays linear in the patch size.
    limit = 12000
    if count <= limit:
        candidates = np.arange(count)
        worst_dot = np.empty(count, dtype=np.float64)
        chunk = 384
        for start in range(0, count, chunk):
            block = rays[start:start + chunk]
            worst_dot[start:start + len(block)] = (block @ rays.T).min(axis=1)
    else:
        stride = max(1, count // 240)
        picked = set(range(0, count, stride))
        picked.update(np.argsort(levels)[-32:].tolist())
        picked.add(int(np.argmax(rays @ core_ray)))
        candidates = np.array(sorted(picked), dtype=np.int64)
        worst_dot = np.array([float((rays @ rays[index]).min()) for index in candidates])
    coverage_index = int(candidates[int(np.argmax(worst_dot))])
    coverage_ray = rays[coverage_index]

    balanced = coverage_ray + core_ray
    norm = np.linalg.norm(balanced)
    balanced = balanced / norm if norm > 1e-12 else coverage_ray
    return int(np.argmax(rays @ balanced))


def connected_sheet(eligible, seed_y, seed_x):
    """Boolean mask of the four-connected eligible component holding the seed."""
    if ndimage is not None:
        structure = np.array(((0, 1, 0), (1, 1, 1), (0, 1, 0)), dtype=bool)
        labels, _ = ndimage.label(eligible, structure=structure)
        component_id = int(labels[seed_y, seed_x])
        return labels == component_id

    # Pure-Python fallback (only used when scipy is unavailable).
    from collections import deque

    height, width = eligible.shape
    component = np.zeros_like(eligible)
    queue = deque([(seed_y, seed_x)])
    component[seed_y, seed_x] = True
    while queue:
        py, px = queue.popleft()
        for ny, nx in ((py - 1, px), (py + 1, px), (py, px - 1), (py, px + 1)):
            if 0 <= ny < height and 0 <= nx < width and eligible[ny, nx] and not component[ny, nx]:
                component[ny, nx] = True
                queue.append((ny, nx))
    return component


def bright_patch(image, config, luminance=None):
    """Four-connected floor-qualified sheet grown from the saved seed pixel.

    Only pixels strictly inside the calibration circle are masked in, so pixels
    outside the circle can neither be collected nor bridge two bright regions.
    """
    sx, sy = config["seed_x"], config["seed_y"]
    if luminance is None:
        luminance = image @ LUMA
    seed_level = float(luminance[sy, sx])
    seed_direction = ray(sx, sy, config)
    seed = {
        "x": sx, "y": sy, "level": seed_level,
        "above_floor": bool(seed_level >= config["floor"]),
        "direction": None if seed_direction is None else angles(seed_direction),
    }
    if seed_direction is None:
        return {"status": "outside", "seed": seed, "count": 0, "pixels": [],
                "over_pixels": [], "message": "反光点在标定圆外，请重新选点"}
    if seed_level < config["floor"]:
        return {"status": "below_floor", "seed": seed, "count": 0, "pixels": [],
                "over_pixels": [], "message": "点中像素低于亮区下限，请重新选点"}

    nx, ny, inside = sphere_grid(luminance.shape, config)
    eligible = inside & (luminance >= config["floor"])
    # The seed passed both guards, so its mask cell is labelled.
    component_mask = connected_sheet(eligible, sy, sx)
    ys, xs = np.nonzero(component_mask)
    levels = luminance[ys, xs]
    rays = patch_rays(xs, ys, config, nx, ny)
    yaw = np.degrees(np.arctan2(rays[:, 0], rays[:, 2]))
    pitch = np.degrees(np.arcsin(np.clip(rays[:, 1], -1.0, 1.0)))

    target = np.array(target_ray(config))
    target_gaps = np.degrees(np.arccos(np.clip(rays @ target, -1.0, 1.0)))
    tolerance = float(config["tolerance"])
    within = target_gaps <= tolerance + ANGLE_EPS
    over = ~within
    worst_index = int(target_gaps.argmax())

    center_index = recommend_center(rays, levels, config["floor"])
    center_ray = rays[center_index]
    center_radii = np.degrees(np.arccos(np.clip(rays @ center_ray, -1.0, 1.0)))
    far_index = int(center_radii.argmax())

    def point(index):
        return {"x": int(xs[index]), "y": int(ys[index]),
                "yaw": float(yaw[index]), "pitch": float(pitch[index])}

    center = point(center_index)
    center["level"] = float(levels[center_index])
    center["target_gap_deg"] = float(target_gaps[center_index])
    far_point = point(far_index)
    far_point["distance_deg"] = float(center_radii[far_index])
    worst = point(worst_index)
    worst["gap_deg"] = float(target_gaps[worst_index])

    over_y, over_x = ys[over], xs[over]
    pixels = np.stack((xs, ys), axis=1).tolist()
    over_pixels = np.stack((over_x, over_y), axis=1).tolist()
    radius_deg = float(center_radii[far_index])
    target_worst_deg = float(target_gaps[worst_index])
    return {
        "status": "ok", "seed": seed, "count": int(len(xs)), "pixels": pixels,
        "points": pixels,
        "over_pixels": over_pixels,
        "outside_pixels": over_pixels,
        "within_count": int(within.sum()), "outside_count": int(over.sum()),
        "over_count": int(over.sum()),
        "all_within": bool(within.all()),
        "center": center, "radius_deg": radius_deg,
        "max_radius_deg": radius_deg,
        "farthest_deg": radius_deg,
        "farthest_angle_deg": radius_deg,
        "spread_deg": radius_deg,
        "far_point": far_point, "target_worst_deg": target_worst_deg,
        "max_gap_deg": target_worst_deg,
        "worst_gap_deg": target_worst_deg,
        "worst": worst,
        "center_yaw": center["yaw"], "center_pitch": center["pitch"],
        "bounds": {"min_x": int(xs.min()), "max_x": int(xs.max()),
                   "min_y": int(ys.min()), "max_y": int(ys.max())},
    }


def patch_aim(patch, config):
    """Whole-sheet verdict: every measured direction must clear the target cone."""
    mode = "patch"
    if patch["status"] == "outside":
        return {"status": "outside", "error_deg": None, "reselect": True,
                "reason": "outside", "mode": mode, "message": patch["message"]}
    if patch["status"] == "below_floor":
        return {"status": "below_floor", "error_deg": None, "reselect": True,
                "reason": "below_floor", "mode": mode, "message": patch["message"]}
    tolerance = float(config["tolerance"])
    worst = float(patch["target_worst_deg"])
    center_gap = float(patch["center"]["target_gap_deg"])
    all_within = bool(patch["all_within"])
    return {
        "status": "pass" if all_within else "miss",
        "error_deg": worst,
        "worst_deg": worst,
        "radius_deg": float(patch["radius_deg"]),
        "center": patch["center"],
        "all_within": all_within,
        "reselect": False,
        "mode": mode,
        "count": patch["count"],
        "within_count": patch["within_count"],
        "outside_count": patch["outside_count"],
        "center_inside_target": center_gap <= tolerance + ANGLE_EPS,
        "center_in_edge_out": center_gap <= tolerance + ANGLE_EPS and not all_within,
    }


def report(image, config):
    x, y = config["seed_x"], config["seed_y"]
    chosen = sample(image, x, y, config)
    left = max(0, math.floor(config["cx"] - config["radius"]))
    right = min(image.shape[1], math.ceil(config["cx"] + config["radius"]) + 1)
    profile = [sample(image, column, y, config) for column in range(left, right)
               if ray(column, y, config) is not None]
    mode = config.get("check_mode", "point")
    point_aim = aiming(chosen, config)
    result = {"reading": chosen, "profile": profile, "check_mode": mode, "mode": mode,
              "point_aim": point_aim, "patch_aim": None}
    if mode == "patch":
        patch = bright_patch(image, config)
        patch_result = patch_aim(patch, config)
        result["patch"] = patch
        result["patch_aim"] = patch_result
        # The adopted style decides the card-wide verdict.
        result["aim"] = patch_result
        if patch.get("status") == "ok":
            # Convenience top-level mirrors for API/readout callers.
            result["center"] = patch["center"]
            result["center_yaw"] = patch["center"]["yaw"]
            result["center_pitch"] = patch["center"]["pitch"]
            result["radius_deg"] = patch["radius_deg"]
            result["all_within"] = patch["all_within"]
    else:
        result["patch"] = None
        result["aim"] = point_aim
    return result


def practice():
    width, height = 640, 464
    config = settings({
        "cx": 305, "cy": 231, "radius": 175, "seed_x": 342, "seed_y": 189,
        "target_yaw": 26, "target_pitch": 29, "tolerance": 13, "floor": 174,
    }, (width, height))
    yy, xx = np.indices((height, width))
    dx = (xx - config["cx"]) / config["radius"]
    dy = (yy - config["cy"]) / config["radius"]
    inside = dx * dx + dy * dy <= 1
    sphere = 20 + 29 * np.maximum(0, 1 - (dx * dx + dy * dy)) + 26 * (dy > 0.43)
    sphere += 244 * np.exp(-(((xx - 345 + 0.22 * (yy - 190)) / 37) ** 2 + ((yy - 190) / 12) ** 2))
    sphere += 243 * np.exp(-(((xx - 234) / 10) ** 2 + ((yy - 292) / 16) ** 2))
    sphere += 38 * np.exp(-(((xx - 390) / 48) ** 2 + ((yy - 324) / 20) ** 2))
    table = 20 + 12 * yy / height + 3 * np.sin(xx / 58)
    colors = [np.where(inside, sphere * ratio, table * tint)
              for ratio, tint in ((0.97, 0.77), (1.00, 0.93), (1.04, 1.19))]
    photo = Image.fromarray(np.clip(np.stack(colors, axis=-1), 0, 255).astype("uint8"), "RGB")
    return photo.size, png(photo), config
