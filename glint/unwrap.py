"""Unwrap the calibrated mirror ball into a full 360 by 180 degree direction map.

The map inverts the same orthographic reflection used for point measurement.
Every output cell keeps the integer original pixel it sampled, so a cell can be
handed back to the existing reflection-point workflow without interpolation.
Cells with no usable original pixel stay fully transparent.
"""

import math
from io import BytesIO

import numpy as np
from PIL import Image, PngImagePlugin

from . import optics


SEAM_LIMIT = 30.0          # degrees away from the back-facing meridian
SEAM_STEP = 0.5            # seam search resolution in degrees
MIN_WIDTH = 360            # tiny balls still get a readable full circle
MAX_WIDTH = 1920
BLOCK_ROWS = 128
CHANNELS = 4


def wrap180(value):
    """Normalise an azimuth to (-180, 180]."""
    result = (float(value) + 180.0) % 360.0 - 180.0
    return 180.0 if result == -180.0 else result


def layout(config):
    """Return an even output width and the matching 2:1 height.

    One output column per original pixel across the visible hemisphere gives
    2*pi*radius columns for the full turn, matching the original's angular
    detail at the ball centre.
    """
    width = int(round(2 * math.pi * config["radius"]))
    width = max(MIN_WIDTH, min(MAX_WIDTH, width))
    width += width % 2
    return width, width // 2


def pitch_rows(height):
    return 90.0 - (np.arange(height, dtype=np.float64) + 0.5) * (180.0 / height)


def yaw_columns(seam, width):
    return seam + (np.arange(width, dtype=np.float64) + 0.5) * (360.0 / width)


def source_pixels(yaw, pitch, config, size):
    """Map directions to the original pixel centres that hold their reflection.

    A direction reflects onto a point ``radius * sin(theta / 2)`` from the ball
    centre, where theta is its angle from the camera axis.  The nearest
    original pixel centre is used only when it is inside both the calibrated
    circle and the photograph, so a cropped sphere edge and the background
    around the ball never enter the unwrap; those cells stay transparent and
    no source is invented or interpolated.
    """
    yaw = np.radians(np.asarray(yaw, dtype=np.float64))
    pitch = np.radians(np.asarray(pitch, dtype=np.float64))
    cosine = np.cos(pitch)
    dx = np.sin(yaw) * cosine
    dy = np.sin(pitch)
    dz = np.cos(yaw) * cosine
    denominator = 2.0 + 2.0 * dz
    usable = denominator > 1e-12
    root = np.sqrt(np.where(usable, denominator, 1.0))
    nx = np.where(usable, dx / root, 0.0)
    ny = np.where(usable, dy / root, 0.0)
    frame_width, frame_height = size
    x = np.floor(config["cx"] + config["radius"] * nx + 0.5)
    y = np.floor(config["cy"] - config["radius"] * ny + 0.5)
    usable = usable & (x >= 0) & (x < frame_width) & (y >= 0) & (y < frame_height)
    usable = usable & (((x - config["cx"]) ** 2 + (y - config["cy"]) ** 2) < config["radius"] ** 2)
    return np.clip(x, 0, frame_width - 1).astype(np.int32), np.clip(y, 0, frame_height - 1).astype(np.int32), usable


def seam_costs(image, config, size, seams, width, height):
    """Score every candidate seam by the bright original pixels it would cut.

    Both columns that meet at a seam are sampled.  A distinct original pixel is
    counted once even when a column samples it twice near the rim, and the
    bright test uses the same floor as the saved measurement.  Cells with no
    original pixel are skipped: they cannot cut real highlight.
    """
    seams = np.asarray(seams, dtype=np.float64)
    step = 360.0 / width
    shape = (len(seams), 2, height)
    yaw = np.broadcast_to((seams[:, None, None] + np.array([step / 2.0, -step / 2.0])[None, :, None]), shape)
    pitch = np.broadcast_to(pitch_rows(height)[None, None, :], shape)
    x, y, usable = source_pixels(yaw, pitch, config, size)
    frame_width = size[0]
    key = np.where(usable, y.astype(np.int64) * frame_width + x, -1)
    flat_keys = key.reshape(len(seams), -1)
    levels = np.where(usable, image[y, x].astype(np.float32) @ optics.LUMA.astype(np.float32), 0.0)
    count = np.zeros(len(seams), dtype=np.int64)
    excess = np.zeros(len(seams), dtype=np.float64)
    for index in range(len(seams)):
        keys, first = np.unique(flat_keys[index], return_index=True)
        keep = keys >= 0
        bright = levels[index].reshape(-1)[first] >= config["floor"]
        bright &= keep
        count[index] = int(bright.sum())
        excess[index] = float(np.sum(levels[index].reshape(-1)[first][bright] - config["floor"]))
    return count, excess


def choose_seam(image, config, size, width, height):
    """Pick the seam meridian that cuts the fewest bright cells, nearest the back.

    Only meridians within SEAM_LIMIT of the camera-away direction qualify.  The
    bright-cell count above the saved floor comes first, so a real highlight is
    never split across the join while a darker meridian is available; ties fall
    back to the summed brightness excess and then to closeness to the back.
    """
    steps = int(round(2 * SEAM_LIMIT / SEAM_STEP))
    seams = 180.0 - SEAM_LIMIT + np.arange(steps + 1) * SEAM_STEP
    counts, excess = seam_costs(image, config, size, seams, width, height)
    best = min(range(len(seams)), key=lambda index: (int(counts[index]), round(float(excess[index]), 6),
                                                     abs(float(seams[index]) - 180.0)))
    return float(seams[best])


def inspect(image, config):
    """Return the seam, cell grid, and coverage without building the picture."""
    height, width = image.shape[:2]
    size = (width, height)
    out_width, out_height = layout(config)
    seam = choose_seam(image, config, size, out_width, out_height)
    yaw = yaw_columns(seam, out_width)
    pitches = pitch_rows(out_height)
    opaque = 0
    for start in range(0, out_height, BLOCK_ROWS):
        stop = min(out_height, start + BLOCK_ROWS)
        _, _, usable = source_pixels(yaw[None, :], pitches[start:stop, None], config, size)
        opaque += int(usable.sum())
    step = 360.0 / out_width
    return {
        "width": out_width,
        "height": out_height,
        "seam_yaw_deg": wrap180(seam),
        "seam_limit_deg": SEAM_LIMIT,
        "back_offset_deg": abs(seam - 180.0),
        "cell_yaw_deg": step,
        "cell_pitch_deg": 180.0 / out_height,
        "first_yaw_deg": wrap180(seam + step / 2.0),
        "last_yaw_deg": wrap180(seam - step / 2.0),
        "opaque_cells": opaque,
    }


def describe(image, config):
    """Return the seam and coverage metadata shown next to the unwrap."""
    meta = inspect(image, config)
    total = meta["width"] * meta["height"]
    meta["transparent_cells"] = total - meta["opaque_cells"]
    meta["transparent_fraction"] = meta["transparent_cells"] / total
    meta["note"] = "横向为完整360°，右端接回左端且不重复接缝列；纵向从+90°到-90°。只取标定球内实际原片像素，无来源的方向透明。"
    return meta


def grid(image, config):
    """Return the unwrap geometry, its seam, the RGBA cells, and the metadata."""
    meta = describe(image, config)
    size = (image.shape[1], image.shape[0])
    yaw = yaw_columns(meta["seam_yaw_deg"], meta["width"])
    pitches = pitch_rows(meta["height"])
    rgba = np.zeros((meta["height"], meta["width"], CHANNELS), dtype=np.uint8)
    for start in range(0, meta["height"], BLOCK_ROWS):
        stop = min(meta["height"], start + BLOCK_ROWS)
        x, y, usable = source_pixels(yaw[None, :], pitches[start:stop, None], config, size)
        block = rgba[start:stop]
        block[..., :3][usable] = image[y[usable], x[usable]]
        block[..., 3][usable] = 255
    return rgba, meta["seam_yaw_deg"], meta


def cell(image, config, u, v):
    """Describe one output cell and the original pixel it actually sampled."""
    height, width = image.shape[:2]
    meta = describe(image, config)
    if not (0 <= u < meta["width"] and 0 <= v < meta["height"]):
        return None
    yaw = meta["seam_yaw_deg"] + (u + 0.5) * meta["cell_yaw_deg"]
    pitch = 90.0 - (v + 0.5) * meta["cell_pitch_deg"]
    x, y, usable = source_pixels(np.array([yaw]), np.array([pitch]), config, (width, height))
    opaque = bool(usable[0])
    return {
        "u": u, "v": v,
        "yaw_deg": wrap180(yaw), "pitch_deg": pitch,
        "opaque": opaque,
        "source": optics.sample(image, int(x[0]), int(y[0]), config) if opaque else None,
    }


def png(image, config):
    """Encode the unwrap as an RGBA PNG; directions without a source are transparent."""
    rgba, _, meta = grid(image, config)
    info = PngImagePlugin.PngInfo()
    info.add_text("glint-seam-yaw-deg", f'{meta["seam_yaw_deg"]:.2f}')
    info.add_text("glint-back-offset-deg", f'{meta["back_offset_deg"]:.2f}')
    info.add_text("glint-seam-limit-deg", f'{meta["seam_limit_deg"]:.2f}')
    info.add_text("glint-transparent-fraction", f'{meta["transparent_fraction"]:.6f}')
    buffer = BytesIO()
    Image.fromarray(rgba, "RGBA").save(buffer, format="PNG", pnginfo=info)
    return buffer.getvalue()
