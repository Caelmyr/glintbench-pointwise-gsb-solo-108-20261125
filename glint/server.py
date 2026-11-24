"""Connect the lighting bench to the photo preparation workspace."""

from io import BytesIO
import os
from pathlib import Path

from flask import Blueprint, jsonify, render_template, request, send_file, url_for

from image_lab import create_app as preparation_app
from image_lab.validation import Conflict, Invalid, integer, object_value

from . import exports, optics
from .store import Cards


def download(data, mime, filename):
    return send_file(BytesIO(data), mimetype=mime, max_age=0, download_name=filename, as_attachment=True)


def create_app(directory=None):
    root = Path(directory or os.environ.get("GLINT_DATA", ".glint-data")).resolve()
    desk = preparation_app(root / "prepare")
    cards = Cards(root / "cards")
    api = Blueprint("glint", __name__, url_prefix="/glint")

    @desk.get("/")
    def home():
        return render_template("glint.html")

    @api.get("/health")
    def health():
        return jsonify(status="ok", app="glint")

    @api.get("/cards")
    def listing():
        return jsonify(cards=cards.list())

    @api.post("/cards")
    def create():
        request.max_content_length = 20 * 1024 * 1024
        upload = request.files.get("photo")
        if not upload or not upload.filename:
            raise Invalid("请选择镜面球原片")
        size, image = optics.decode(upload.stream.read(optics.MAX_IMAGE_BYTES + 1))
        return jsonify(cards.create(request.form.get("name", ""), Path(upload.filename).name[:140],
                                    size, image, optics.defaults(size))), 201

    @api.post("/practice")
    def practice():
        return jsonify(cards.demo()), 201

    @api.route("/cards/<key>", methods=["GET", "PATCH"])
    def card(key):
        return jsonify(cards.update(key, object_value(request.get_json(silent=True), "测光卡设置"))
                       if request.method == "PATCH" else cards.read(key))

    @api.get("/cards/<key>/capture.png")
    def capture(key):
        return send_file(cards.photo(key), mimetype="image/png", conditional=True)

    def measurement(key):
        card = cards.read(key, request.args.get("revision"))
        image = optics.pixels(cards.photo(key))
        return card, image, optics.report(image, card["settings"])

    def refined(key):
        card = cards.read(key, request.args.get("revision"))
        return card, optics.rim_preview(optics.pixels(cards.photo(key)), card["settings"])

    @api.get("/cards/<key>/rim")
    def rim_json(key):
        card, preview = refined(key)
        return jsonify(revision=card["revision"], **preview)

    @api.post("/cards/<key>/rim")
    def rim_adopt(key):
        card, preview = refined(key)
        body = object_value(request.get_json(silent=True), "球缘精修")
        if not preview["adoptable"]:
            raise Invalid(preview["reason"] or "目前轮廓不足以定准，没有替换手填标定")
        candidate = object_value(body.get("candidate"), "精修圆")
        chosen = preview["candidate"]
        try:
            mismatch = any(abs(float(candidate.get(field)) - chosen[field]) > 0.01
                           for field in ("cx", "cy", "radius"))
        except (TypeError, ValueError):
            mismatch = True
        if mismatch:
            raise Conflict("精修结果已经变化，请重新预览")
        updated = cards.adopt_rim(key, {"revision": card["revision"], "status": preview["status"],
                                        "candidate": chosen},
                                  body.get("settings", {}), body.get("name", card["name"]))
        return jsonify(updated), 201

    @api.get("/cards/<key>/measurement")
    def measurement_json(key):
        card, _, result = measurement(key)
        return jsonify(revision=card["revision"], **result)

    @api.get("/cards/<key>/probe")
    def probe(key):
        card = cards.read(key, request.args.get("revision"))
        x = integer(request.args.get("x"), "X", 0, card["width"] - 1)
        y = integer(request.args.get("y"), "Y", 0, card["height"] - 1)
        return jsonify(optics.sample(optics.pixels(cards.photo(key)), x, y, card["settings"]))

    @api.get("/cards/<key>/export/profile.csv")
    def profile(key):
        _, _, result = measurement(key)
        return download(exports.trace_table(result), "text/csv", "mirror-profile.csv")

    @api.get("/cards/<key>/export/proof.png")
    def proof(key):
        card, image, result = measurement(key)
        return download(exports.proof(image, card, result), "image/png", "aim-proof.png")

    @api.get("/cards/<key>/export/observation.zip")
    def bundle(key):
        card, image, result = measurement(key)
        preview = optics.rim_preview(image, card["settings"])
        package = exports.package(card, cards.photo(key).read_bytes(), image, result,
                                  {key: preview[key] for key in ("status", "adoptable", "evidence")})
        return download(package, "application/zip", f"glint-r{card['revision']}.zip")

    @api.post("/cards/<key>/prepare")
    def prepare(key):
        card = cards.read(key, request.args.get("revision"))
        image = optics.pixels(cards.photo(key)).astype("float32") / 255
        services = desk.extensions
        asset = services["assets"].put(image, card["photo_name"])
        project = services["store"].create(f'{card["name"]} · 原片准备')
        services["frames"].add(project["id"], project["revision"], [asset["id"]])
        return jsonify(url=url_for("index", project=project["id"])), 201

    desk.register_blueprint(api)
    return desk
