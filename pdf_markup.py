"""
pdf_markup.py — Applies AI-generated QC markups to the PDF.
bbox format: bbox_pdf_pct [x0%, y0%, x1%, y1%] — percentage of page, top-left origin.
"""
import json
import fitz  # PyMuPDF
import os


def draw_annotated_callout(page, rect, finding_id, rule_id, requirement, color):
    """Draws a premium two-line callout."""
    ax, ay = rect.x0, rect.y0
    dx = -60 if ax > 60 else 60
    dy = -40 if ay > 40 else 40
    
    p1 = fitz.Point(ax, ay)
    p2 = fitz.Point(ax + dx, ay + dy)
    page.draw_line(p1, p2, color=color, width=1.2)
    
    # Text Block
    bw, bh = 150, 24
    bx = p2.x if dx > 0 else p2.x - bw
    by = p2.y - (bh / 2)
    box = fitz.Rect(bx, by, bx + bw, by + bh)
    
    page.draw_rect(box, color=color, fill=(1,1,1), width=0.8)
    
    # Combine ID, Rule and Requirement
    full_text = f"[{finding_id}] {rule_id}\n{requirement} required"
    page.insert_textbox(box, full_text, fontsize=7, color=color, align=1)


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

            # --- UPDATED: Premium Annotated Callout ---
            requirement = m.get("requirement")
            rule_id = m.get("rule_id", "ERROR")
            if requirement:
                draw_annotated_callout(page, rect, finding_id, rule_id, requirement, color)

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
