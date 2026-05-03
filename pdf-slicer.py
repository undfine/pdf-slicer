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

    # 2. REFINE THE CUTS - image boundaries take priority over drawing cuts
    img_y_set = set()
    for box in img_boxes:
        img_y_set.add(round(box.y0))
        img_y_set.add(round(box.y1))

    sorted_cuts = sorted(list(set(raw_cuts)))
    final_cuts = [0]
    for p in sorted_cuts:
        if 0 < p < height:
            is_inside_image = any(box.y0 + 5 < p < box.y1 - 5 for box in img_boxes)
            if is_inside_image:
                continue

            is_img_boundary = p in img_y_set
            gap = p - final_cuts[-1]

            if is_img_boundary:
                if gap > 5:
                    # If a nearby non-image cut precedes this, replace it so image wins
                    if gap < 40 and final_cuts[-1] not in img_y_set and final_cuts[-1] != 0:
                        final_cuts[-1] = p
                    else:
                        final_cuts.append(p)
            elif gap > 40:
                final_cuts.append(p)

    if final_cuts[-1] < height: final_cuts.append(height)

    # 3. RENDER SLICES
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
