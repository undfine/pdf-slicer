# PDF Slicer

A smart PDF slicing tool that automatically segments single-page PDFs (particularly email PDFs) into multiple image slices based on visual content structure.

## Features

- **Automatic Slice Detection**: Analyzes background color blocks and drawings to intelligently determine where to cut the PDF
- **Smart Format Selection**: Automatically chooses JPG for photo-heavy regions and PNG for text/graphics
- **High-Quality Output**: Renders slices at 2x resolution for crisp, clear images
- **Organized Output**: Creates a dedicated assets folder next to the source PDF with descriptive filenames
- **Asset Harvesting**: Extracts logos, icons, vector graphics, and headlines as standalone files
- **Manifest Generation**: Produces a `manifest.json` blueprint with paragraph text, font metadata, and asset cross-references for email template generation
- **Minimal Configuration**: Just point it at a PDF and let it work

## Requirements

- Python 3.x
- PyMuPDF (fitz)
- Pillow (PIL) — for headline transparency

## Installation

Install the required dependencies:

```bash
pip install PyMuPDF Pillow
```

## Usage

```bash
python3 pdf_slicer.py path/to/your/file.pdf
```

### Example

```bash
python3 pdf_slicer.py "Marketing-Email-2024.pdf"
```

This will create a folder called `Marketing_Assets` in the same directory as the PDF, containing numbered slices:
- `Marketing-slice_01.png`
- `Marketing-slice_02.jpg`
- `Marketing-slice_03.png`
- etc.

### Reviewing slice boundaries before export

By default the detected cut points are exported immediately. To review and adjust them first, use `--review`:

```bash
python3 pdf_slicer.py path/to/your/file.pdf --review
```

This opens a browser tab with the detected regions overlaid on the rendered page. Drag boundaries, split/merge/add/delete regions, then click **Export** — the real harvest/crop/manifest pipeline runs immediately and slices are written next to the source PDF, same as a normal run. Nothing touches disk until Export is clicked; in-progress edits persist to the browser's `localStorage` (keyed by PDF path) so a reload won't lose them. This is the recommended entry point for a Shortcut/CLI invocation, since it's a single blocking command.

Two lower-level flags support reviewing detection output from a separate session:

```bash
python3 pdf_slicer.py path/to/your/file.pdf --detect-only   # write regions.json + preview, no slices
python3 review_server.py path/to/your/file_Assets_1200px     # open a browser to edit that regions.json
```

```bash
python3 pdf_slicer.py path/to/your/file.pdf --from-regions path/to/your/file_Assets_1200px/regions.json
```

`--from-regions` exports using a previously reviewed regions file instead of re-running detection. `review_server.py` requires no extra dependencies (Python stdlib only, no Flask/FastAPI).

## How It Works

### Phase 0 — Asset Harvesting
Before slicing begins, the page is scanned for extractable assets saved to a `/Harvested` subfolder:

- **Raster images**: All embedded images are extracted regardless of size.
  - *Transparent* images (with a soft mask) are page-rendered with the alpha channel composited → `graphic_NN.png`.
  - *Large opaque* images (≥15% of page area, e.g. hero photos) are extracted directly from their PDF xref at native resolution → `photo_NN.{ext}`.
  - *Small opaque* images (logos, icons) are also extracted via xref → `img_NN.{ext}`.
- **Vector clusters**: Drawing paths are grouped using a **union-find spatial clustering** algorithm (proximity = 20pt). Each cluster is exported as a standalone SVG with fill colour, stroke, opacity, and fill-rule preserved, then converted to a companion PNG. Clusters smaller than 8×8pt (isolated decorative marks) are skipped.
- **Button elements**: Filled rectangles that enclose a text span are detected as buttons and rendered as SVGs containing a composited PNG, preserving both the shape and custom font.
- **Headlines**: Letter-tracked display type (detected via per-character spacing analysis) is grouped into headline clusters and exported as transparent PNGs. Groups whose bounding boxes overlap are merged into a single image so no headline captures text from another.

All assets are deduplicated: raster images via PyMuPDF content digest, vector clusters via a position-normalised MD5 hash.

### Phase 1 — Cut Point Discovery
Candidate horizontal cut points are collected from:
- Wide filled rectangles (≥80% page width) used as section backgrounds
- Image bounding box tops and bottoms

### Phase 2 — Suppression Rules
Candidate cuts are filtered to avoid splitting visual units:
- **Wide images near section tops**: Only suppress a photo's top edge if the photo starts within `MIN_SLICE_HEIGHT` of the section background's own top (header images). Photos embedded deeper are kept as independent cut points.
- **Wide image bottom edges**: A photo's bottom edge is only suppressed when the containing section drawing also ends within `MIN_SLICE_HEIGHT` of the photo's bottom — meaning the photo genuinely fills the section. When the drawing extends well past the photo, the photo's y1 is preserved as a cut so text sections are not merged with the photo above.
- **Section edges landing inside photos**: A section drawing's bottom edge is suppressed when it falls inside a photo's vertical span, preventing a phantom thin slice between the drawing boundary and the photo's true bottom.
- **Image-to-image seams (true overlays only)**: A cut between two wide images is suppressed only when a second image genuinely *spans across* that y-coordinate (i.e. started before it and continues past it). Two images that are merely adjacent — one ending at y, the next starting at y — are **not** suppressed, so section boundaries between a photo and a coloured JPEG background are kept.
- **Drawings contained by images**: Decorative vector overlays fully inside a photo suppress both their top and bottom edges.

### Phase 3 — Post-processing
After initial cuts are established, two refinement passes run:

1. **Straddling-paragraph detection**: For each cut that aligns with a wide-image boundary, the page is scanned for body text paragraphs (reconstructed by merging consecutive line blocks within 10pt) whose top starts just above the cut (within 80pt) and whose bottom extends meaningfully below it. When found, the cut is **moved down** to the paragraph's bottom rather than adding a new cut — keeping the paragraph intact in the preceding slice and starting the photo slice cleanly.

2. **Minimum slice height**: Any slice shorter than `MIN_SLICE_HEIGHT` (120pt) is merged into its neighbour.

### Phase 4 — Rendering
Each slice is rendered at the target width. Format is chosen by image coverage ratio:
- **≥70% image coverage → JPG** (95% quality), shrink-wrapped horizontally to the combined image bounds with a 2pt inset to remove border artefacts.
- **<70% coverage → PNG** with alpha channel preserved.

### Phase 5 — Manifest Generation
`manifest.json` is written alongside the slices containing:
- Per-slice `alt_text` (all text in reading order) and `paragraphs` (body text grouped by font/size/colour with bounding boxes)
- `assets` registry: every harvested raster, vector, button, and headline with its bbox, font metadata, colours, and companion PNG reference

## Output Format

- **Slice folder**: `{prefix}_Assets_{width}px/`
- **Slice files**: `{prefix}_slice_{number}.{ext}` — `.jpg` for photo-heavy content, `.png` for text/graphics
- **Harvested assets**: `{prefix}_Assets_{width}px/Harvested/`
  - `photo_NN.{ext}` — large content photos (≥15% of page area), extracted at native resolution
  - `img_NN.{ext}` — small/logo raster images
  - `graphic_NN.png` — transparent images (smask composited)
  - `vector_NN.svg` + `vector_NN.png` — vector clusters
  - `button-{slug}.svg` + `button-{slug}.png` — button elements
  - `headline-{slug}.png` — transparent headline PNGs
- **Manifest**: `{prefix}_Assets_{width}px/manifest.json`
- **Preview**: `{source}.jpg` — full-page preview at target width

## Limitations

- Currently optimized for single-page PDFs
- Designed primarily for email newsletters and marketing materials with distinct visual sections

## Changelog

### v8.2
- **Interactive region review**: New `--review` flag opens a browser-based editor (`region-editor-prototype.html`, served by the new `review_server.py`) showing detected slice boundaries over the rendered page. Boundaries can be dragged, split, merged, added, or deleted before export. Regions and the preview image are served straight from memory — nothing is written to disk until **Export** is clicked, which runs the real harvest/crop/manifest pipeline in-process and writes slices next to the source PDF, matching the existing Shortcut-based workflow.
- Added `--detect-only` (writes `regions.json` + a full-page preview without exporting) and `--from-regions <path>` (exports from a previously saved/reviewed regions file) for reviewing detection output outside the single blocking `--review` command.
- **Renamed** `pdf-slicer.py` → `pdf_slicer.py` so it can be `import`-ed directly as a normal Python module (hyphenated filenames aren't valid module names).

### v8.1
- **Full-photo harvesting**: Removed the `area_ratio < 0.15` gate from Phase A raster harvesting. All embedded images are now extracted to `Harvested/` regardless of size. Large photos (≥15% page area) are saved as `photo_NN.{ext}` at native resolution; small logos/icons continue as `img_NN.{ext}`; transparent images as `graphic_NN.png`.
- **Image-to-image seam detection fix**: `has_image_to_image_continuity_at_y()` now requires one image to genuinely *span across* the candidate y-coordinate (started before it, extends past it) rather than merely start at it. This prevents section boundaries between a hero photo and an adjacent JPEG background from being incorrectly suppressed, which was causing mixed image-and-text slices.
- **Wide-image y1 suppression guard**: A photo's bottom edge is now only suppressed when the containing section drawing ends within `MIN_SLICE_HEIGHT` (120pt) of the photo's own bottom. Previously, any drawing that fully contained a photo (even extending hundreds of points past it) would suppress the photo's y1, merging the photo slice with the text section below.

### v8.0
- **Vector clustering**: Replaced single-pass sweep algorithm with union-find for correct grouping of chained-proximity elements (e.g. letter-spaced display type rendered as separate paths). Added 8×8pt minimum size filter to skip isolated decorative marks.
- **Headline transparency**: Headlines now render with `alpha=True` and PIL colour-keying removes the section background colour, producing genuinely transparent PNGs. Falls back gracefully if Pillow is not installed.
- **Headline overlap prevention**: Adjacent headline groups whose render clips intersect are merged into a single image before export, preventing one headline's PNG from capturing text from another.
- **Improved suppress rules**: Section drawing boundaries are now only suppressed at photo top edges when the photo begins within `MIN_SLICE_HEIGHT` of the section top (header images). Photos sitting deeper in a section are preserved as valid cut points. Section drawing bottom edges that fall inside a photo's vertical span are also suppressed.
- **Straddling-paragraph detection**: A new post-processing pass identifies body paragraphs that start above a wide-image cut boundary and end below it. The cut is moved to the paragraph's bottom rather than split the text — keeping paragraphs intact and photo slices clean. Uses paragraph-proxy merging (line blocks within 10pt gap) and proximity matching (80pt tolerance, 80pt minimum extension below cut).
- **Pillow** added as a dependency.

### v7.0
- Added Phase 0: `harvest_assets(page, output_folder)` runs before slicing begins
- Raster harvesting: extracts transparent images (smask → composited alpha PNG) and small images (<15% page area, extracted via xref for lossless quality)
- Vector harvesting: clusters isolated drawing paths into logo groups and exports each as a standalone SVG (fill, stroke, opacity, fill-rule preserved)
- Deduplication: raster assets use PyMuPDF content digest; vector clusters use a position-normalized MD5 hash — same logo appearing multiple times is saved only once
- All harvested assets saved to `{prefix}_Assets_{width}px/Harvested/` subfolder
- Added five private helpers: `_rgb_to_hex`, `_path_items_to_svg_d`, `_group_to_svg`, `_cluster_drawings`, `_hash_drawing_group`

### v6.0
- Added `has_image_to_image_continuity_at_y()` to detect image composites and overlays
- Implemented width-based image classification: wide images (≥80%) vs narrow images (<80%)
- Sophisticated suppression rules based on image width and z-order relationships
- Smart handling of intersecting images to avoid cutting through composites
- Wide image boundaries now prioritized as primary section delimiters
- Enhanced structural vs content image differentiation for better slice accuracy

### v5.2
- Added z-order detection using `page.get_bboxlog()` for advanced paint order analysis
- New `get_top_layer_kind_at_y()` function to determine which element type is on top at specific Y coordinates
- Enhanced cut refinement with z-order awareness: respects when drawings overlay images
- Fallback logic for when paint-order data is unavailable
- Better handling of overlapping elements based on actual rendering order

### v5.1
- Refined cut prioritization: image boundaries now take precedence over drawing boundaries
- Smart boundary replacement: image cuts can replace nearby non-image cuts within 40px
- Improved handling of close-proximity cuts for cleaner slice boundaries
- Better edge case handling with minimum 5px gap requirement

### v5.0
- Added `get_combined_image_bounds()` to handle multiple images in a single slice
- Refined shrink-wrap logic: only applies to JPG slices (high image coverage), not PNGs
- Format-specific alpha channel handling: alpha=True for PNGs (preserves transparency), alpha=False for JPGs
- Improved multi-image handling by combining bounding boxes

### v4.0
- Added edge inset feature (EDGE_INSET = 2.0pt) to remove borders from extracted images
- Enhanced shrink-wrap logic: now applies inset to all four sides of cropped images
- Improved rendering with alpha=False for cleaner edges (flattens transparency to white)
- Better error handling with proper exit codes

### v3.0
- Added configurable output width (accepts optional second argument, defaults to 1200px)
- Implemented smart shrink-wrap logic: crops horizontally to image bounds when images occupy >50% slice width
- Updated folder naming to include width specification (e.g., `prefix_Assets_1200px`)
- Refactored code with configuration constants (DEFAULT_WIDTH, OUTPUT_SUBFOLDER_SUFFIX)
- Changed output logging to display width instead of height
- Removed manual error messages in favor of cleaner exit codes

### v2.0
- Added image boundary detection alongside color block analysis
- Improved cut refinement with "sliver detection" (reduced threshold from 100px to 40px)
- New safety check to prevent cutting inside images
- Enhanced output logging with slice height information
- Changed filename format from `prefix-slice` to `prefix_slice` (underscore separator)

### v1.0 (Initial Release)
- Basic PDF slicing functionality
- Automatic detection of color blocks for section boundaries
- Smart format selection (JPG vs PNG) based on image coverage
- 2x resolution rendering for high-quality output
- Automatic prefix extraction from filename
- Minimum slice height filtering (100px) to avoid tiny fragments

## License

Open source - feel free to modify and adapt for your needs.
