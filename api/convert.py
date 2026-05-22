import io
import os

from flask import Flask, request, send_file, jsonify
from fontTools.ttLib import TTFont
import fontTools.woff2 as woff2_module

app = Flask(__name__)

ALLOWED = {"ttf", "otf", "woff", "woff2"}
MAX_BYTES = 50 * 1024 * 1024

MIME = {
    "ttf":   "font/ttf",
    "otf":   "font/otf",
    "woff":  "font/woff",
    "woff2": "font/woff2",
}


def _ext(name: str) -> str:
    return os.path.splitext(name)[1].lower().lstrip(".")


def _to_raw(data: bytes, src: str) -> bytes:
    """Strip WOFF/WOFF2 wrapper → raw sfnt bytes."""
    if src == "woff2":
        out = io.BytesIO()
        woff2_module.decompress(io.BytesIO(data), out)
        return out.getvalue()
    if src == "woff":
        font = TTFont(io.BytesIO(data))
        font.flavor = None
        out = io.BytesIO()
        font.save(out)
        return out.getvalue()
    return data  # ttf / otf: already raw sfnt


def _to_target(raw: bytes, tgt: str) -> bytes:
    """Wrap raw sfnt bytes in target container."""
    if tgt == "woff2":
        out = io.BytesIO()
        woff2_module.compress(io.BytesIO(raw), out)
        return out.getvalue()
    if tgt == "woff":
        font = TTFont(io.BytesIO(raw))
        font.flavor = "woff"
        out = io.BytesIO()
        font.save(out)
        return out.getvalue()
    # ttf / otf: raw sfnt is the file — outline type is preserved as-is
    return raw


@app.route("/", methods=["POST"])
@app.route("/api/convert", methods=["POST"])
def convert():
    if "font" not in request.files:
        return jsonify({"error": "Geen bestand meegestuurd."}), 400

    f = request.files["font"]
    if not f.filename:
        return jsonify({"error": "Geen bestandsnaam."}), 400

    src = _ext(f.filename)
    if src not in ALLOWED:
        return jsonify({"error": f"Niet-ondersteund formaat: .{src}"}), 400

    tgt = request.form.get("format", "").lower().lstrip(".")
    if tgt not in ALLOWED:
        return jsonify({"error": f"Ongeldig doelformaat: .{tgt}"}), 400

    if src == tgt:
        return jsonify({"error": "Bron- en doelformaat zijn hetzelfde."}), 400

    data = f.read()
    if len(data) > MAX_BYTES:
        return jsonify({"error": "Bestand te groot (max 50 MB)."}), 413

    try:
        raw = _to_raw(data, src)
        result = _to_target(raw, tgt)
    except Exception as exc:
        return jsonify({"error": f"Conversie mislukt: {exc}"}), 500

    base = os.path.splitext(f.filename)[0]
    out_name = f"{base}.{tgt}"

    return send_file(
        io.BytesIO(result),
        mimetype=MIME[tgt],
        as_attachment=True,
        download_name=out_name,
    )
