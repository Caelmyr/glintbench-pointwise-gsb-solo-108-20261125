"""Create the saved measurement's working proof, trace, and handoff archive."""

from io import BytesIO, StringIO
from zipfile import ZIP_DEFLATED, ZipFile
import csv
import json

from PIL import Image, ImageDraw

from .optics import png


def trace_table(result):
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(("x_px", "y_px", "r", "g", "b", "luma", "above_floor", "yaw_deg", "pitch_deg"))
    for row in result["profile"]:
        direction = row["direction"]
        writer.writerow((row["x"], row["y"], *row["rgb"], f'{row["level"]:.3f}', int(row["above_floor"]),
                         f'{direction["yaw"]:.3f}', f'{direction["pitch"]:.3f}'))
    return buffer.getvalue().encode()


def proof(image, card, result):
    height, width = image.shape[:2]
    screen_width = max(420, width)
    output = Image.new("RGB", (screen_width, height + 64), "#172124")
    output.paste(Image.fromarray(image), (0, 0))
    draw = ImageDraw.Draw(output)
    p = card["settings"]
    cx, cy, radius = p["cx"], p["cy"], p["radius"]
    draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), outline="#84e2cd", width=2)
    reading = result["reading"]
    x, y = reading["x"], reading["y"]
    draw.line((x - 8, y, x + 8, y), fill="#edbf7c", width=2)
    draw.line((x, y - 8, x, y + 8), fill="#edbf7c", width=2)
    if direction := reading["direction"]:
        line = f'Yaw {direction["yaw"]:.2f} deg  Pitch {direction["pitch"]:.2f} deg'
        detail = f'{result["aim"]["status"].upper()}  gap {result["aim"]["error_deg"]:.2f} deg  limit {p["tolerance"]:.1f} deg'
    else:
        line, detail = "Outside calibrated sphere", "No direction"
    draw.text((14, height + 14), line, fill="#eeeeee")
    draw.text((14, height + 35), detail, fill="#edbf7c")
    return png(output)


def package(card, capture, image, result, rim=None):
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as output:
        output.writestr("capture.png", capture)
        measurement = {"card": card, "reading": result["reading"], "aim": result["aim"]}
        if rim is not None:
            measurement["rim"] = rim
        output.writestr("measurement.json", json.dumps(measurement, ensure_ascii=False, indent=2))
        output.writestr("profile.csv", trace_table(result))
        output.writestr("aim-proof.png", proof(image, card, result))
    return buffer.getvalue()
