"""
pdf_markup.py — Applies AI-generated QC markups to the PDF.
bbox format: bbox_pdf_pct [x0%, y0%, x1%, y1%] — percentage of page, top-left origin.
"""
import json
import fitz  # PyMuPDF
import os


def add_callout(page, rect, text, color):
    """Draw a text callout box near the flagged rectangle."""
    pw = page.rect.width
    ph = page.rect.height
    x = min(rect.x1 + 6, pw - 260)
    y = min(rect.y1 + 6, ph - 50)
    box = fitz.Rect(x, y, x + 240, y + 45)
    page.draw_rect(box, color=(0, 0, 0), fill=(1, 1, 1), width=0.6)
    page.insert_textbox(box, text, fontsize=7, color=color)


def apply_markups(pdf_path: str, markup_plan: any, output_path: str):
    """
    Applies the markup_plan to the PDF.
    markup_plan can be a path to a JSON file OR a direct list of dictionaries.
    """
    if not os.path.exists(pdf_path):
        print(f"Error: Input PDF '{pdf_path}' not found.")
        return

    # If markup_plan is a string, assume it's a path and load it
    if isinstance(markup_plan, str):
        if not os.path.exists(markup_plan):
            print(f"Error: Markup plan file '{markup_plan}' not found.")
            return
        with open(markup_plan, 'r', encoding='utf-8') as f:
            try:
                data = json.load(f)
                if isinstance(data, dict) and "markup_plan" in data:
                    markup_plan = data["markup_plan"]
                elif isinstance(data, list):
                    markup_plan = data
            except json.JSONDecodeError as e:
                print(f"Error decoding markup plan JSON: {e}")
                return
    
    # Ensure we have a list now
    if not isinstance(markup_plan, list):
        print("Error: markup_plan must be a list or a path to a JSON list.")
        return

    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        print(f"Error opening PDF: {e}")
        return

    applied = 0
    skipped = 0

    for m in markup_plan:
        finding_id  = m.get("id") or m.get("finding_id", "?") # Prefer F-001 style IDs
        page_index  = m.get("page_index", 0)
        result      = m.get("result", "")
        note_text   = m.get("note_text", "")
        bbox_pct    = m.get("bbox_pct") # Using the refined bbox_pct from main.py

        if page_index < 0 or page_index >= len(doc):
            print(f"Warning [{finding_id}]: Page index {page_index} out of bounds.")
            continue

        page = doc[page_index]
        pw   = page.rect.width
        ph   = page.rect.height

        # Color by result (Matched to UI)
        if result == "FAIL":
            color = (0.93, 0.26, 0.26) # #ef4444 (approx)
        elif result == "REVIEW REQUIRED":
            color = (0.97, 0.45, 0.08) # #f97316 (approx)
        else:
            color = (0.23, 0.51, 0.96) # #3b82f6 (approx)

        if not bbox_pct:
            continue

        try:
            x0p, y0p, x1p, y1p = [max(0.0, min(100.0, float(v))) for v in bbox_pct]

            # Convert percentages to PDF points (Already calibrated by main.py Sniper)
            x0 = (x0p / 100.0) * pw
            y0 = (y0p / 100.0) * ph
            x1 = (x1p / 100.0) * pw
            y1 = (y1p / 100.0) * ph

            rect = fitz.Rect(x0, y0, x1, y1)
            if rect.is_empty or rect.is_infinite: continue

            # Draw rectangle outline
            page.draw_rect(rect, color=color, width=2.0)

            # Add ID Label (Like the UI red tag)
            # Safety check: if box is at the very top, put label inside/below
            label_y0 = y0 - 15 if y0 > 20 else y0 + 5
            label_rect = fitz.Rect(x0, label_y0, x0 + 40, label_y0 + 15)
            
            page.draw_rect(label_rect, color=color, fill=color, width=0)
            page.insert_textbox(label_rect, finding_id, fontsize=8, color=(1, 1, 1), align=1)

            applied += 1

        except Exception as e:
            print(f"Error [{finding_id}]: {e}")
            skipped += 1

    doc.save(output_path)
    doc.close()
    print(f"\nDone: {applied} markups applied. Saved to: {output_path}")


def stitch_images_to_pdf(image_paths: list[str], output_path: str):
    """
    Creates a new PDF by stitching together high-res PNG images.
    This guarantees that what the user saw in the visualizer is what they get in the PDF.
    """
    if not image_paths:
        print("Error: No images provided for stitching.")
        return

    try:
        doc = fitz.open()
        for img_path in image_paths:
            if not os.path.exists(img_path):
                print(f"Warning: Image path not found: {img_path}")
                continue
            
            # Open images as a document
            imgdoc = fitz.open(img_path)
            # Convert image to PDF 
            pdfbytes = imgdoc.convert_to_pdf()
            imgpdf = fitz.open("pdf", pdfbytes)
            # Insert into main document
            doc.insert_pdf(imgpdf)
            imgdoc.close()
            imgpdf.close()

        doc.save(output_path)
        doc.close()
        print(f"Successfully stitched {len(image_paths)} images into {output_path}")
        return True
    except Exception as e:
        print(f"Error stitching images to PDF: {e}")
        return False


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf",  default="input.pdf")
    parser.add_argument("--json", default="markup_plan.json")
    parser.add_argument("--out",  default="output_markedup.pdf")
    args = parser.parse_args()
    apply_markups(args.pdf, args.json, args.out)
