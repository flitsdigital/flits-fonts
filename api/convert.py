import io
import os
import sys
import traceback
import zipfile

from flask import Flask, request, send_file, jsonify

# ---------------------------------------------------------------------------
# Startup diagnostics — printed to Vercel function logs on cold start
# ---------------------------------------------------------------------------
print(f"[fontconv] Python {sys.version}", file=sys.stderr)

_IMPORT_ERRORS: dict = {}

def _try_import(name: str):
    try:
        mod = __import__(name)
        for part in name.split(".")[1:]:
            mod = getattr(mod, part)
        print(f"[fontconv] import {name!r} OK — {getattr(mod, '__file__', '?')}", file=sys.stderr)
        return mod
    except Exception as exc:
        print(f"[fontconv] import {name!r} FAILED: {exc}", file=sys.stderr)
        _IMPORT_ERRORS[name] = str(exc)
        return None

_ttLib     = _try_import("fontTools.ttLib")
TTFont     = getattr(_ttLib, "TTFont", None)
_woff2_mod = _try_import("fontTools.ttLib.woff2")
_brotli    = _try_import("brotlicffi") or _try_import("brotli")

print(f"[fontconv] TTFont={TTFont}, woff2={_woff2_mod}, brotli={_brotli}", file=sys.stderr)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024

ALLOWED  = {"ttf", "otf", "woff", "woff2"}
MAX_BYTES = 50 * 1024 * 1024

MIME = {
    "ttf":   "font/ttf",
    "otf":   "font/otf",
    "woff":  "font/woff",
    "woff2": "font/woff2",
}


def _ext(name: str) -> str:
    return os.path.splitext(name)[1].lower().lstrip(".")


def _probe_imports() -> dict:
    results = {"python": sys.version, "startup_errors": _IMPORT_ERRORS}
    for mod in ("flask", "fontTools", "fontTools.ttLib", "fontTools.ttLib.woff2", "brotlicffi", "brotli"):
        try:
            __import__(mod)
            results[mod] = "ok"
        except Exception as exc:
            results[mod] = f"FAILED: {exc}"
    return results


def _to_raw(data: bytes, src: str) -> bytes:
    """Strip WOFF/WOFF2 wrapper → raw sfnt bytes."""
    if src == "woff2":
        if _woff2_mod is None:
            raise RuntimeError(f"fontTools.ttLib.woff2 niet beschikbaar: {_IMPORT_ERRORS.get('fontTools.ttLib.woff2')}")
        out = io.BytesIO()
        _woff2_mod.decompress(io.BytesIO(data), out)
        return out.getvalue()
    if src == "woff":
        font = TTFont(io.BytesIO(data))
        font.flavor = None
        out = io.BytesIO()
        font.save(out)
        return out.getvalue()
    return data


def _to_target(raw: bytes, tgt: str) -> bytes:
    """Wrap raw sfnt bytes in target container."""
    if tgt == "woff2":
        if _woff2_mod is None:
            raise RuntimeError(f"fontTools.ttLib.woff2 niet beschikbaar: {_IMPORT_ERRORS.get('fontTools.ttLib.woff2')}")
        out = io.BytesIO()
        _woff2_mod.compress(io.BytesIO(raw), out)
        return out.getvalue()
    if tgt == "woff":
        font = TTFont(io.BytesIO(raw))
        font.flavor = "woff"
        out = io.BytesIO()
        font.save(out)
        return out.getvalue()
    return raw


def _convert_one(f, tgt: str) -> tuple[str, bytes] | str:
    """Convert a single FileStorage object. Returns (filename, bytes) or error string."""
    src = _ext(f.filename)
    if src not in ALLOWED:
        return f"Niet-ondersteund formaat: .{src}"
    if src == tgt:
        return f"Al {tgt.upper()} — overgeslagen"
    data = f.read()
    if len(data) > MAX_BYTES:
        return "Bestand te groot (max 50 MB)"
    try:
        raw    = _to_raw(data, src)
        result = _to_target(raw, tgt)
        base   = os.path.splitext(f.filename)[0]
        return (f"{base}.{tgt}", result)
    except Exception as exc:
        tb = traceback.format_exc()
        print(tb, file=sys.stderr)
        return str(exc)


# ── Health endpoint ──────────────────────────────────────────────────────────

@app.route("/api/health", methods=["GET"])
@app.route("/health", methods=["GET"])
def health():
    return jsonify(_probe_imports())


# ── Conversion endpoint ──────────────────────────────────────────────────────

@app.route("/", methods=["POST"])
@app.route("/api/convert", methods=["POST"])
def convert():
    files = request.files.getlist("font")
    if not files or not files[0].filename:
        return jsonify({"error": "Geen bestand meegestuurd."}), 400

    tgt = request.form.get("format", "").lower().lstrip(".")
    if tgt not in ALLOWED:
        return jsonify({"error": f"Ongeldig doelformaat: .{tgt}"}), 400

    # Convert each file
    successes = []  # list of (out_filename, bytes)
    errors    = []  # list of {file, error}

    for f in files:
        if not f.filename:
            continue
        result = _convert_one(f, tgt)
        if isinstance(result, tuple):
            successes.append(result)
        else:
            errors.append({"file": f.filename, "error": result})

    if not successes:
        msg = errors[0]["error"] if errors else "Geen conversies geslaagd."
        return jsonify({"error": msg, "details": errors}), 400

    # Single file → direct download
    if len(successes) == 1 and len(files) == 1:
        out_name, data = successes[0]
        return send_file(
            io.BytesIO(data),
            mimetype=MIME[tgt],
            as_attachment=True,
            download_name=out_name,
        )

    # Multiple files → ZIP
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for out_name, data in successes:
            zf.writestr(out_name, data)
    zip_buf.seek(0)

    return send_file(
        zip_buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"fonts-{tgt}.zip",
    )
