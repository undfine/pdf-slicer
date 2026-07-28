"""
Review server for the region-editor prototype. Two ways to use it:

1. Embedded (the fast path): pdf_slicer.py's --review flag calls
   run_review_session() directly, in-process, with a live PyMuPDF page
   already open. Nothing touches disk until you click Export — regions and
   the preview image are served straight out of memory.

       python3 pdf_slicer.py your-file.pdf --review

2. Standalone: for reviewing output from an earlier --detect-only run,
   possibly in a separate terminal/session.

       python3 pdf_slicer.py your-file.pdf --detect-only
       python3 review_server.py your-file_Assets_1200px

Either way, clicking Export in the browser runs the real pipeline
(harvest + crop + manifest) immediately and writes the finished slices next
to the source PDF — no intermediate regions file is ever written.
"""
import json
import os
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import fitz  # PyMuPDF

import pdf_slicer

DEFAULT_PORT = 8000
EDITOR_HTML = os.path.join(os.path.dirname(os.path.abspath(__file__)), "region-editor-prototype.html")


class ReviewHandler(BaseHTTPRequestHandler):
    """
    Generic handler — the three callables below are bound (as staticmethods)
    by whichever mode starts the server, so this class doesn't need to know
    whether it's talking to a live in-memory page or files on disk.
    """
    get_regions = None      # () -> {"meta": ..., "regions": [...]}
    get_page_bytes = None   # () -> (bytes, content_type)
    do_export = None        # (edited_regions: list) -> summary dict
    on_export_done = None   # () -> None, optional

    def _send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self._send_bytes(body, "application/json", status)

    def _send_bytes(self, body, content_type, status=200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            with open(EDITOR_HTML, "rb") as f:
                self._send_bytes(f.read(), "text/html; charset=utf-8")
        elif self.path == "/api/regions":
            try:
                self._send_json(200, self.get_regions())
            except Exception as e:
                self._send_json(500, {"error": str(e)})
        elif self.path == "/api/page":
            try:
                body, content_type = self.get_page_bytes()
                self._send_bytes(body, content_type)
            except Exception as e:
                self._send_json(404, {"error": str(e)})
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/api/export":
            self._send_json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length))
            regions = payload.get("regions") if isinstance(payload, dict) else payload
            if not isinstance(regions, list) or not regions:
                raise ValueError('expected {"regions": [...]} with at least one region')
            summary = self.do_export(regions)
        except Exception as e:
            self._send_json(400, {"error": str(e)})
            return
        self._send_json(200, summary)
        if self.on_export_done:
            self.on_export_done()

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def _bind_server(port):
    """Tries a small range of ports in case one is already taken by another session."""
    last_err = None
    for candidate in range(port, port + 10):
        try:
            return ThreadingHTTPServer(("localhost", candidate), ReviewHandler), candidate
        except OSError as e:
            last_err = e
    raise OSError(f"No free port found in range {port}-{port + 9}: {last_err}")


def run_review_session(page, matrix, width, height, target_width, output_folder, prefix,
                        abs_pdf_path, bands, classify_band, export_bands, port=DEFAULT_PORT):
    """
    Embedded mode: serves regions + a preview image straight out of memory,
    blocks until the browser POSTs an Export, runs the real export in-process,
    then returns a summary — so a Shortcut invoking `--review` finishes only
    once the review is actually done.
    """
    regions = [
        {"id": i, "y0": y0, "y1": y1, "top_layer_kind": classify_band(page, width, y0, y1)}
        for i, (y0, y1) in enumerate(bands, start=1)
    ]
    meta = {
        "source": os.path.basename(abs_pdf_path),
        "pdf_path": abs_pdf_path,
        "target_width": target_width,
        "page_width": width,
        "page_height": height,
        "scale": target_width / width,
    }
    preview_bytes = page.get_pixmap(matrix=matrix, colorspace=fitz.csRGB, alpha=False).tobytes(
        "jpg", jpg_quality=90
    )

    done = threading.Event()
    result = {}

    def get_regions():
        return {"meta": meta, "regions": regions}

    def get_page_bytes():
        return preview_bytes, "image/jpeg"

    def do_export(edited_regions):
        edited_bands = sorted(
            ((float(r["y0"]), float(r["y1"])) for r in edited_regions), key=lambda b: b[0]
        )
        slices_data = export_bands(
            page, matrix, width, edited_bands, output_folder, prefix, abs_pdf_path, target_width
        )
        result["output_folder"] = output_folder
        result["slice_count"] = len(slices_data)
        return {"status": "ok", "output_folder": output_folder, "slice_count": len(slices_data)}

    def on_export_done():
        done.set()

    ReviewHandler.get_regions = staticmethod(get_regions)
    ReviewHandler.get_page_bytes = staticmethod(get_page_bytes)
    ReviewHandler.do_export = staticmethod(do_export)
    ReviewHandler.on_export_done = staticmethod(on_export_done)

    server, bound_port = _bind_server(port)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://localhost:{bound_port}"
    print(f" - Review at {url}  (waiting for Export...)")
    webbrowser.open(url)

    done.wait()
    server.shutdown()
    return result


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 review_server.py <output_folder> [port]")
        print('  <output_folder> is the "*_Assets_*px" folder written by --detect-only')
        sys.exit(1)

    output_folder = os.path.abspath(sys.argv[1])
    regions_path = os.path.join(output_folder, "regions.json")
    if not os.path.isfile(regions_path):
        print(f"Error: no regions.json found in {output_folder}")
        print("Run: python3 pdf_slicer.py your-file.pdf --detect-only")
        sys.exit(1)

    port = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_PORT

    def get_regions():
        with open(regions_path) as f:
            return json.load(f)

    def get_page_bytes():
        with open(regions_path) as f:
            meta = json.load(f)["meta"]
        with open(meta["preview_image"], "rb") as f:
            return f.read(), "image/jpeg"

    def do_export(edited_regions):
        with open(regions_path) as f:
            meta = json.load(f)["meta"]
        doc = fitz.open(meta["pdf_path"])
        page = doc[0]
        zoom = meta["target_width"] / meta["page_width"]
        matrix = fitz.Matrix(zoom, zoom)
        prefix = pdf_slicer.get_abbreviated_prefix(meta["pdf_path"])
        bands = sorted(
            ((float(r["y0"]), float(r["y1"])) for r in edited_regions), key=lambda b: b[0]
        )
        slices_data = pdf_slicer.export_bands(
            page, matrix, meta["page_width"], bands, output_folder, prefix,
            meta["pdf_path"], meta["target_width"],
        )
        return {"status": "ok", "output_folder": output_folder, "slice_count": len(slices_data)}

    ReviewHandler.get_regions = staticmethod(get_regions)
    ReviewHandler.get_page_bytes = staticmethod(get_page_bytes)
    ReviewHandler.do_export = staticmethod(do_export)
    ReviewHandler.on_export_done = None

    server, bound_port = _bind_server(port)
    print(f"Reviewing: {output_folder}")
    print(f"Serving at http://localhost:{bound_port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
