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
    # 1. Resolve the path relative to the PDF file location
    abs_pdf_path = os.path.abspath(pdf_path)
    parent_dir = os.path.dirname(abs_pdf_path)
    prefix = get_abbreviated_prefix(abs_pdf_path)
    
    # 2. Create the output folder sitting right next to the PDF
    output_folder = os.path.join(parent_dir, f"{prefix}_Assets")
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)

    # 3. Process the PDF
    try:
        doc = fitz.open(abs_pdf_path)
    except Exception as e:
        print(f"Error opening PDF: {e}")
        return

    page = doc[0]  # Assuming single-page email PDF
    width, height = page.rect.width, page.rect.height

    # Logic to find background color-block transitions for cutting
    drawings = page.get_drawings()
    cut_points = [0, height]
    
    for d in drawings:
        if d["fill"] and d["rect"].width > width * 0.8:
            cut_points.append(d["rect"].y0)
            cut_points.append(d["rect"].y1)
    
    # Sort and remove duplicates/tiny slices
    cut_points = sorted(list(set(cut_points)))
    final_cuts = [0]
    for p in cut_points:
        if 0 < p < height and p - final_cuts[-1] > 100:
            final_cuts.append(p)
    if final_cuts[-1] < height:
        final_cuts.append(height)

    print(f"--- Slicing: {os.path.basename(abs_pdf_path)} ---")
    print(f"--- Target Folder: {output_folder} ---")

    for i in range(len(final_cuts) - 1):
        clip = fitz.Rect(0, final_cuts[i], width, final_cuts[i+1])
        ext = get_optimal_extension(page, clip)
        
        # Render at 2x resolution
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=clip)
        
        filename = os.path.join(output_folder, f"{prefix}-slice_{i+1:02d}{ext}")
        
        if ext == ".jpg":
            pix.save(filename, "jpg", jpg_quality=95)
        else:
            pix.save(filename)
            
        print(f"Saved: {os.path.basename(filename)}")

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
