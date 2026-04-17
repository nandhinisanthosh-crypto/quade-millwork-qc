"""
pdf_markup.py — Applies AI-generated QC markups to the PDF.
bbox format: bbox_pdf_pct [x0%, y0%, x1%, y1%] — percentage of page, top-left origin.
"""
import json
import fitz  # PyMuPDF
import os
from datetime import datetime


def draw_annotated_callout(page, rect, finding_id, rule_id, requirement, color):
    """Draws a premium two-line callout."""
    ax, ay = rect.x0, rect.y0
    dx = -70 if ax > 70 else 70
    dy = -50 if ay > 50 else 50
    
    p1 = fitz.Point(ax, ay)
    p2 = fitz.Point(ax + dx, ay + dy)
    page.draw_line(p1, p2, color=color, width=1.2)
    
    # Text Block
    bw, bh = 180, 32
    bx = p2.x if dx > 0 else p2.x - bw
    by = p2.y - (bh / 2)
    box = fitz.Rect(bx, by, bx + bw, by + bh)
    
    page.draw_rect(box, color=color, fill=(1,1,1), width=0.8)
    
    # Combine ID, Rule and Requirement
    full_text = f"[{finding_id}] {rule_id}\n{requirement} required"
    page.insert_textbox(box, full_text, fontsize=10, color=color, align=1)


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

            # Add padding (15 points) for better visibility
            rect = fitz.Rect(x0 - 15, y0 - 15, x1 + 15, y1 + 15)
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


def add_audit_summary_page(doc, results_data):
    """
    Adds a professional cover page with company branding and a summary grid of findings.
    Dynamically matches the size of the drawings.
    """
    # Use first drawing page as dimension source if available
    target_pw, target_ph = 595, 842 # Default A4
    if doc.page_count > 0:
        target_pw = doc[0].rect.width
        target_ph = doc[0].rect.height
        
    page = doc.new_page(0, width=target_pw, height=target_ph)
    pw, ph = page.rect.width, page.rect.height
    
    # Dampened Scale factor (Square root ensures elements grow but stay controlled on huge sheets)
    s = (pw / 595.0) ** 0.5
    
    curr_y = 50 * s
    
    # --- BRANDING ---
    logo_path = os.path.join(os.path.dirname(__file__), "logo", "QUADE_Logo.png")
    if os.path.exists(logo_path):
        # Insert logo (Centered at top)
        # Even more dampened scaling for logo to avoid it dominating the page
        ls = (pw / 595.0) ** 0.35
        lw, lh = 120 * ls, 45 * ls
        page.insert_image(fitz.Rect((pw-lw)/2, curr_y, (pw+lw)/2, curr_y + lh), filename=logo_path)
        curr_y += lh + (15 * s)
    
    # Company Name
    page.insert_textbox(fitz.Rect(50 * s, curr_y, pw - (50 * s), curr_y + (30 * s)), "QUADE ENGINEERING SERVICES", 
                         fontsize=14 * s, fontname="hebo", color=(0.1, 0.1, 0.1), align=1)
    curr_y += (40 * s)
    
    # --- REPORT TITLE ---
    page.draw_line((50 * s, curr_y), (pw - (50 * s), curr_y), color=(0.8, 0.8, 0.8), width=1 * s)
    curr_y += (20 * s)
    
    page.insert_textbox(fitz.Rect(50 * s, curr_y, pw - (50 * s), curr_y + (40 * s)), "QC AUDIT REPORT", 
                         fontsize=18 * s, fontname="hebo", color=(0.93, 0.26, 0.26), align=1)
    curr_y += (45 * s)
    
    date_str = datetime.now().strftime("%B %d, %Y")
    page.insert_textbox(fitz.Rect(50 * s, curr_y, pw - (50 * s), curr_y + (25 * s)), f"Report Date: {date_str}", 
                         fontsize=10 * s, fontname="helv", color=(0.4, 0.4, 0.4), align=1)
    curr_y += (35 * s)
    
    # --- AUDIT GRID (TABLE) ---
    start_y = curr_y
    cols = [
        {"name": "ID", "width": 40 * s},
        {"name": "CATEGORY", "width": 120 * s},
        {"name": "FINDING / REQUIREMENT", "width": 260 * s},
        {"name": "STATUS", "width": 75 * s}
    ]
    
    # Header Row
    curr_x = 50 * s
    header_h = 25 * s
    page.draw_rect(fitz.Rect(50 * s, start_y, pw - (50 * s), start_y + header_h), 
                   color=(0.1, 0.1, 0.1), fill=(0.1, 0.1, 0.1))
    
    for col in cols:
        page.insert_text((curr_x + (5 * s), start_y + (16 * s)), col["name"], 
                         fontsize=9 * s, fontname="hebo", color=(1, 1, 1))
        curr_x += col["width"]
    
    # Data Rows
    errors = results_data.get("errors", []) if results_data else []
    curr_y = start_y + header_h
    row_h = 30 * s
    
    for i, err in enumerate(errors):
        # Handle Page Overflow
        if curr_y + row_h > ph - (60 * s):
            page = doc.new_page(doc.page_count, width=pw, height=ph)
            curr_y = 60 * s # Start fresh on new page
        
        # Zebra Striping
        if i % 2 == 1:
            page.draw_rect(fitz.Rect(50 * s, curr_y, pw - (50 * s), curr_y + row_h), 
                           fill=(0.97, 0.97, 0.97), width=0)
        
        curr_x = 50 * s
        # ID
        page.insert_text((curr_x + (5 * s), curr_y + (18 * s)), str(err.get("id", "??")), 
                         fontsize=9 * s, fontname="hebo")
        curr_x += cols[0]["width"]
        
        # Category
        page.insert_text((curr_x + (5 * s), curr_y + (18 * s)), str(err.get("category", "General")), 
                         fontsize=8 * s, fontname="helv")
        curr_x += cols[1]["width"]
        
        # Description (Requirement)
        desc = err.get("error_message", "N/A")
        if len(desc) > 65: desc = desc[:62] + "..."
        page.insert_text((curr_x + (5 * s), curr_y + (18 * s)), desc, 
                         fontsize=8 * s, fontname="helv")
        curr_x += cols[2]["width"]
        
        # Status
        status = err.get("standard_ref", "FAIL")
        s_color = (0.93, 0.26, 0.26) if "FAIL" in status else (0.97, 0.45, 0.08)
        page.insert_text((curr_x + (5 * s), curr_y + (18 * s)), status, 
                         fontsize=8 * s, fontname="hebo", color=s_color)
        
        # Row line
        page.draw_line((50 * s, curr_y + row_h), (pw - (50 * s), curr_y + row_h), 
                       color=(0.9, 0.9, 0.9), width=0.5 * s)
        curr_y += row_h

    # Footer
    page.insert_textbox(fitz.Rect(50 * s, ph - (40 * s), pw - (50 * s), ph - (20 * s)), 
                        "© Quade Engineering Services - Proprietary & Confidential", 
                        fontsize=8 * s, fontname="heit", color=(0.6, 0.6, 0.6), align=1)



def add_page_branding(page):
    """Adds a scaled branding header to a drawing page."""
    pw = page.rect.width
    # Scale header with dampened factor (0.4 power)
    s = (pw / 595.0) ** 0.4
    logo_path = os.path.join(os.path.dirname(__file__), "logo", "QUADE_Logo.png")
    
    # Top-right branding (scaled offsets)
    header_y = 20 * s
    logo_w, logo_h = 70 * s, 25 * s
    
    if os.path.exists(logo_path):
        page.insert_image(fitz.Rect(pw - (logo_w + (30 * s)), header_y, pw - (30 * s), header_y + logo_h), filename=logo_path)
    
    page.insert_textbox(fitz.Rect(pw - (300 * s), header_y, pw - (logo_w + (40 * s)), header_y + logo_h), 
                        "Quade Engineering Services", 
                        fontsize=8 * s, fontname="hebo", color=(0.5, 0.5, 0.5), align=2)


def stitch_images_to_pdf(image_paths: list[str], output_path: str, results_data: dict = None):
    """
    Creates a new PDF by stitching together high-res PNG images.
    Adds professional branding and an audit summary.
    """
    if not image_paths:
        print("Error: No images provided for stitching.")
        return
    
    try:
        doc = fitz.open()
        
        # 1. Add drawings first
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
            
            # Add branding to the newly inserted page
            add_page_branding(doc[-1])
            
            imgdoc.close()
            imgpdf.close()

        # 2. Add Audit Summary Page at the START
        if results_data:
            add_audit_summary_page(doc, results_data)

        doc.save(output_path)
        doc.close()
        print(f"Successfully stitched {len(image_paths)} images with branding into {output_path}")
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
