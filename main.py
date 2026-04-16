import os
import logging
import shutil
from typing import List
import base64
import json
from PIL import Image, ImageDraw
from pdf_markup import apply_markups, stitch_images_to_pdf

from fastapi import FastAPI, Request, UploadFile, File, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import fitz  # PyMuPDF — used for both PDF->PNG and standards text extraction
from dotenv import load_dotenv
from pydantic import BaseModel

# ──────────────────────────────────────────────
# LOGGING SETUP
# ──────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOGS_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOGS_DIR, exist_ok=True)

log_file = os.path.join(LOGS_DIR, "app.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  [%(levelname)s]  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(),           # still prints to uvicorn console
    ],
)
logger = logging.getLogger("millwork_qc")

# ──────────────────────────────────────────────
# ENV & APP
# ──────────────────────────────────────────────
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

app = FastAPI(title="Millwork QC Automation")

# Production CORS safety
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Define all directories used by the app
STATIC_DIR      = os.path.join(BASE_DIR, "static")
TEMPLATES_DIR   = os.path.join(BASE_DIR, "templates")
UPLOADS_DIR     = os.path.join(BASE_DIR, "uploads")
STANDARDS_DIR   = os.path.join(UPLOADS_DIR, "standards")
DRAWINGS_DIR    = os.path.join(UPLOADS_DIR, "drawings")

# Audit & Debug directories inside UPLOADS for persistence
DEBUG_DIR_NAMES = [
    "debug_scout_results",
    "debug_sniper_calls",
    "debug_sniper_responses",
    "debug_crops",
    "debug_logs"
]

all_dirs = [STATIC_DIR, TEMPLATES_DIR, STANDARDS_DIR, DRAWINGS_DIR]
for d_name in DEBUG_DIR_NAMES:
    all_dirs.append(os.path.join(UPLOADS_DIR, d_name))

# Create them all recursively
for d in all_dirs:
    os.makedirs(d, exist_ok=True)

try:
    app.mount("/static",    StaticFiles(directory=STATIC_DIR),    name="static")
    app.mount("/standards", StaticFiles(directory=STANDARDS_DIR), name="standards")
    app.mount("/drawings",  StaticFiles(directory=DRAWINGS_DIR),  name="drawings")
    app.mount("/logo",      StaticFiles(directory="logo"),        name="logo")
except RuntimeError:
    pass

templates = Jinja2Templates(directory=TEMPLATES_DIR)

# ──────────────────────────────────────────────
# PYDANTIC
# ──────────────────────────────────────────────
class AnalyzeRequest(BaseModel):
    filename: str

# Calibration
CALIBRATION_Y_OFFSET = 2.2 # Moves boxes DOWN by 2.2% of page height
CALIBRATION_X_OFFSET = -0.5 # Moves boxes LEFT by 0.5% of page width

# ──────────────────────────────────────────────
# SNIPER CONFIGS (Semantic Hints)
# ──────────────────────────────────────────────
GLOBAL_DO_NOT_SELECT = [
    "title block and schedules",
    "general note blocks",
    "page numbers",
    "FINISHED FLOOR line text"
]

SNIPER_CONFIGS = {
    "ADA-KNEE-CLEARANCE-27": {
        "selection_rule": "Select the dimension text/callout that states the vertical knee-clearance height in the ADA Sink Cabinet section. Prefer the offending measurement itself over nearby notes or labels.",
        "preferred_candidate_terms": ['26" MIN', '27" MIN', '26 1/2"', '27"'],
        "target_object_type": "dimension_text",
        "do_not_select": ["B SECTION - ADA SINK CABINET", "ADA KNEE CLEARANCE LINES", '31" A.F.F.']
    },
    "ADA-TOE-CLEARANCE-9": {
        "selection_rule": "Select the dimension text for the toe kick height (vertical clearance from floor).",
        "preferred_candidate_terms": ['8" MIN', '9" MIN', '8 1/2"'],
        "target_object_type": "dimension_text",
        "do_not_select": ["FINISHED FLOOR", "TOE KICK DETAIL"]
    }
}



# ──────────────────────────────────────────────
# PAGES
# ──────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")

# ──────────────────────────────────────────────
# GUIDELINES API
# ──────────────────────────────────────────────
@app.get("/api/guidelines")
async def list_guidelines():
    files = os.listdir(STANDARDS_DIR) if os.path.exists(STANDARDS_DIR) else []
    return JSONResponse(content={"files": files})

@app.post("/api/upload_guideline")
async def upload_guideline(files: List[UploadFile] = File(...)):
    uploaded_files = []
    for file in files:
        file_path = os.path.join(STANDARDS_DIR, file.filename)
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        logger.info(f"Guideline uploaded: {file.filename}")
        uploaded_files.append(file.filename)
    return JSONResponse(content={"message": f"{len(files)} guideline(s) uploaded.", "files": uploaded_files})

@app.delete("/api/delete_guideline/{filename}")
async def delete_guideline(filename: str):
    file_path = os.path.join(STANDARDS_DIR, filename)
    if os.path.exists(file_path):
        os.remove(file_path)
        logger.info(f"Guideline deleted: {filename}")
        return {"message": "Success"}
    return JSONResponse(status_code=404, content={"message": "File not found"})

# ──────────────────────────────────────────────
# DRAWINGS API
# ──────────────────────────────────────────────
@app.get("/api/drawings")
async def list_drawings():
    files = os.listdir(DRAWINGS_DIR) if os.path.exists(DRAWINGS_DIR) else []
    drawings = []
    seen_pdfs = set()
    for f in sorted(files):
        if f.lower().endswith('.pdf'):
            seen_pdfs.add(f)
            # Collect all generated page PNGs for this PDF
            stem = f[:-4]  # filename without .pdf
            pages = []
            page_idx = 0
            while True:
                page_file = f"{stem}_page_{page_idx}.png"
                if page_file in files:
                    pages.append(f"/drawings/{page_file}")
                    page_idx += 1
                else:
                    break
            # Fallback: old single-page PNG name
            if not pages and f"{stem}.png" in files:
                pages.append(f"/drawings/{stem}.png")
            drawings.append({"filename": f, "pages": pages, "page_count": len(pages)})
    return JSONResponse(content={"files": drawings})

@app.post("/api/upload_drawing")
async def upload_drawing(file: UploadFile = File(...)):
    file_path = os.path.join(DRAWINGS_DIR, file.filename)
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    logger.info(f"Drawing uploaded: {file.filename}")

    stem = file.filename[:-4] if file.filename.lower().endswith('.pdf') else file.filename
    pages = []
    page_dims = []  # list of {width, height} in PDF points per page

    if file.filename.lower().endswith('.pdf'):
        try:
            doc = fitz.open(file_path)
            mat = fitz.Matrix(2.0, 2.0)  # 2× scale for crisp rendering
            for i, page in enumerate(doc):
                pix = page.get_pixmap(matrix=mat, alpha=False)
                page_filename = f"{stem}_page_{i}.png"
                page_path = os.path.join(DRAWINGS_DIR, page_filename)
                pix.save(page_path)
                pages.append(f"/drawings/{page_filename}")
                page_dims.append({"width": page.rect.width, "height": page.rect.height})
                logger.info(f"  Page {i} → {page_filename} ({pix.width}×{pix.height}px)")
            doc.close()
            logger.info(f"PDF converted: {len(pages)} page(s) for {file.filename}")
        except Exception as e:
            logger.error(f"PDF conversion error for {file.filename}: {e}")

    return JSONResponse(content={
        "message": "Drawing uploaded successfully",
        "filename": file.filename,
        "pages": pages,
        "page_count": len(pages),
        "page_dims": page_dims,
    })

@app.delete("/api/delete_drawing/{filename}")
async def delete_drawing(filename: str):
    file_path = os.path.join(DRAWINGS_DIR, filename)
    if os.path.exists(file_path):
        # Determine the stem (e.g., 'Drawing_A' from 'Drawing_A.pdf')
        stem = filename[:-4] if filename.lower().endswith('.pdf') else filename.rsplit('.', 1)[0]
        
        # Define all directories that might contain derivatives
        target_dirs = [DRAWINGS_DIR] + [os.path.join(UPLOADS_DIR, d) for d in DEBUG_DIR_NAMES]
        
        purged_count = 0
        for d_path in target_dirs:
            if not os.path.exists(d_path): continue
            
            for f in os.listdir(d_path):
                # Match original file OR derivatives (starting with stem followed by separator)
                # This prevents 'Drawing 1' from deleting 'Drawing 10'
                # Included '.' to catch thumbnails and direct extension swaps
                separators = ["_", " ", "-", "."]
                is_match = (f == filename) or any(f.startswith(stem + s) for s in separators)
                
                if is_match:
                    try:
                        os.remove(os.path.join(d_path, f))
                        purged_count += 1
                    except Exception as e:
                        logger.error(f"Error purging file {f} in {d_path}: {e}")

        logger.info(f"Drawing and derivatives purged: {filename} ({purged_count} files removed)")
        return {"message": f"Success. {purged_count} files purged.", "purged_count": purged_count}
        
    return JSONResponse(status_code=404, content={"message": "File not found"})

@app.get("/api/results/{filename}")
async def get_results(filename: str):
    """Fetch previously saved analysis results for a drawing."""
    stem = filename[:-4] if filename.lower().endswith('.pdf') else filename
    results_path = os.path.join(UPLOADS_DIR, "debug_logs", f"{stem}_results.json")
    
    if os.path.exists(results_path):
        try:
            with open(results_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return JSONResponse(content=data)
        except Exception as e:
            logger.error(f"Error reading results for {filename}: {e}")
    
    return JSONResponse(status_code=404, content={"message": "No results found"})

# ──────────────────────────────────────────────
# WEBSOCKET ANALYSIS PIPELINE
# ──────────────────────────────────────────────
async def ws_send(ws: WebSocket, status: str, message: str, data: dict = None):
    """Helper to send a status message over the WebSocket."""
    payload = {"status": status, "message": message}
    if data:
        payload["data"] = data
    await ws.send_json(payload)

def extract_text_map(pdf_path: str, page_index: int) -> list[dict]:
    """Extracts text blocks and returns IDs for the AI to reference."""
    try:
        import fitz
        with fitz.open(pdf_path) as doc:
            if page_index >= len(doc):
                return []
            page = doc[page_index]
            
            # Simple rotation matrix
            mat = page.rotation_matrix
            
            blocks = []
            for i, b in enumerate(page.get_text("blocks")):
                text = b[4].strip()
                if text and len(text) > 1:
                    r = fitz.Rect(b[:4])
                    tr = r * mat
                    
                    blocks.append({
                        "id": f"T-{i:03d}",
                        "text": text.replace("\n", " "),
                        "bbox": [round(tr.x0, 2), round(tr.y0, 2), round(tr.x1, 2), round(tr.y1, 2)]
                    })
            return blocks
    except Exception as e:
        logger.error(f"Error extracting text map: {e}")
        return []


@app.websocket("/ws/analyze")
async def analyze_via_ws(ws: WebSocket):
    await ws.accept()
    logger.info("WebSocket analysis connection opened.")

    try:
        body = await ws.receive_json()
        filename = body.get("filename", "")

        if not filename:
            await ws_send(ws, "error", "No filename provided.")
            await ws.close()
            return

        logger.info(f"Analysis started for: {filename}")
        original_file_path = os.path.join(DRAWINGS_DIR, filename)

        if not os.path.exists(original_file_path):
            await ws_send(ws, "error", f"Drawing file not found: {filename}")
            await ws.close()
            return

        # Collect all page PNGs for this drawing
        stem = filename[:-4] if filename.lower().endswith('.pdf') else filename
        page_images = []   # list of (page_index, image_path)
        page_dims = []     # list of {width, height} in PDF points

        if filename.lower().endswith('.pdf'):
            doc_check = fitz.open(original_file_path)
            i = 0
            for i, page in enumerate(doc_check):
                page_file = os.path.join(DRAWINGS_DIR, f"{stem}_page_{i}.png")
                if os.path.exists(page_file):
                    page_images.append((i, page_file))
                    page_dims.append({"width": page.rect.width, "height": page.rect.height})
            doc_check.close()
        else:
            image_path = os.path.join(DRAWINGS_DIR, filename)
            if os.path.exists(image_path):
                page_images.append((0, image_path))
                page_dims.append({"width": 0, "height": 0})  # unknown for plain images

        if not page_images:
            await ws_send(ws, "error", "No page images found. Please re-upload the drawing.")
            await ws.close()
            return

        # Use page 0 for Vision analysis (most drawings are single-page or key info on p0)
        image_path = page_images[0][1]

        # ── STEP 1: Read Standards Documents (PDF + detect Excel) ──────
        await ws_send(ws, "step", "📄 Step 1/4 — Reading ADA & NAAWS Standard Documents...")
        logger.info("Step 1: Scanning standards folder for PDF and Excel files...")

        import asyncio
        standards_pdf_context = ""
        standards_pdf_found   = []
        rules_excel_files     = []   # paths to .xlsx / .xls files

        if os.path.exists(STANDARDS_DIR):
            for std_file in sorted(os.listdir(STANDARDS_DIR)):
                std_path = os.path.join(STANDARDS_DIR, std_file)
                if std_file.lower().endswith('.pdf'):
                    try:
                        doc = fitz.open(std_path)
                        all_text = ""
                        for i in range(len(doc)):
                            all_text += doc[i].get_text()
                        standards_pdf_context += all_text
                        standards_pdf_found.append(f"{std_file} ({len(doc)} pages)")
                        logger.info(f"  PDF standard: {std_file} ({len(doc)} pages, {len(all_text)} chars)")
                        doc.close()
                    except Exception as e:
                        logger.error(f"  Error reading standard PDF {std_file}: {e}")
                elif std_file.lower().endswith(('.xlsx', '.xls')):
                    rules_excel_files.append(std_path)
                    logger.info(f"  Rules Engine Excel detected: {std_file}")

        pdf_summary = ', '.join(standards_pdf_found) or 'None'
        excel_summary = f"{len(rules_excel_files)} Excel rules file(s)" if rules_excel_files else 'None'
        await ws_send(ws, "step_done",
            f"✅ Step 1/4 — PDFs: {pdf_summary} | Excel: {excel_summary}")

        # ── STEP 2: Parse Excel Rules Engine (real step) ─────────────────
        await ws_send(ws, "step", "⚙️  Step 2/4 — Loading Rules Engine from Excel...")
        logger.info("Step 2: Parsing Excel rules engine...")

        from rules_engine import parse_rules_excel
        all_rules       = []
        rules_prompt    = ""

        for excel_path in rules_excel_files:
            rules, prompt_chunk = parse_rules_excel(excel_path)
            all_rules.extend(rules)
            rules_prompt += prompt_chunk + "\n"

        if all_rules:
            logger.info(f"  Loaded {len(all_rules)} rules from {len(rules_excel_files)} Excel file(s).")
            await ws_send(ws, "step_done",
                f"✅ Step 2/4 — Rules Engine loaded: {len(all_rules)} rules from Excel.")
        else:
            logger.warning("  No rules loaded from Excel. Falling back to raw PDF context only.")
            await ws_send(ws, "step_done",
                "✅ Step 2/4 — No Excel rules found; using raw PDF standards as fallback.")

        # ── STEP 3: AI Vision Analysis ──────────────────────────────────
        await ws_send(ws, "step", "🤖 Step 3/4 — AI Vision Engine Analyzing Blueprint...")
        logger.info("Step 3: Sending drawing to OpenAI GPT-4o Vision API...")

        from prompts import QC_SYSTEM_PROMPT
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=OPENAI_API_KEY)

        # Build the full system prompt:
        # Priority 1 → Structured rules checklist from Excel (with WHERE TO LOOK spatial anchors)
        # Priority 2 → Raw PDF standards text as supplementary context
        full_system_prompt = QC_SYSTEM_PROMPT

        if rules_prompt.strip():
            full_system_prompt += (
                "\n\n" + "═" * 60 + "\n"
                "--- RULES CHECKLIST (PRIMARY — check EVERY row) ---\n"
                "Each rule includes a 'WHERE TO LOOK ON DRAWING' field.\n"
                "Use it as the spatial anchor for your bbox_pct coordinates.\n"
                + "═" * 60 + "\n"
                + rules_prompt[:20000]
            )

        if standards_pdf_context.strip():
            full_system_prompt += (
                "\n\n" + "─" * 60 + "\n"
                "--- SUPPLEMENTARY STANDARDS CONTEXT (PDF reference) ---\n"
                "─" * 60 + "\n"
                + standards_pdf_context[:10000]
            )

        # ── PHASE 1: Vision Engine (Global Scan) ────────────────────
        await ws_send(ws, "step", "🔎 Step 3/4 — AI Global Vision Analysis...")
        
        from grid_overlay import add_grid_overlay
        from image_processor import enhance_drawing
        
        all_markup_plans = []
        all_qc_tables = []
        page_pixel_dims = {}
        page_anchors = {} # [p_idx] -> anchors list
        
        for p_idx, p_path in page_images:
            logger.info(f"Analyzing Page {p_idx} (Global)...")
            
            # 1. Prepare Image (Grid + 2x)
            gridded_bytes = add_grid_overlay(p_path)
            image_b64 = base64.b64encode(gridded_bytes).decode('utf-8')
            
            # 2. Extract Text Anchors & Normalize to Percentages
            anchors = extract_text_map(original_file_path, p_idx)
            page_anchors[p_idx] = anchors # Cache for Stage 2
            
            # Get PDF logical dimensions for normalization
            with fitz.open(original_file_path) as doc:
                p_rect = doc[p_idx].rect
                pw, ph = p_rect.width, p_rect.height

            ai_anchors = []
            for a in anchors:
                if pw > 0 and ph > 0:
                    coords_pct = [
                        round((a["bbox"][0] / pw) * 100.0, 1),
                        round((a["bbox"][1] / ph) * 100.0, 1),
                        round((a["bbox"][2] / pw) * 100.0, 1),
                        round((a["bbox"][3] / ph) * 100.0, 1)
                    ]
                    ai_anchors.append({"id": a["id"], "text": a["text"], "coords_pct": coords_pct})
                else:
                    ai_anchors.append({"id": a["id"], "text": a["text"]})
            
            # 3. Store Dims for markup scaling
            with Image.open(p_path) as img:
                page_pixel_dims[p_idx] = (img.width, img.height)

            # 4. AI Call (Set to High Detail & Full Context)
            content_array = [
                {"type": "text", "text": f"--- PAGE {p_idx} ANALYSIS ---\nScan the image and provided text anchors to verify compliance."},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}", "detail": "high"}},
                {"type": "text", "text": f"text_anchors (Page {p_idx}):\n" + json.dumps(ai_anchors[:1400])}
            ]

            response = await client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": full_system_prompt},
                    {"role": "user", "content": content_array}
                ],
                response_format={"type": "json_object"},
                temperature=0.0
            )

            # --- SAVE AUDIT: Scout Result + Input Image ---
            scout_log_path = os.path.join(UPLOADS_DIR, "debug_scout_results", f"{stem}_p{p_idx}_scout.json")
            with open(scout_log_path, "w", encoding="utf-8") as f:
                json.dump({
                    "system_prompt": full_system_prompt,
                    "user_text": f"--- PAGE {p_idx} ANALYSIS ---\nScan the image and provided text anchors...",
                    "raw_response": response.choices[0].message.content
                }, f, indent=2)
            
            # Save the exact image sent
            scout_img_path = os.path.join(UPLOADS_DIR, "debug_scout_results", f"{stem}_p{p_idx}_scout_input.png")
            with open(scout_img_path, "wb") as f:
                f.write(gridded_bytes)

            # Phase 1 result parsing
            res_json = json.loads(response.choices[0].message.content)
            
            if "markup_plan" in res_json:
                for item in res_json["markup_plan"]:
                    item["page_index"] = p_idx
                all_markup_plans.extend(res_json["markup_plan"])
            
            if "qc_issue_table" in res_json:
                all_qc_tables.extend(res_json["qc_issue_table"])

        # ── PHASE 2: Full-Page Sniper Refinement ─────────────────────
        await ws_send(ws, "step", "🎯 Step 4/5 — Sniper Refinement (Whole Page Context)...")
        logger.info("Step 4: Starting Sniper phase (Full Page)...")
        
        from prompts import SNIPER_PROMPT
        from image_processor import draw_markups_on_image
        
        # ONLY refine FAIL detections for the drawing
        refine_items = [m for m in all_markup_plans if m.get("result") == "FAIL"]
        
        for i, m_item in enumerate(refine_items):
            rid = m_item.get("rule_id", "Unknown")
            p_idx = m_item["page_index"]
            anchors = page_anchors.get(p_idx, [])
            img_w, img_h = page_pixel_dims.get(p_idx, (2000, 1500))
            orig_w = page_dims[p_idx]["width"]
            orig_h = page_dims[p_idx]["height"]
            
            # 1. Get Snippet Metadata
            config = SNIPER_CONFIGS.get(rid, {})
            hint_box = m_item.get("bbox_pct", [0,0,100,100])
            
            # 2. Filter focus_anchor_ids (Spatial Buffer: 15%)
            focus_ids = []
            margin = 15.0
            search_x0 = max(0, hint_box[0] - margin)
            search_y0 = max(0, hint_box[1] - margin)
            search_x1 = min(100, hint_box[2] + margin)
            search_y1 = min(100, hint_box[3] + margin)
            
            for a in anchors:
                if orig_w > 0 and orig_h > 0:
                    a_pct_x0 = (a["bbox"][0] / orig_w) * 100.0
                    a_pct_y0 = (a["bbox"][1] / orig_h) * 100.0
                    a_pct_x1 = (a["bbox"][2] / orig_w) * 100.0
                    a_pct_y1 = (a["bbox"][3] / orig_h) * 100.0
                    
                    cntr_x = (a_pct_x0 + a_pct_x1) / 2.0
                    cntr_y = (a_pct_y0 + a_pct_y1) / 2.0
                    
                    if search_x0 <= cntr_x <= search_x1 and search_y0 <= cntr_y <= search_y1:
                        focus_ids.append(a["id"])

            # 3. Build the Structured Request with Grounded Focal Anchors
            focal_anchors_grounded = []
            for fid in focus_ids[:60]:
                match = next((a for a in anchors if a["id"] == fid), None)
                if match:
                    ax0, ay0, ax1, ay1 = match["bbox"]
                    focal_anchors_grounded.append({
                        "id": fid,
                        "text": match["text"],
                        "coords_pct": [
                            round((ax0 / orig_w) * 100.0, 1),
                            round((ay0 / orig_h) * 100.0, 1),
                            round((ax1 / orig_w) * 100.0, 1),
                            round((ay1 / orig_h) * 100.0, 1)
                        ]
                    })

            sniper_request = {
                "rule_id": rid,
                "page_index": p_idx,
                "page_width_px": img_w,
                "page_height_px": img_h,
                "focus_detail_title": m_item.get("sheet_or_view", "Unknown View"),
                "focus_region_hint_pct": hint_box,
                "target_object_type": config.get("target_object_type", "dimension_text"),
                "rule_description": m_item.get("note_text", ""),
                "selection_rule": config.get("selection_rule", f"Select the element that represents the error: {rid}"),
                "preferred_candidate_terms": config.get("preferred_candidate_terms", []),
                "focus_anchors": focal_anchors_grounded,
                "do_not_select": GLOBAL_DO_NOT_SELECT + config.get("do_not_select", [])
            }
            
            # Use gridded high-res exactly the same as Phase 1
            gridded_bytes = add_grid_overlay(page_images[p_idx][1])
            image_b64 = base64.b64encode(gridded_bytes).decode('utf-8')
            
            logger.info(f"  [Sniper] Refining {rid} on Page {p_idx} with {len(focus_ids)} focal anchors...")
            
            # --- SAVE AUDIT ---
            input_log_path = os.path.join(UPLOADS_DIR, "debug_sniper_calls", f"{stem}_{rid}_p{p_idx}_call.json")
            with open(input_log_path, "w", encoding="utf-8") as f:
                json.dump(sniper_request, f, indent=2)

            # SAVE IMAGE for transparency
            sniper_img_path = os.path.join(UPLOADS_DIR, "debug_sniper_calls", f"{stem}_{rid}_p{p_idx}_call_input.png")
            try:
                import io
                with Image.open(io.BytesIO(gridded_bytes)) as img:
                    draw = ImageDraw.Draw(img)
                    # Draw Focus Region Hint (Magenta) - 3px wide
                    h0, h1, h2, h3 = hint_box
                    x0 = (h0 / 100.0) * img.width
                    y0 = (h1 / 100.0) * img.height
                    x1 = (h2 / 100.0) * img.width
                    y1 = (h3 / 100.0) * img.height
                    draw.rectangle([x0, y0, x1, y1], outline="#ff00ff", width=4)
                    
                    # Draw Search Buffer (Cyan) - 1px dotted (using small lines)
                    s0 = (search_x0 / 100.0) * img.width
                    s1 = (search_y0 / 100.0) * img.height
                    s2 = (search_x1 / 100.0) * img.width
                    s3 = (search_y1 / 100.0) * img.height
                    draw.rectangle([s0, s1, s2, s3], outline="#00ffff", width=2)
                    
                    img.save(sniper_img_path)
            except Exception as e:
                logger.error(f"Error saving sniper debug image: {e}")
                
            sniper_response = await client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": SNIPER_PROMPT},
                    {"role": "user", "content": [
                        {"type": "text", "text": json.dumps(sniper_request, indent=2)},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}", "detail": "high"}}
                    ]}
                ],
                response_format={"type": "json_object"},
                temperature=0.0
            )
            
            # --- SAVE AUDIT: Sniper Output (Response) ---
            output_log_path = os.path.join(UPLOADS_DIR, "debug_sniper_responses", f"{stem}_{rid}_p{p_idx}_response.json")
            with open(output_log_path, "w", encoding="utf-8") as f:
                f.write(sniper_response.choices[0].message.content)

            s_json = json.loads(sniper_response.choices[0].message.content)
            status = s_json.get("status", "found")
            loc_bbox = s_json.get("refined_bbox_pct")
            
            if status == "found" and loc_bbox:
                # Apply calibration offsets (moves DOWN and LEFT)
                calibrated_bbox = [
                    max(0.0, min(100.0, loc_bbox[0] + CALIBRATION_X_OFFSET)),
                    max(0.0, min(100.0, loc_bbox[1] + CALIBRATION_Y_OFFSET)),
                    max(0.0, min(100.0, loc_bbox[2] + CALIBRATION_X_OFFSET)),
                    max(0.0, min(100.0, loc_bbox[3] + CALIBRATION_Y_OFFSET))
                ]
                m_item["bbox_pct"] = calibrated_bbox
                m_item["anchor_id"] = s_json.get("anchor_id")
                logger.info(f"    -> Refined coordinates: {m_item['bbox_pct']} (Original: {loc_bbox})")
            elif status == "uncertain":
                logger.warning(f"    -> Sniper UNCERTAIN for {rid}: {s_json.get('reasoning')}")
            else:
                logger.error(f"    -> Sniper NOT_FOUND for {rid}: {s_json.get('reasoning')}")

        # ── STEP 5: Final Report & Drawing ──────────────────────
        await ws_send(ws, "step", "🖊️  Step 5/5 — Finalizing Report & Drawing Markups...")
        
        # Simple Deduplication Logic (IoU)
        def get_iou(boxA, boxB):
            xA = max(boxA[0], boxB[0]); yA = max(boxA[1], boxB[1])
            xB = min(boxA[2], boxB[2]); yB = min(boxA[3], boxB[3])
            interArea = max(0, xB - xA) * max(0, yB - yA)
            boxAArea = (boxA[2] - boxA[0]) * (boxA[3] - boxA[1])
            boxBArea = (boxB[2] - boxB[0]) * (boxB[3] - boxB[1])
            return interArea / float(boxAArea + boxBArea - interArea)

        merged_markup = []
        for item in all_markup_plans:
            # ONLY DRAW BOXES FOR FAIL ERRORS
            if item.get("result") != "FAIL": continue
            
            is_dup = False
            for existing in merged_markup:
                if item.get("rule_id") == existing.get("rule_id"):
                    if get_iou(item["bbox_pct"], existing["bbox_pct"]) > 0.4:
                        is_dup = True; break
            if not is_dup: merged_markup.append(item)

        # Build final display results
        full_result_map = {}
        for idx, item in enumerate(all_qc_tables):
            rid   = item.get("rule_id") or f"R-{idx+1:03d}"
            res   = item.get("result", "INFO ONLY")
            view  = item.get("sheet_view", "").strip()
            elem  = item.get("element_description", "").strip()
            
            # Construct a rich context string: "Elevation A | Toe Kick | FAIL: Check 7\""
            prefix = f"{view} | {elem} | " if view or elem else ""
            msg = f"{prefix}{res}: {item.get('parameter_checked', 'Check')} {item.get('shown_value','')}".strip()
            
            # Dynamic Severity mapping
            severity = "INFO" # Defaulting PASS to INFO
            if res == "FAIL": severity = "HIGH"
            elif res == "REVIEW REQUIRED": severity = "MEDIUM"
            
            # Truncate requirement to keep it short
            req_text = item.get("required_value", "").strip()
            if len(req_text) > 30: req_text = req_text[:27] + "..."
            
            if rid not in full_result_map or res == "FAIL":
                full_result_map[rid] = {
                    "id": f"F-{idx+1:03d}", "category": rid,
                    "error_message": f"{msg} ({rid})",
                    "standard_ref": f"{res}", 
                    "severity": severity, 
                    "requirement": req_text, # ADDED
                    "page_index": 0, "has_bbox": False, "x":0, "y":0, "width":0, "height":0
                }

        # Enrich with Spatial data
        for item in merged_markup:
            rid = item.get("rule_id")
            if rid in full_result_map:
                res_item = full_result_map[rid]
                p_idx = item["page_index"]
                img_w, img_h = page_pixel_dims.get(p_idx, (2000, 1500))
                bx = item["bbox_pct"]
                res_item["bbox_pct"] = bx # Save raw pct for PDF engine
                res_item["x"] = (bx[0]/100.0)*img_w; res_item["y"] = (bx[1]/100.0)*img_h
                res_item["width"] = ((bx[2]-bx[0])/100.0)*img_w; res_item["height"] = ((bx[3]-bx[1])/100.0)*img_h
                res_item["has_bbox"] = True; res_item["page_index"] = p_idx
                res_item["id_label"] = res_item["id"] # F-001

        # ── DRAW MARKUPS ──────────────────────────────────────────
        page_markup_map = {}
        for item in merged_markup:
            p_idx = item["page_index"]
            if p_idx not in page_markup_map: page_markup_map[p_idx] = []
            
            # Find the F-001 label for this rule
            rid = item.get("rule_id")
            f_id = full_result_map[rid]["id"] if rid in full_result_map else "??"
            
            page_markup_map[p_idx].append({
                "id": f_id,
                "bbox_pct": item["bbox_pct"],
                "rule_id": full_result_map[rid].get("category", ""), # ADDED
                "requirement": full_result_map[rid].get("requirement", ""),
                "result": full_result_map[rid].get("standard_ref", "FAIL")
            })
            
        marked_up_pages = {}
        for p_idx, items in page_markup_map.items():
            source_path = page_images[p_idx][1]
            out_name = f"{stem}_page_{p_idx}_markedup.png"
            out_path = os.path.join(DRAWINGS_DIR, out_name)
            
            success = draw_markups_on_image(source_path, items, out_path)
            if success:
                marked_up_pages[p_idx] = f"/drawings/{out_name}"
                logger.info(f"  Page {p_idx} marked up: {out_name}")

        frontend_errors = list(full_result_map.values())

        # ── GENERATE PDF REPORT (STITCHED FROM IMAGES) ──────────────────────────
        pdf_out_name = f"{stem}_markedup.pdf"
        pdf_out_path = os.path.join(DRAWINGS_DIR, pdf_out_name)
        
        # Collect paths for all pages (using marked-up versions where available)
        final_image_paths = []
        for i, (p_idx, p_path) in enumerate(page_images):
            # The markedup filename used above was f"{stem}_page_{p_idx}_markedup.png"
            marked_up_path = os.path.join(DRAWINGS_DIR, f"{stem}_page_{p_idx}_markedup.png")
            if os.path.exists(marked_up_path):
                final_image_paths.append(marked_up_path)
            else:
                # Use the original clean PNG for this page
                final_image_paths.append(p_path)
        
        try:
            success = stitch_images_to_pdf(final_image_paths, pdf_out_path)
            if success:
                marked_up_pdf_url = f"/drawings/{pdf_out_name}"
                logger.info(f"PDF Report (Stitched) generated: {pdf_out_name} with {len(final_image_paths)} pages.")
            else:
                marked_up_pdf_url = ""
        except Exception as e:
            logger.error(f"Failed to generate Stitched PDF Report: {e}")
            marked_up_pdf_url = ""

        # ── COMPLETE ─────────────────────────────────────────────────────
        logger.info(f"Analysis complete for {filename}. Findings: {len(frontend_errors)}")
        
        results_data = {
            "errors": frontend_errors,
            "summary": {"total": len(frontend_errors), "fails": sum(1 for e in frontend_errors if e.get("severity") == "FAIL")},
            "page_count": len(page_images),
            "marked_up_pages": marked_up_pages, 
            "marked_up_pdf_url": marked_up_pdf_url,
            "stem": stem
        }

        # PERSIST RESULTS TO DISK
        try:
            results_path = os.path.join(UPLOADS_DIR, "debug_logs", f"{stem}_results.json")
            with open(results_path, "w", encoding="utf-8") as f:
                json.dump(results_data, f, indent=2)
            logger.info(f"Results persisted to: {results_path}")
        except Exception as e:
            logger.error(f"Failed to persist results: {e}")

        await ws_send(ws, "complete", "Analysis complete!", data=results_data)
        
        # Small sleep to ensure final packet is flushed before socket closes
        await asyncio.sleep(1.0)

    except WebSocketDisconnect:
        logger.warning("WebSocket client disconnected during analysis.")
    except Exception as e:
        logger.error(f"Unexpected error in WebSocket analysis: {e}", exc_info=True)
        try:
            await ws_send(ws, "error", f"Unexpected server error: {str(e)}")
        except Exception:
            pass
    finally:
        logger.info("WebSocket analysis connection closed.")
