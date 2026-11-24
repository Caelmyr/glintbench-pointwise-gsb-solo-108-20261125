"""Create the saved measurement's working proof, trace, and handoff archive."""

from io import BytesIO, StringIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile
import csv
import json

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .optics import png


FONT_FILE = Path(__file__).resolve().parents[1] / "static" / "fonts" / "NotoSansSC-Regular.otf"
_font_cache = {}

MINT = (132, 226, 203)
MINT_HEX = "#84e2cb"
AMBER = (237, 191, 124)
AMBER_HEX = "#edbf7c"
ALERT = (255, 107, 94)
ALERT_HEX = "#ff6b5e"
INK = "#eef5f2"


def font(size):
    if size not in _font_cache:
        try:
            _font_cache[size] = ImageFont.truetype(str(FONT_FILE), size)
        except OSError:
            _font_cache[size] = ImageFont.load_default()
    return _font_cache[size]


def trace_table(result):
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(("x_px", "y_px", "r", "g", "b", "luma", "above_floor", "yaw_deg", "pitch_deg"))
    for row in result["profile"]:
        direction = row["direction"]
        writer.writerow((row["x"], row["y"], *row["rgb"], f'{row["level"]:.3f}', int(row["above_floor"]),
                         f'{direction["yaw"]:.3f}', f'{direction["pitch"]:.3f}'))
    return buffer.getvalue().encode()


def patch_table(image, card, result):
    """Every connected sheet pixel with its reflected angle and target gap."""
    from .optics import angles, ray

    patch = result.get("patch")
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(("x_px", "y_px", "luma", "yaw_deg", "pitch_deg",
                     "gap_to_target_deg", "within_tolerance"))
    if not patch or patch.get("status") != "ok":
        return buffer.getvalue().encode()

    config = card["settings"]
    from .optics import angular_gap, target_ray
    target = target_ray(config)
    tolerance = float(config["tolerance"])
    pixels = np.asarray(patch["pixels"], dtype=np.int64)
    levels = image[pixels[:, 1], pixels[:, 0]] @ np.array([0.2126, 0.7152, 0.0722])
    for (x, y), level in zip(pixels, levels):
        direction = ray(int(x), int(y), config)
        angle = angles(direction)
        gap = angular_gap(direction, target)
        writer.writerow((int(x), int(y), f"{float(level):.3f}",
                         f'{angle["yaw"]:.4f}', f'{angle["pitch"]:.4f}',
                         f"{gap:.4f}", int(gap <= tolerance + 1e-7)))
    return buffer.getvalue().encode()


def _blend_patch(image, patch):
    canvas = image.astype(np.float64).copy()
    pixels = np.asarray(patch["pixels"], dtype=np.int64)
    xs, ys = pixels[:, 0], pixels[:, 1]
    canvas[ys, xs] = 0.42 * np.array(MINT) + 0.58 * canvas[ys, xs]
    over = np.asarray(patch["over_pixels"], dtype=np.int64)
    if len(over):
        ox, oy = over[:, 0], over[:, 1]
        canvas[oy, ox] = 0.62 * np.array(ALERT) + 0.38 * canvas[oy, ox]
    return Image.fromarray(np.clip(canvas, 0, 255).astype("uint8"), "RGB")


def _marker(draw, x, y, radius, color, width=2):
    draw.ellipse((x - radius, y - radius, x + radius, y + radius), outline=color, width=width)


def proof(image, card, result):
    patch = result.get("patch") if result.get("check_mode") == "patch" else None
    base = _blend_patch(image, patch) if patch and patch.get("status") == "ok" else Image.fromarray(image)
    height, width = image.shape[:2]
    screen_width = max(560, width)
    footer = 118
    output = Image.new("RGB", (screen_width, height + footer), "#172124")
    output.paste(base, (0, 0))
    draw = ImageDraw.Draw(output)
    small = font(15)
    bold = font(17)

    p = card["settings"]
    cx, cy, radius_px = p["cx"], p["cy"], p["radius"]
    draw.ellipse((cx - radius_px, cy - radius_px, cx + radius_px, cy + radius_px),
                 outline=MINT_HEX, width=2)

    reading = result["reading"]
    x, y = reading["x"], reading["y"]
    cross = max(7, width / 90)
    draw.line((x - cross, y, x + cross, y), fill=AMBER_HEX, width=2)
    draw.line((x, y - cross, x, y + cross), fill=AMBER_HEX, width=2)

    aim = result["aim"]
    mode_patch = result.get("check_mode") == "patch"

    line1 = (f"核对方式：整片核对 PATCH · 四连通亮片 · 下限 {p['floor']}"
             if mode_patch else "核对方式：单点核对 POINT")

    if patch and patch.get("status") == "ok":
        center = patch["center"]
        ccx, ccy = center["x"], center["y"]
        diamond = 9
        draw.polygon(((ccx, ccy - diamond), (ccx + diamond, ccy),
                      (ccx, ccy + diamond), (ccx - diamond, ccy)),
                     outline="#7dffe2", width=3)
        _marker(draw, ccx, ccy, 14, "#7dffe2", width=2)
        far = patch["far_point"]
        draw.line((ccx, ccy, far["x"], far["y"]), fill=ALERT_HEX, width=2)
        _marker(draw, far["x"], far["y"], 6, ALERT_HEX, width=2)
        center_label = "建议中心"
        label_w = draw.textlength(center_label, font=small)
        draw.text((ccx - label_w / 2, ccy - 40), center_label, font=small, fill="#cffff2")
        far_label = f"最远 {patch['radius_deg']:.1f}°"
        if far["x"] >= ccx:
            far_xy = (min(far["x"] + 9, width - 118), far["y"] + 6)
        else:
            far_xy = (max(far["x"] - 104, 4), far["y"] + 6)
        draw.text(far_xy, far_label, font=small, fill=ALERT_HEX)
        line2 = (f"建议中心 Yaw {center['yaw']:.2f}°  Pitch {center['pitch']:.2f}°"
                 f"    最远亮片角距 {patch['radius_deg']:.2f}°")
        line3 = (f"整片 {patch['count']} px · 最远端距目标 {patch['target_worst_deg']:.2f}°"
                 f" / 容差 {p['tolerance']:.1f}° · 超出 {patch['outside_count']}/{patch['count']} px")
        if aim["status"] == "pass":
            line4, color4 = f"结论：合格 PASS · 整片所有实测方向均在容差内", MINT_HEX
        elif aim.get("center_in_edge_out"):
            line4, color4 = ("结论：不合格 FAIL · 建议中心在容差内，但亮片边缘超出", ALERT_HEX)
        else:
            line4, color4 = (f"结论：不合格 FAIL · 整片最大夹角 {patch['target_worst_deg']:.2f}° 超过容差",
                             ALERT_HEX)
    elif aim["status"] == "outside" or reading["direction"] is None:
        line2 = f"选点 ({x}, {y}) 在标定圆外"
        line3 = "圆外像素不参与整片，也不能用于连通" if mode_patch else "No direction"
        line4, color4 = "结论：请重新选点 RESELECT · 反光点在标定圆外", AMBER_HEX
    elif aim["status"] == "below_floor":
        line2 = f"选点 ({x}, {y}) 亮度 {reading['level']:.1f} < 下限 {p['floor']}"
        line3 = "该点没有达标，无法长出整片亮片"
        line4, color4 = "结论：请重新选点 RESELECT · 点中像素低于亮区下限", AMBER_HEX
    else:
        direction = reading["direction"]
        line2 = f"Yaw {direction['yaw']:.2f}°  Pitch {direction['pitch']:.2f}°"
        line3 = f"距目标 {aim['error_deg']:.2f}° · 容差 {p['tolerance']:.1f}°"
        if aim["status"] == "pass":
            line4, color4 = f"结论：合格 PASS · 单点夹角 {aim['error_deg']:.2f}° 在容差内", MINT_HEX
        else:
            line4, color4 = f"结论：不合格 FAIL · 单点夹角 {aim['error_deg']:.2f}° 超出容差", ALERT_HEX

    draw.text((14, height + 9), line1, font=small, fill=INK)
    draw.text((14, height + 31), line2, font=small, fill="#eeeeee")
    draw.text((14, height + 53), line3, font=small, fill="#b9c9c5")
    draw.text((14, height + 78), line4, font=bold, fill=color4)
    return png(output)


def package(card, capture, image, result):
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as output:
        output.writestr("capture.png", capture)
        output.writestr("measurement.json", json.dumps({"card": card, "reading": result["reading"],
                                                        "aim": result["aim"],
                                                        "point_aim": result.get("point_aim"),
                                                        "patch_aim": result.get("patch_aim"),
                                                        "patch": result.get("patch"),
                                                        "check_mode": result.get("check_mode")},
                                                       ensure_ascii=False, indent=2))
        output.writestr("profile.csv", trace_table(result))
        if result.get("patch"):
            output.writestr("patch.csv", patch_table(image, card, result))
        output.writestr("aim-proof.png", proof(image, card, result))
    return buffer.getvalue()
