# PDF Slicer

A smart PDF slicing tool that automatically segments single-page PDFs (particularly email PDFs) into multiple image slices based on visual content structure.

## Features

- **Automatic Slice Detection**: Analyzes background color blocks and drawings to intelligently determine where to cut the PDF
- **Smart Format Selection**: Automatically chooses JPG for photo-heavy regions and PNG for text/graphics
- **High-Quality Output**: Renders slices at 2x resolution for crisp, clear images
- **Organized Output**: Creates a dedicated assets folder next to the source PDF with descriptive filenames
- **Minimal Configuration**: Just point it at a PDF and let it work

## Requirements

- Python 3.x
- PyMuPDF (fitz)

## Installation

Install the required dependency:

```bash
pip install PyMuPDF
```

## Usage

```bash
python3 pdf-slicer.py path/to/your/file.pdf
```

### Example

```bash
python3 pdf-slicer.py "Marketing-Email-2024.pdf"
```

This will create a folder called `Marketing_Assets` in the same directory as the PDF, containing numbered slices:
- `Marketing-slice_01.png`
- `Marketing-slice_02.jpg`
- `Marketing-slice_03.png`
- etc.

## How It Works

1. **Prefix Extraction**: Extracts the first part of the filename (before space or dash) to use as a prefix for output files
2. **Color Block Analysis**: Scans the PDF for background color blocks that span more than 80% of the page width
3. **Cut Point Determination**: Uses these color blocks to determine natural breaking points, filtering out cuts that would create tiny slices (< 100 units)
4. **Content Analysis**: For each slice, analyzes whether it's primarily an image (>70% bitmap coverage) or text/graphics
5. **High-Quality Rendering**: Renders each slice at 2x resolution with appropriate format (JPG at 95% quality for images, PNG for text)

## Output Format

- **Folder naming**: `{prefix}_Assets/`
- **File naming**: `{prefix}-slice_{number}.{ext}`
- **Image formats**: `.jpg` for photo-heavy content, `.png` for text/graphics

## Limitations

- Currently optimized for single-page PDFs
- Designed primarily for email newsletters and marketing materials with distinct visual sections

## Error Handling

The script includes robust error handling:
- Validates that a file path is provided
- Checks that the file exists before processing
- Gracefully handles PDF opening errors

## Changelog

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
