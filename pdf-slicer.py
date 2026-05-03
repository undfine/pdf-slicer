import fitz  # PyMuPDF
import os
import re
import sys

# --- CONFIGURATION ---
DEFAULT_WIDTH = 1200  
OUTPUT_SUBFOLDER_SUFFIX = "_Assets"

# The number of original PDF points to shave off image edges to avoid borders
EDGE_INSET = 2.0  

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

    # 1. GATHER ALL POTENTIAL CUT POINTS (Horizontal Y-coordinates)
    drawings = page.get_drawings()
    raw_cuts = [0, height]
    for d in drawings:
        if d["fill"] and d["rect"].width > width * 0.8:
            raw_cuts.append(round(d["rect"].y0))
            raw_cuts.append(round(d["rect"].y1))

    images = page.get_image_info()
    img_boxes = [fitz.Rect(img["bbox"]) for img in images]
    for box in img_boxes:
        raw_cuts.append(round(box.y0))
        raw_cuts.append(round(box.y1))

    # 2. REFINE THE CUTS
    sorted_cuts = sorted(list(set(raw_cuts)))
    final_cuts = [0]
    for p in sorted_cuts:
        if 0 < p < height and p - final_cuts[-1] > 40:
            is_inside_another_image = False
            for box in img_boxes:
                if box.y0 + 5 < p < box.y1 - 5:
                    is_inside_another_image = True; break
            if not is_inside_another_image:
                final_cuts.append(p)
    if final_cuts[-1] < height: final_cuts.append(height)

    # 3. RENDER SLICES
    for i in range(len(final_cuts) - 1):
        y0, y1 = final_cuts[i], final_cuts[i+1]
        
        # Start with full width
        clip = fitz.Rect(0, y0, width, y1)
        
        coverage, dom_rect = get_slice_info(page, clip)
        
        # --- SHRINK-WRAP LOGIC ---
        # If an image takes up > 50% of the slice width, crop to its X bounds
        if dom_rect and dom_rect.width > (width * 0.5):
            # NEW: Apply the negative margin (inset) to all four sides
            # This shaves off 2pt from top, bottom, left, and right.
            inset_x0 = dom_rect.x0 + EDGE_INSET
            inset_y0 = y0 + EDGE_INSET
            inset_x1 = dom_rect.x1 - EDGE_INSET
            inset_y1 = y1 - EDGE_INSET
            
            # Re-create the clip with the tighter bounds
            clip = fitz.Rect(inset_x0, inset_y0, inset_x1, inset_y1)

        ext = ".jpg" if coverage > 0.7 else ".png"
        
        # IMPORTANT: Use alpha=False for cleaner edges (flatten to white)
        pix = page.get_pixmap(matrix=matrix, clip=clip, colorspace=fitz.csRGB, alpha=False)
        
        filename = os.path.join(output_folder, f"{prefix}_slice_{i+1:02d}{ext}")
        if ext == ".jpg":
            pix.save(filename, "jpg", jpg_quality=95)
        else:
            pix.save(filename)
            
        print(f" - Saved {os.path.basename(filename)} (Width: {pix.width}px)")

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
