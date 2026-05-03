import base64
from datetime import datetime
import fitz  # PyMuPDF
import hashlib
import json
import os
import re
import sys

# --- CONFIGURATION ---
DEFAULT_WIDTH = 1200  
OUTPUT_SUBFOLDER_SUFFIX = "_Assets"

# The number of original PDF points to shave off image edges to avoid borders
EDGE_INSET = 2.0  

def has_image_to_image_continuity_at_y(img_boxes, y, page_width, y_tolerance=6.0, min_width_ratio=0.6, min_overlap_ratio=0.5):
    """
    True when y looks like an internal seam between two wide images that visually
    continue into each other (typical of hero composites / overlays).
    """
    min_img_width = page_width * min_width_ratio
    min_x_overlap = page_width * min_overlap_ratio

    ending = [b for b in img_boxes if abs(b.y1 - y) <= y_tolerance and b.width >= min_img_width]
    starting = [b for b in img_boxes if abs(b.y0 - y) <= y_tolerance and b.width >= min_img_width]

    for upper in ending:
        for lower in starting:
            overlap = min(upper.x1, lower.x1) - max(upper.x0, lower.x0)
            if overlap >= min_x_overlap:
                return True

    return False

def get_top_layer_kind_at_y(page, page_width, y, y_tolerance=1.0, min_width_ratio=0.8):
    """
    Returns which object kind is topmost around y for near-full-width objects:
    'image', 'drawing', or None when unavailable.
    Uses page.get_bboxlog() paint order when supported by the PyMuPDF build.
    """
    if not hasattr(page, "get_bboxlog"):
        return None

    try:
        bboxlog = page.get_bboxlog()
    except Exception:
        return None

    top_kind = None
    top_seq = -1
    min_width = page_width * min_width_ratio

    for seq, entry in enumerate(bboxlog):
        obj_type = None
        bbox = None

        if isinstance(entry, (tuple, list)) and len(entry) >= 2:
            obj_type, bbox = entry[0], entry[1]
        elif isinstance(entry, dict):
            obj_type = entry.get("type")
            bbox = entry.get("bbox")

        if bbox is None:
            continue

        rect = fitz.Rect(bbox)
        if rect.is_empty or rect.width < min_width:
            continue

        if not (rect.y0 - y_tolerance <= y <= rect.y1 + y_tolerance):
            continue

        t = str(obj_type).lower()
        kind = None
        if "image" in t:
            kind = "image"
        elif "path" in t:
            kind = "drawing"

        if kind and seq > top_seq:
            top_seq = seq
            top_kind = kind

    return top_kind

def get_abbreviated_prefix(filepath):
    base_name = os.path.splitext(os.path.basename(filepath))[0]
    parts = re.split(r'[\s-]', base_name)
    return parts[0] if parts else "Email"

def get_slice_info(page, clip):
    """
    Returns image coverage ratio and the bounding box of the dominant image.
    """
    img_info = page.get_image_info(hashes=False, xrefs=True)
    slice_area = clip.width * clip.height
    image_coverage = 0
    dominant_img_rect = None
    
    for img in img_info:
        img_rect = fitz.Rect(img["bbox"])
        intersect = clip & img_rect
        if not intersect.is_empty:
            area = intersect.width * intersect.height
            image_coverage += area
            # Track the largest image in this slice
            if dominant_img_rect is None or area > (dominant_img_rect.width * dominant_img_rect.height):
                dominant_img_rect = img_rect
            
    ratio = image_coverage / (slice_area or 1)
    return ratio, dominant_img_rect

def get_combined_image_bounds(page, clip):
    """
    Finds the total bounding box encompassing all images in the slice.
    """
    img_info = page.get_image_info(hashes=False, xrefs=True)
    slice_area = clip.width * clip.height
    image_coverage = 0
    combined_rect = None
    
    for img in img_info:
        img_rect = fitz.Rect(img["bbox"])
        intersect = clip & img_rect
        if not intersect.is_empty:
            image_coverage += intersect.width * intersect.height
            if combined_rect is None:
                combined_rect = intersect
            else:
                combined_rect |= intersect # This expands the box to include both
            
    ratio = image_coverage / (slice_area or 1)
    return ratio, combined_rect

# =========================================================================== #
# HARVESTING HELPERS                                                            #
# =========================================================================== #

def _rgb_to_hex(color):
    """Convert a (r, g, b) float tuple to an SVG hex color string."""
    if color is None:
        return "none"
    r, g, b = [max(0.0, min(1.0, c)) for c in color[:3]]
    return f"#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}"


def _path_items_to_svg_d(items):
    """Convert a PyMuPDF drawing's item list to an SVG path 'd' attribute."""
    parts = []
    prev_end = None
    for item in items:
        cmd = item[0]
        if cmd == "re":
            r = item[1]
            parts.append(
                f"M {r.x0:.2f} {r.y0:.2f} "
                f"h {r.width:.2f} v {r.height:.2f} h {-r.width:.2f} Z"
            )
            prev_end = None
        elif cmd == "l":
            p1, p2 = item[1], item[2]
            if prev_end is None or abs(p1.x - prev_end.x) > 0.5 or abs(p1.y - prev_end.y) > 0.5:
                parts.append(f"M {p1.x:.2f} {p1.y:.2f}")
            parts.append(f"L {p2.x:.2f} {p2.y:.2f}")
            prev_end = p2
        elif cmd == "c":
            p1, p2, p3, p4 = item[1], item[2], item[3], item[4]
            if prev_end is None or abs(p1.x - prev_end.x) > 0.5 or abs(p1.y - prev_end.y) > 0.5:
                parts.append(f"M {p1.x:.2f} {p1.y:.2f}")
            parts.append(f"C {p2.x:.2f} {p2.y:.2f} {p3.x:.2f} {p3.y:.2f} {p4.x:.2f} {p4.y:.2f}")
            prev_end = p4
        elif cmd == "qu":
            quad = item[1]
            pts  = [quad.ul, quad.ur, quad.lr, quad.ll]
            parts.append(
                f"M {pts[0].x:.2f} {pts[0].y:.2f} " +
                " ".join(f"L {p.x:.2f} {p.y:.2f}" for p in pts[1:]) + " Z"
            )
            prev_end = None
    return " ".join(parts)


def _group_to_svg(group, group_rect, padding=4.0):
    """Render a cluster of drawing dicts to a standalone, self-contained SVG string."""
    vx = group_rect.x0 - padding
    vy = group_rect.y0 - padding
    vw = group_rect.width  + 2 * padding
    vh = group_rect.height + 2 * padding
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="{vx:.2f} {vy:.2f} {vw:.2f} {vh:.2f}" '
        f'width="{vw:.2f}" height="{vh:.2f}">',
    ]
    for d in group:
        d_str = _path_items_to_svg_d(d.get("items", []))
        if not d_str:
            continue
        fill   = _rgb_to_hex(d.get("fill"))
        stroke = _rgb_to_hex(d.get("color"))
        sw     = d.get("width", 0)
        fo     = d.get("fill_opacity", 1.0)
        so     = d.get("stroke_opacity", 1.0)
        fr     = "evenodd" if d.get("even_odd") else "nonzero"
        attrs  = [f'd="{d_str}"', f'fill="{fill}"', f'fill-rule="{fr}"']
        if fill != "none" and fo < 1.0:
            attrs.append(f'fill-opacity="{fo:.3f}"')
        if stroke != "none" and sw > 0:
            attrs += [f'stroke="{stroke}"', f'stroke-width="{sw:.2f}"']
            if so < 1.0:
                attrs.append(f'stroke-opacity="{so:.3f}"')
        else:
            attrs.append('stroke="none"')
        lines.append(f'  <path {" ".join(attrs)}/>')
    lines.append("</svg>")
    return "\n".join(lines)


def _find_buttons(page, candidates, page_width):
    """
    Detect button elements: a small filled rect that contains at least one text span.
    Centre-point containment is used to avoid false positives near borders.

    Returns:
        buttons:         List of {paths, full_rect} dicts.
        button_path_ids: set of id() values for paths consumed by buttons.
    """
    spans = []
    try:
        for block in page.get_text("dict", flags=0)["blocks"]:
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    if span.get("text", "").strip():
                        spans.append(fitz.Rect(span["bbox"]))
    except Exception:
        return [], set()

    btn_shapes = [
        d for d in candidates
        if 8 < d["rect"].height < 80
        and 30 < d["rect"].width < page_width * 0.5
        and d.get("fill") is not None
    ]

    buttons, used_ids = [], set()
    for shape in btn_shapes:
        if id(shape) in used_ids:
            continue
        d_rect = shape["rect"]
        contained = [
            s for s in spans
            if d_rect.contains(fitz.Point((s.x0 + s.x1) / 2, (s.y0 + s.y1) / 2))
        ]
        if not contained:
            continue
        group_paths = []
        for d in candidates:
            if not (fitz.Rect(d["rect"]) & d_rect).is_empty:
                group_paths.append(d)
                used_ids.add(id(d))
        full_rect = fitz.Rect(d_rect)
        for s in contained:
            full_rect |= s
        buttons.append({"paths": group_paths, "full_rect": full_rect})
    return buttons, used_ids


def _button_to_svg(button, page, padding=4.0):
    """
    Render a button (vector shape + text) as an SVG file containing a
    base64-encoded PNG image.  Custom PDF fonts cannot be expressed as SVG
    <text> elements reliably, so a composited raster is used instead.
    """
    fr   = button["full_rect"]
    clip = fitz.Rect(fr.x0 - padding, fr.y0 - padding, fr.x1 + padding, fr.y1 + padding)
    pix  = page.get_pixmap(
        matrix=fitz.Matrix(2, 2), clip=clip,
        colorspace=fitz.csRGB, alpha=True,
    )
    b64 = base64.b64encode(pix.tobytes("png")).decode()
    vw, vh = clip.width, clip.height
    return "\n".join([
        '<?xml version="1.0" encoding="utf-8"?>',
        '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"',
        f'     viewBox="0 0 {vw:.2f} {vh:.2f}" width="{vw:.2f}" height="{vh:.2f}">',
        f'  <image x="0" y="0" width="{vw:.2f}" height="{vh:.2f}"',
        f'         xlink:href="data:image/png;base64,{b64}"/>',
        '</svg>',
    ])


def _cluster_drawings(drawings, proximity=15.0):
    """Group path drawings into spatial clusters via a single-pass sweep."""
    if not drawings:
        return []
    sorted_d = sorted(drawings, key=lambda d: (d["rect"].y0, d["rect"].x0))
    groups, current = [], [sorted_d[0]]
    current_union   = fitz.Rect(sorted_d[0]["rect"])
    for d in sorted_d[1:]:
        r        = d["rect"]
        expanded = fitz.Rect(
            current_union.x0 - proximity, current_union.y0 - proximity,
            current_union.x1 + proximity, current_union.y1 + proximity,
        )
        if not (r & expanded).is_empty:
            current.append(d)
            current_union |= r
        else:
            groups.append(current)
            current, current_union = [d], fitz.Rect(r)
    groups.append(current)
    return groups


def _hash_drawing_group(group, group_rect):
    """Position-normalized content hash for deduplicating vector clusters."""
    w = group_rect.width  or 1
    h = group_rect.height or 1
    parts = []
    for d in sorted(group, key=lambda x: (round(x["rect"].y0, 1), round(x["rect"].x0, 1))):
        for item in d.get("items", []):
            cmd = item[0]
            if cmd == "re":
                r = item[1]
                parts.append(
                    f"re:{(r.x0 - group_rect.x0)/w:.3f},"
                    f"{(r.y0 - group_rect.y0)/h:.3f},"
                    f"{r.width/w:.3f},{r.height/h:.3f}"
                )
            elif cmd in ("l", "c"):
                coords = ",".join(
                    f"{(getattr(pt, 'x', 0) - group_rect.x0)/w:.3f},"
                    f"{(getattr(pt, 'y', 0) - group_rect.y0)/h:.3f}"
                    for pt in item[1:]
                )
                parts.append(f"{cmd}:{coords}")
        if d.get("fill"):  parts.append(f"f:{d['fill']}")
        if d.get("color"): parts.append(f"s:{d['color']}")
    return hashlib.md5("|".join(parts).encode()).hexdigest()


# =========================================================================== #
# ASSET HARVESTING                                                              #
# =========================================================================== #

def harvest_assets(page, output_folder):
    """
    Phase 0 — Asset Harvesting.

    Extracts logos, icons, and transparent images as standalone files before
    slicing begins. Two passes are made:

      A. Raster — images that are transparent (have a soft mask / smask) or are
         small relative to the page (<15% area, i.e. likely a logo or icon).
         Transparent images are page-rendered so the smask is composited
         correctly into a proper alpha channel. Small opaque images are
         pulled directly from their PDF xref for maximum fidelity.

      B. Vector — spatially-clustered drawing paths that are neither full-width
         background bands nor hairlines. Each cluster is written as a standalone
         SVG, preserving fill colour, stroke, opacity, and fill-rule.

    Duplicates are suppressed via:
      - Raster: the per-image content digest from get_image_info(hashes=True).
      - Vector: a position-normalised MD5 of path coordinates + colours.

    Args:
        page:          A PyMuPDF Page object (page.parent gives the Document).
        output_folder: The root _Assets folder; a /Harvested sub-folder is
                       created automatically.
    """
    doc              = page.parent
    harvested_folder = os.path.join(output_folder, "Harvested")
    os.makedirs(harvested_folder, exist_ok=True)

    page_width  = page.rect.width
    page_height = page.rect.height
    page_area   = page_width * page_height

    seen_hashes   = set()
    raster_count  = 0
    button_count  = 0
    vector_count  = 0
    assets        = []

    print("  [Harvest] Scanning page for logos and icons...")

    # ----------------------------------------------------------------------- #
    # PHASE A: Raster images                                                   #
    # ----------------------------------------------------------------------- #
    for img in page.get_image_info(hashes=True, xrefs=True):
        xref = img.get("xref", 0)
        if not xref:
            continue

        # Deduplicate by content digest
        digest = img.get("digest", b"")
        if digest in seen_hashes:
            continue
        seen_hashes.add(digest)

        smask      = img.get("smask", 0)
        bbox       = fitz.Rect(img["bbox"])
        area_ratio = (bbox.width * bbox.height) / page_area
        has_alpha  = smask > 0

        # Harvest only: transparent images OR small images (logo / icon sized)
        if not (has_alpha or area_ratio < 0.15):
            continue

        raster_count += 1
        tag = "alpha" if has_alpha else f"{area_ratio:.0%} of page"

        if has_alpha:
            # Render the clip so the smask is composited into a real alpha channel
            pix   = page.get_pixmap(
                matrix=fitz.Matrix(2, 2), clip=bbox,
                colorspace=fitz.csRGB, alpha=True
            )
            fname = os.path.join(harvested_folder, f"logo_{raster_count:02d}.png")
            pix.save(fname)
        else:
            # Extract raw bytes from xref — lossless, original resolution
            try:
                raw   = doc.extract_image(xref)
                ext   = raw.get("ext", "png")
                fname = os.path.join(harvested_folder, f"logo_{raster_count:02d}.{ext}")
                with open(fname, "wb") as f:
                    f.write(raw["image"])
            except Exception:
                pix   = page.get_pixmap(
                    matrix=fitz.Matrix(2, 2), clip=bbox,
                    colorspace=fitz.csRGB, alpha=False
                )
                fname = os.path.join(harvested_folder, f"logo_{raster_count:02d}.png")
                pix.save(fname)

        assets.append({"filename": fname, "type": "raster", "bbox": [bbox.x0, bbox.y0, bbox.x1, bbox.y1]})
        print(f"  [Harvest] Raster:  {os.path.basename(fname)}  [{tag}]")

    # ----------------------------------------------------------------------- #
    # PHASE B: Vector elements — buttons first, then logos / icons            #
    # ----------------------------------------------------------------------- #
    drawings   = page.get_drawings()
    candidates = [
        d for d in drawings
        if d["rect"].width  < page_width * 0.8
        and d["rect"].width  > 2
        and d["rect"].height > 2
        and (d.get("fill") is not None or d.get("color") is not None)
    ]

    # B1: BUTTONS — filled shape enclosing a text span
    buttons, button_path_ids = _find_buttons(page, candidates, page_width)
    for btn in buttons:
        group_hash = _hash_drawing_group(btn["paths"], btn["full_rect"])
        if group_hash in seen_hashes:
            continue
        seen_hashes.add(group_hash)
        button_count += 1
        fname = os.path.join(harvested_folder, f"button_{button_count:02d}.svg")
        with open(fname, "w", encoding="utf-8") as f:
            f.write(_button_to_svg(btn, page))
        fr = btn["full_rect"]
        assets.append({"filename": fname, "type": "button", "bbox": [fr.x0, fr.y0, fr.x1, fr.y1]})
        print(f"  [Harvest] Button:  {os.path.basename(fname)}  [{int(fr.width)}\u00d7{int(fr.height)}pt]")

    # B2: LOGOS / ICONS — remaining paths, small enough to be non-structural
    logo_candidates = [d for d in candidates if id(d) not in button_path_ids]
    for group in _cluster_drawings(logo_candidates, proximity=15.0):
        group_rect = group[0]["rect"]
        for d in group[1:]:
            group_rect |= d["rect"]

        if (group_rect.width * group_rect.height) / page_area > 0.10:
            continue

        group_hash = _hash_drawing_group(group, group_rect)
        if group_hash in seen_hashes:
            continue
        seen_hashes.add(group_hash)
        vector_count += 1
        fname = os.path.join(harvested_folder, f"vector_{vector_count:02d}.svg")
        with open(fname, "w", encoding="utf-8") as f:
            f.write(_group_to_svg(group, group_rect))
        assets.append({"filename": fname, "type": "vector", "bbox": [group_rect.x0, group_rect.y0, group_rect.x1, group_rect.y1]})
        print(
            f"  [Harvest] Vector:  {os.path.basename(fname)}  "
            f"[{int(group_rect.width)}\u00d7{int(group_rect.height)}pt, {len(group)} path(s)]"
        )

    total = raster_count + button_count + vector_count
    print(f"  [Harvest] Done \u2014 {raster_count} raster + {button_count} button(s) + {vector_count} vector = {total} asset(s)")
    return assets


# =========================================================================== #
# MANIFEST GENERATION                                                           #
# =========================================================================== #

def generate_manifest(pdf_path, page, slices, harvested_assets, output_folder, target_width):
    """
    Generate a manifest.json blueprint for Maizzle / HTML email code generation.

    The manifest captures:
      - Metadata: source PDF name, target width, UTC timestamp.
      - Slices:   Each rendered slice with its y-coordinates, top-layer kind,
                  and all text lines it contains (grouped by font / size / colour).
      - Assets:   All files written to the /Harvested sub-folder, with their
                  original page bounding-box coordinates.

    Args:
        pdf_path:         Absolute path to the source PDF.
        page:             PyMuPDF Page object (page 0).
        slices:           List of {filename, y0, y1, top_layer_kind} dicts.
        harvested_assets: List of {filename, type, bbox} dicts from harvest_assets().
        output_folder:    The root _Assets directory (manifest.json is saved here).
        target_width:     Integer pixel width used when rendering slices.
    """

    def _color_to_hex(val):
        """Convert a PyMuPDF colour value to a CSS #rrggbb string."""
        if val is None:
            return None
        if isinstance(val, (tuple, list)):
            r, g, b = [max(0, min(255, int(c * 255))) for c in val[:3]]
            return f"#{r:02x}{g:02x}{b:02x}"
        if isinstance(val, int):
            return f"#{val & 0xFFFFFF:06x}"
        return None

    # ---------------------------------------------------------------------- #
    # Extract every non-empty text span from the page                         #
    # ---------------------------------------------------------------------- #
    all_spans = []
    try:
        blocks = page.get_text("dict", flags=0)["blocks"]
    except Exception:
        blocks = []

    for block in blocks:
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                raw = span.get("text", "").replace("\n", " ").strip()
                if not raw:
                    continue
                bb = span["bbox"]
                all_spans.append({
                    "text":  raw,
                    "font":  span.get("font", ""),
                    "size":  round(span.get("size", 0), 2),
                    "color": _color_to_hex(span.get("color")),
                    "bbox":  [round(v, 2) for v in bb],
                    "_cy":   (bb[1] + bb[3]) / 2.0,
                })

    # ---------------------------------------------------------------------- #
    # Map spans to slices and group into text lines                           #
    # ---------------------------------------------------------------------- #
    enriched_slices = []
    for sl in slices:
        y0, y1 = sl["y0"], sl["y1"]

        # Spans whose vertical centre falls within this slice
        slice_spans = sorted(
            [s for s in all_spans if y0 <= s["_cy"] < y1],
            key=lambda s: (s["_cy"], s["bbox"][0]),
        )

        # Group spans into logical lines (same font+size+colour, close y-centre)
        text_lines = []
        for span in slice_spans:
            tolerance = max(span["size"] * 0.10, 1.0)
            matched = False
            for ln in reversed(text_lines):
                ref = ln["_ref"]
                if (
                    ref["font"]  == span["font"]
                    and ref["size"]  == span["size"]
                    and ref["color"] == span["color"]
                    and abs(ref["_cy"] - span["_cy"]) <= tolerance
                ):
                    ln["text"] += " " + span["text"]
                    ln["bbox"] = [
                        min(ln["bbox"][0], span["bbox"][0]),
                        min(ln["bbox"][1], span["bbox"][1]),
                        max(ln["bbox"][2], span["bbox"][2]),
                        max(ln["bbox"][3], span["bbox"][3]),
                    ]
                    ln["spans"].append(span)
                    matched = True
                    break
            if not matched:
                text_lines.append({
                    "text":  span["text"],
                    "font":  span["font"],
                    "size":  span["size"],
                    "color": span["color"],
                    "bbox":  list(span["bbox"]),
                    "spans": [span],
                    "_ref":  span,
                })

        # Strip internal keys; omit "spans" sub-list when only one span
        clean_lines = []
        for ln in text_lines:
            entry = {
                "text":  ln["text"],
                "font":  ln["font"],
                "size":  ln["size"],
                "color": ln["color"],
                "bbox":  ln["bbox"],
            }
            if len(ln["spans"]) > 1:
                entry["spans"] = [
                    {k: v for k, v in s.items() if k != "_cy"}
                    for s in ln["spans"]
                ]
            clean_lines.append(entry)

        enriched_slices.append({
            "filename":       os.path.basename(sl["filename"]),
            "y0":             sl["y0"],
            "y1":             sl["y1"],
            "top_layer_kind": sl["top_layer_kind"],
            "text_lines":     clean_lines,
        })

    # ---------------------------------------------------------------------- #
    # Build asset registry                                                     #
    # ---------------------------------------------------------------------- #
    asset_registry = [
        {
            "filename": os.path.basename(a["filename"]),
            "type":     a["type"],
            "bbox":     [round(v, 2) for v in a["bbox"]],
        }
        for a in (harvested_assets or [])
    ]

    # ---------------------------------------------------------------------- #
    # Assemble and write manifest.json                                         #
    # ---------------------------------------------------------------------- #
    manifest = {
        "meta": {
            "source":       os.path.basename(pdf_path),
            "target_width": target_width,
            "generated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        "slices": enriched_slices,
        "assets": asset_registry,
    }

    out_path = os.path.join(output_folder, "manifest.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    total_lines = sum(len(sl["text_lines"]) for sl in enriched_slices)
    print(
        f"  [Manifest] Saved manifest.json "
        f"({len(enriched_slices)} slice(s), {total_lines} text line(s), "
        f"{len(asset_registry)} asset(s))"
    )


# =========================================================================== #
# SLICER                                                                        #
# =========================================================================== #

def run_slicer(pdf_path, target_width):
    abs_pdf_path = os.path.abspath(pdf_path)
    parent_dir = os.path.dirname(abs_pdf_path)
    prefix = get_abbreviated_prefix(abs_pdf_path)
    
    # Folder name includes width for clarity
    output_folder = os.path.join(parent_dir, f"{prefix}{OUTPUT_SUBFOLDER_SUFFIX}_{target_width}px")
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)

    try:
        doc = fitz.open(abs_pdf_path)
    except Exception as e:
        print(f"Error opening PDF: {e}")
        sys.exit(1)

    page = doc[0]
    width, height = page.rect.width, page.rect.height
    
    zoom = target_width / width
    matrix = fitz.Matrix(zoom, zoom)

    # 0. ASSET HARVESTING
    harvested_assets = harvest_assets(page, output_folder)

    # 1. GATHER ALL POTENTIAL CUT POINTS (Horizontal Y-coordinates)
    slices_data = []
    drawings = page.get_drawings()
    raw_cuts = [0, height]
    section_drawings = []  # wide filled rects that act as section separators
    for d in drawings:
        if d["fill"] and d["rect"].width > width * 0.8:
            section_drawings.append(d["rect"])
            raw_cuts.append(round(d["rect"].y0))
            raw_cuts.append(round(d["rect"].y1))

    images = page.get_image_info()
    img_boxes = [fitz.Rect(img["bbox"]) for img in images]
    for box in img_boxes:
        raw_cuts.append(round(box.y0))
        raw_cuts.append(round(box.y1))

    # 2. BUILD SUPPRESSION RULES (width-based priority + z-order awareness)
    # Wide images (≥80% width) are structural - only suppress if drawing truly overlays
    # Narrow images (<80% width) are content - apply all suppression rules
    suppress_y = set()
    width_threshold = width * 0.8

    # Classify images by width
    wide_images = [box for box in img_boxes if box.width >= width_threshold]
    narrow_images = [box for box in img_boxes if box.width < width_threshold]

    # WIDE IMAGES: Only suppress if drawing starts BEFORE image (structural overlay)
    for img_box in wide_images:
        for elem in section_drawings:
            # Suppress top if drawing starts before and extends into image (header case)
            if elem.y0 < img_box.y0 - 5 and elem.y1 > img_box.y0 + 5:
                suppress_y.add(round(img_box.y0))
            
            # Suppress bottom if drawing starts before and extends past (full background case)
            if elem.y0 < img_box.y0 - 5 and elem.y1 > img_box.y1 + 5:
                suppress_y.add(round(img_box.y1))

    # NARROW IMAGES: Apply all suppression rules (they're content, not section boundaries)
    for img_box in narrow_images:
        for elem in section_drawings:
            # Suppress if drawing overlays from above
            if elem.y0 < img_box.y0 - 5 and elem.y1 > img_box.y0 + 5:
                suppress_y.add(round(img_box.y0))
            
            # Suppress if drawing extends past bottom
            if elem.y0 < img_box.y0 - 5 and elem.y1 > img_box.y1 + 5:
                suppress_y.add(round(img_box.y1))

    # ALL IMAGES: Suppress drawings fully contained inside (decorative overlays)
    for img_box in img_boxes:
        for elem in section_drawings:
            if elem.y0 > img_box.y0 + 5 and elem.y1 < img_box.y1 - 5:
                suppress_y.add(round(elem.y0))
                suppress_y.add(round(elem.y1))

    # Merge only truly intersecting images (both vertical AND horizontal overlap)
    for i, img1 in enumerate(img_boxes):
        for j, img2 in enumerate(img_boxes):
            if i >= j:
                continue
            # Check both vertical and horizontal intersection
            v_overlap = img1.y0 < img2.y1 - 5 and img2.y0 < img1.y1 - 5
            h_overlap = img1.x0 < img2.x1 - 5 and img2.x0 < img1.x1 - 5
            
            # Only merge if they truly intersect in both dimensions
            if v_overlap and h_overlap:
                # Suppress the boundary between intersecting images
                if img1.y1 < img2.y1:
                    suppress_y.add(round(img1.y1))
                else:
                    suppress_y.add(round(img2.y1))

    # 3. REFINE THE CUTS - wide image boundaries are primary section delimiters
    # Build sets for quick lookup
    img_y_set = set()
    wide_img_y_set = set()
    for box in wide_images:
        wide_img_y_set.add(round(box.y0))
        wide_img_y_set.add(round(box.y1))
    
    for box in img_boxes:
        img_y_set.add(round(box.y0))
        img_y_set.add(round(box.y1))

    sorted_cuts = sorted(list(set(raw_cuts)))
    final_cuts = [0]
    for p in sorted_cuts:
        if 0 < p < height:
            # Skip any cuts explicitly suppressed by overlap/element rules
            if p in suppress_y:
                continue

            # Prioritize cuts based on type
            is_wide_img_boundary = p in wide_img_y_set
            is_narrow_img_boundary = (p in img_y_set) and (p not in wide_img_y_set)
            gap = p - final_cuts[-1]
            
            # Wide image boundaries: always add (unless suppressed above)
            if is_wide_img_boundary:
                if gap > 5:
                    final_cuts.append(p)
                elif gap > 0 and final_cuts[-1] not in img_y_set and final_cuts[-1] != 0:
                    # Replace nearby non-image cut with wide image boundary
                    final_cuts[-1] = p
            # Narrow image boundaries and drawings: require minimum gap
            elif gap > 40:
                final_cuts.append(p)

    if final_cuts[-1] < height: final_cuts.append(height)

    # 4. RENDER SLICES
    for i in range(len(final_cuts) - 1):
        y0, y1 = final_cuts[i], final_cuts[i+1]
        
        # Start with full width
        full_width_clip = fitz.Rect(0, y0, width, y1)
        coverage, combined_rect = get_combined_image_bounds(page, full_width_clip)
        
        # Determine format first
        ext = ".jpg" if coverage > 0.7 else ".png"
        
        # INITIALIZE RENDERING CLIP
        render_clip = full_width_clip

        # --- REFINED SHRINK-WRAP LOGIC ---
        # 1. Only shrink-wrap if it's a JPG (High image coverage)
        # 2. Only if the images combined take up > 50% of the slice width
        if ext == ".jpg" and combined_rect and combined_rect.width > (width * 0.5):
            # Apply Inset to the combined box of all images in this row
            render_clip = fitz.Rect(
                combined_rect.x0 + EDGE_INSET, 
                y0 + EDGE_INSET, 
                combined_rect.x1 - EDGE_INSET, 
                y1 - EDGE_INSET
            )

        # RENDER
        # Use alpha=False only for JPGs to keep borders clean
        # Keep alpha=True for PNGs to preserve text/logo transparency
        use_alpha = True if ext == ".png" else False
        
        pix = page.get_pixmap(matrix=matrix, clip=render_clip, colorspace=fitz.csRGB, alpha=use_alpha)
        
        filename = os.path.join(output_folder, f"{prefix}_slice_{i+1:02d}{ext}")
        if ext == ".jpg":
            pix.save(filename, "jpg", jpg_quality=95)
        else:
            pix.save(filename)

        mid_y = (y0 + y1) / 2.0
        top_layer_kind = get_top_layer_kind_at_y(page, width, mid_y) or (
            "image" if ext == ".jpg" else "text"
        )
        slices_data.append({
            "filename":       filename,
            "y0":             y0,
            "y1":             y1,
            "top_layer_kind": top_layer_kind,
        })
        print(f" - Saved {os.path.basename(filename)} (Width: {pix.width}px)")

    generate_manifest(abs_pdf_path, page, slices_data, harvested_assets, output_folder, target_width)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(1)
        
    path = sys.argv[1]
    
    # Check for second argument (width) from Shortcut
    # Shortcut passes $1 (path) and $2 (width)
    active_width = DEFAULT_WIDTH
    if len(sys.argv) > 2:
        try:
            active_width = int(sys.argv[2])
        except ValueError:
            pass

    if os.path.exists(path):
        run_slicer(path, active_width)
