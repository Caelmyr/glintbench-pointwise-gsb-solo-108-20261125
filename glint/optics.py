"""Measure apparent source directions from a calibrated spherical mirror photo."""

from io import BytesIO
import math

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

from image_lab.validation import Invalid, integer, number


LUMA = np.array([0.2126, 0.7152, 0.0722])
MAX_IMAGE_BYTES = 18 * 1024 * 1024


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
        return {"status": "outside", "error_deg": None}
    error = angular_gap(measured, target_ray(config))
    return {"status": "pass" if error <= config["tolerance"] else "miss", "error_deg": error}


def report(image, config):
    x, y = config["seed_x"], config["seed_y"]
    chosen = sample(image, x, y, config)
    left = max(0, math.floor(config["cx"] - config["radius"]))
    right = min(image.shape[1], math.ceil(config["cx"] + config["radius"]) + 1)
    profile = [sample(image, column, y, config) for column in range(left, right)
               if ray(column, y, config) is not None]
    return {"reading": chosen, "aim": aiming(chosen, config), "profile": profile}


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
