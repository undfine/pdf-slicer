import fitz  # PyMuPDF
import os
import re
import sys

def get_abbreviated_prefix(filepath):
    """
    Extracts the first part of the filename for the prefix.
    """
    base_name = os.path.splitext(os.path.basename(filepath))[0]
    # Split by the first space or dash
    parts = re.split(r'[\s-]', base_name)
    return parts[0] if parts else "Email"

def get_optimal_extension(page, clip):
    """
    Analyzes a region to see if it's primarily an image or text/graphics.
    Returns '.jpg' for photos and '.png' for text/graphics.
    """
    img_info = page.get_image_info(hashes=False, xrefs=True)
    slice_area = clip.width * clip.height
    image_coverage = 0
    
    for img in img_info:
        img_rect = fitz.Rect(img["bbox"])
        intersect = clip & img_rect
        if not intersect.is_empty:
            image_coverage += intersect.width * intersect.height
            
    # Heuristic: If > 70% of the slice is a bitmap image, use JPG.
    return ".jpg" if (image_coverage / (slice_area or 1)) > 0.7 else ".png"

def run_slicer(pdf_path):
    abs_pdf_path = os.path.abspath(pdf_path)
    parent_dir = os.path.dirname(abs_pdf_path)
    prefix = get_abbreviated_prefix(abs_pdf_path)
    output_folder = os.path.join(parent_dir, f"{prefix}_Assets")
    
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)

    try:
        doc = fitz.open(abs_pdf_path)
    except Exception as e:
        print(f"Error: {e}"); return

    page = doc[0]
    width, height = page.rect.width, page.rect.height

    # 1. GATHER ALL POTENTIAL CUT POINTS
    raw_cuts = [0, height]

    # A: Background Color Block boundaries
    drawings = page.get_drawings()
    for d in drawings:
        if d["fill"] and d["rect"].width > width * 0.8:
            raw_cuts.append(round(d["rect"].y0))
            raw_cuts.append(round(d["rect"].y1))

    # B: Image boundaries (This forces the "Text | Image | Text" split)
    images = page.get_image_info()
    img_boxes = [fitz.Rect(img["bbox"]) for img in images]
    for box in img_boxes:
        raw_cuts.append(round(box.y0))
        raw_cuts.append(round(box.y1))

    # 2. REFINE THE CUTS
    # Sort and remove duplicates
    sorted_cuts = sorted(list(set(raw_cuts)))
    
    final_cuts = [0]
    for p in sorted_cuts:
        if p <= 0 or p >= height: continue
        
        # Check for "Slivers": Only cut if the section is at least 40px tall.
        # (Emails often have tiny 1-5px shifts; this ignores them)
        if p - final_cuts[-1] > 40:
            # SAFETY CHECK: If this cut point (from a background rect) 
            # lands INSIDE another image, don't cut there.
            is_inside_another_image = False
            for box in img_boxes:
                if box.y0 + 5 < p < box.y1 - 5:
                    is_inside_another_image = True
                    break
            
            if not is_inside_another_image:
                final_cuts.append(p)

    if final_cuts[-1] < height:
        final_cuts.append(height)

    print(f"--- Slicing: {os.path.basename(abs_pdf_path)} ---")

    for i in range(len(final_cuts) - 1):
        y0, y1 = final_cuts[i], final_cuts[i+1]
        clip = fitz.Rect(0, y0, width, y1)
        
        ext = get_optimal_extension(page, clip)
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=clip)
        
        filename = os.path.join(output_folder, f"{prefix}_slice_{i+1:02d}{ext}")
        
        if ext == ".jpg":
            pix.save(filename, "jpg", jpg_quality=95)
        else:
            pix.save(filename)
        print(f" - Saved {os.path.basename(filename)} (Height: {int(y1-y0)}px)")

if __name__ == "__main__":
    # --- STRICT GUARD CLAUSE ---
    # 1. Exit if no argument provided
    if len(sys.argv) < 2:
        print("Usage: python3 smart_slicer.py [path_to_pdf]")
        sys.exit(1)
        
    target_pdf = sys.argv[1]
    
    # 2. Exit if filename is empty string or file doesn't exist
    if not target_pdf or not os.path.exists(target_pdf):
        print(f"Error: File not found at '{target_pdf}'")
        sys.exit(1)

    run_slicer(target_pdf)
