"""Rim refinement: only photographic rim arcs may replace the hand-filled calibration."""

import io
import math
import os
import sys
import tempfile
import unittest
from zipfile import ZipFile

import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from glint import optics
from glint.server import create_app
from glint.store import Cards


def judge(target_yaw, target_pitch, tolerance):
    def aim(x, y, cx, cy, radius):
        return optics.aiming({"x": x, "y": y},
                             {"target_yaw": target_yaw, "target_pitch": target_pitch,
                              "tolerance": tolerance},
                             {"cx": cx, "cy": cy, "radius": radius})
    return aim


def scene(radius=175, center=(305, 231), width=640, height=464, seam=None, seam_span=(20, 280),
          block=None, noise=0.0, seed=1):
    """A mirror disc on a table; optional inner arc and an occluding pedestal."""
    yy, xx = np.indices((height, width))
    cx, cy = center
    distance = np.hypot(xx - cx, yy - cy)
    rng = np.random.default_rng(seed)
    image = np.repeat(np.full((height, width), 20.0)[:, :, None], 3, axis=2)
    for channel in range(3):
        image[:, :, channel] = np.where(distance <= radius, 200, image[:, :, channel])
    if block is not None:
        for channel in range(3):
            image[:, :, channel] = np.where(yy > cy + block, 120, image[:, :, channel])
    if seam is not None:
        angle = np.degrees(np.arctan2(-(yy - cy), xx - cx)) % 360
        arc = (np.abs(distance - seam) < 2.0) & (angle > seam_span[0]) & (angle < seam_span[1])
        for channel in range(3):
            image[:, :, channel] = np.where(arc, 60, image[:, :, channel])
    for channel in range(3):
        image[:, :, channel] = gaussian_filter(image[:, :, channel], 0.8) + rng.normal(0, noise, (height, width))
    return np.clip(image, 0, 255).astype(np.uint8)


def calibration(**changes):
    base = {"cx": 305, "cy": 231, "radius": 175, "seed_x": 342, "seed_y": 189,
            "target_yaw": 26, "target_pitch": 29, "tolerance": 13, "floor": 170}
    return {**base, **changes}


class RimPrecision(unittest.TestCase):
    def test_practice_rim_is_recovered_and_replaceable(self):
        size, png, settings = optics.practice()
        image = np.array(Image.open(io.BytesIO(png)).convert("RGB"))
        preview = optics.rim_preview(image, settings)
        self.assertEqual(preview["status"], "adoptable")
        self.assertAlmostEqual(preview["candidate"]["cx"], 305, delta=1)
        self.assertAlmostEqual(preview["candidate"]["cy"], 231, delta=1)
        self.assertAlmostEqual(preview["candidate"]["radius"], 175, delta=1.5)
        self.assertGreaterEqual(preview["candidate"]["supported_quadrants"], 3)

    def test_coarse_hand_mark_within_band_pulls_back_to_rim(self):
        image = scene()
        preview = optics.rim_preview(image, calibration(cx=316, cy=242, radius=160))
        self.assertEqual(preview["status"], "adoptable")
        self.assertAlmostEqual(preview["candidate"]["radius"], 175, delta=1.5)
        self.assertAlmostEqual(preview["candidate"]["cx"], 305, delta=1.5)

    def test_radius_outside_the_band_is_not_used(self):
        image = scene(radius=140)
        preview = optics.rim_preview(image, calibration())
        self.assertFalse(preview["adoptable"])
        self.assertIsNone(preview["candidate"])

    def test_single_arc_leaves_calibration_alone(self):
        image = scene(seam=None, block=None)
        yy, xx = np.indices(image.shape[:2])
        distance = np.hypot(xx - 305, yy - 231)
        angle = np.degrees(np.arctan2(-(yy - 231), xx - 305)) % 360
        arc = (np.abs(distance - 175) < 0.8) & (angle > 10) & (angle < 150)
        for channel in range(3):
            image[:, :, channel] = np.where(arc, 200, 20)
        preview = optics.rim_preview(image.astype(np.uint8), calibration())
        self.assertEqual(preview["status"], "insufficient")
        self.assertIsNone(preview["candidate"])

    def test_pedestal_straight_edge_is_not_rim(self):
        # The straight top of a base cuts the lower disc; the rim still needs three quadrants.
        preview = optics.rim_preview(scene(block=175 * 0.55), calibration())
        self.assertEqual(preview["status"], "adoptable")
        self.assertAlmostEqual(preview["candidate"]["radius"], 175, delta=1.5)

    def test_tangent_pedestal_edge_is_not_rim(self):
        # A pedestal edge touching the bottom of the circle must not fake a fourth arc.
        preview = optics.rim_preview(scene(block=174.5), calibration(tolerance=6, seed_x=305, seed_y=58,
                                                                     target_yaw=0, target_pitch=90))
        self.assertEqual(preview["status"], "adoptable")
        self.assertAlmostEqual(preview["candidate"]["radius"], 175, delta=1.5)

    def test_competing_close_contours_with_same_verdict_are_adoptable(self):
        preview = optics.rim_preview(scene(seam=160), calibration(tolerance=13))
        self.assertEqual(preview["status"], "adoptable")
        self.assertAlmostEqual(preview["candidate"]["radius"], 175, delta=1.5)
        self.assertGreater(len(preview["rivals"]), 1)

    def test_competing_close_contours_with_split_verdict_block_adoption(self):
        # A point at radius 171 is inside the true rim but outside the inner arc,
        # and the two rounds land on opposite sides of a 30° tolerance.
        point = (476, 231)
        preview = optics.rim_preview(scene(seam=160),
                                     calibration(seed_x=point[0], seed_y=point[1],
                                                 target_yaw=90, target_pitch=0, tolerance=30))
        self.assertEqual(preview["status"], "insufficient")
        self.assertIn("球外", preview["reason"])
        self.assertIsNone(preview["candidate"])

    def test_full_frame_and_blurred_real_shapes_stay_stable(self):
        preview = optics.rim_preview(scene(noise=4), calibration(cx=310, cy=238, radius=166))
        self.assertEqual(preview["status"], "adoptable")
        self.assertAlmostEqual(preview["candidate"]["radius"], 175, delta=1.5)


class RimSaving(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.app = create_app(self.root)
        self.client = self.app.test_client()

    def card(self):
        return self.client.post("/glint/practice").get_json()

    def test_preview_contract(self):
        card = self.card()
        body = self.client.get(f'/glint/cards/{card["id"]}/rim?revision={card["revision"]}').get_json()
        self.assertTrue(body["adoptable"])
        self.assertEqual(body["coarse"], {"cx": card["settings"]["cx"], "cy": card["settings"]["cy"],
                                          "radius": card["settings"]["radius"]})
        self.assertIn("quadrants", body["candidate"])
        self.assertEqual(body["preview"], body["candidate"])

    def test_adopt_writes_centre_and_radius_but_keeps_point_and_tolerance(self):
        card = self.card()
        body = self.client.get(f'/glint/cards/{card["id"]}/rim?revision={card["revision"]}').get_json()
        response = self.client.post(f'/glint/cards/{card["id"]}/rim?revision={card["revision"]}',
                                    json={"revision": card["revision"], "name": "镜球复核",
                                          "settings": card["settings"], "candidate": body["candidate"]})
        self.assertEqual(response.status_code, 201)
        updated = response.get_json()
        self.assertEqual(updated["revision"], card["revision"] + 1)
        self.assertEqual(updated["name"], "镜球复核")
        for key in ("cx", "cy", "radius"):
            self.assertAlmostEqual(updated["settings"][key], body["candidate"][key], places=2)
        for key in ("seed_x", "seed_y", "target_yaw", "target_pitch", "tolerance", "floor"):
            self.assertEqual(updated["settings"][key], card["settings"][key])
        result = self.client.get(f'/glint/cards/{card["id"]}/measurement?revision={updated["revision"]}').get_json()
        self.assertEqual(result["aim"]["status"], "pass")

    def test_insufficient_rim_never_replaces_calibration(self):
        image = scene(seam=None, block=None)
        yy, xx = np.indices(image.shape[:2])
        distance = np.hypot(xx - 305, yy - 231)
        arc = (np.abs(distance - 175) < 0.8) & (np.degrees(np.arctan2(-(yy - 231), xx - 305)) % 360 < 150)
        for channel in range(3):
            image[:, :, channel] = np.where(arc, 200, 20)
        cards = Cards(os.path.join(self.root, "cards"))
        stored = cards.create("单弧", "arc.png", (640, 464), optics.png(Image.fromarray(image.astype(np.uint8))),
                              calibration(), synthetic=True)
        revision = stored["revision"]
        body = self.client.get(f'/glint/cards/{stored["id"]}/rim?revision={revision}').get_json()
        self.assertFalse(body["adoptable"])
        response = self.client.post(f'/glint/cards/{stored["id"]}/rim?revision={revision}',
                                    json={"revision": revision, "name": "单弧", "settings": {},
                                          "candidate": {"cx": 305, "cy": 231, "radius": 175}})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(cards.read(stored["id"])["settings"], calibration())

    def test_stale_or_tampered_adoption_is_rejected(self):
        card = self.card()
        body = self.client.get(f'/glint/cards/{card["id"]}/rim?revision={card["revision"]}').get_json()
        tampered = {**body["candidate"], "radius": body["candidate"]["radius"] + 4}
        response = self.client.post(f'/glint/cards/{card["id"]}/rim?revision={card["revision"]}',
                                    json={"revision": card["revision"], "name": card["name"],
                                          "settings": card["settings"], "candidate": tampered})
        self.assertEqual(response.status_code, 409)

    def test_archive_carries_rim_evidence(self):
        card = self.card()
        response = self.client.get(f'/glint/cards/{card["id"]}/export/observation.zip?revision={card["revision"]}')
        self.assertEqual(response.status_code, 200)
        with ZipFile(io.BytesIO(response.data)) as package:
            measurement = __import__("json").loads(package.read("measurement.json"))
        self.assertEqual(measurement["rim"]["status"], "adoptable")
        self.assertIn("band", measurement["rim"]["evidence"])
        self.assertEqual(measurement["rim"]["adoptable"], True)


if __name__ == "__main__":
    unittest.main()
