import math
import numpy as np
from PIL import Image, ImageEnhance, ImageOps, ImageDraw, ImageFont

def enhance_drawing(image: Image.Image) -> Image.Image:
    """
    Applies common engineering scan fixes: Grayscale, Contrast, and Sharpness.
    """
    # 1. Grayscale
    img = ImageOps.grayscale(image)
    
    # 2. Contrast Boost (simulates CLAHE/Threshold)
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(1.8) # Strong contrast for CAD lines
    
    # 3. Sharpening
    enhancer = ImageEnhance.Sharpness(img)
    img = enhancer.enhance(2.0)
    
    return img

def is_tile_meaningful(tile: Image.Image, threshold=0.98) -> bool:
    """
    Checks if a tile has enough content (not just white paper).
    Returns True if tile should be analyzed.
    """
    extrema = tile.convert("L").getextrema()
    if extrema[0] == extrema[1]: # Solid color
        return False
        
    # Convert to grayscale and get mean brightness
    # 0 = black, 255 = white
    # If mean > (255 * threshold), it's mostly white paper
    stat = ImageOps.grayscale(tile).getdata()
    avg = sum(stat) / len(stat)
    
    return avg < (255 * threshold)

def get_adaptive_tiles(image: Image.Image, tile_size=768, overlap=150):
    """
    Generates overlapping tiles using a sliding window.
    Returns: List of dicts { 'image': PIL.Image, 'x': int, 'y': int, 'w': int, 'h': int }
    """
    w, h = image.size
    stride = tile_size - overlap
    
    tiles = []
    
    # Calculate number of tiles needed in each dimension
    nx = math.ceil((w - overlap) / stride) if w > tile_size else 1
    ny = math.ceil((h - overlap) / stride) if h > tile_size else 1
    
    for iy in range(ny):
        top = iy * stride
        # Adjust for last tile to ensure it's exactly tile_size and covers the edge
        if top + tile_size > h:
            top = max(0, h - tile_size)
            
        for ix in range(nx):
            left = ix * stride
            if left + tile_size > w:
                left = max(0, w - tile_size)
                
            box = (left, top, left + tile_size, top + tile_size)
            tile_img = image.crop(box)
            
            # Density filter: Skip tiles that are essentially empty background
            if is_tile_meaningful(tile_img):
                tiles.append({
                    "image": tile_img,
                    "x": left,
                    "y": top,
                    "w": tile_size,
                    "h": tile_size
                })
                
    return tiles

def get_target_crop(image: Image.Image, bbox_pct: list[float], crop_size=768):
    """
    Given a [x0, y0, x1, y1] in percentages, returns a crop of crop_size x crop_size 
    centered on that region. Returns (PIL.Image, offset_x, offset_y).
    """
    w, h = image.size
    
    # Calculate center in pixels
    cx = ((bbox_pct[0] + bbox_pct[2]) / 200.0) * w
    cy = ((bbox_pct[1] + bbox_pct[3]) / 200.0) * h
    
    # Calculate crop box
    left = max(0, cx - crop_size / 2)
    top  = max(0, cy - crop_size / 2)
    
    # Adjust if we hit right/bottom edges
    if left + crop_size > w: left = max(0, w - crop_size)
    if top + crop_size > h: top = max(0, h - crop_size)
    
    crop_img = image.crop((left, top, left + crop_size, top + crop_size))
    return crop_img, int(left), int(top)

def draw_markups_on_image(image_path: str, markup_items: list[dict], output_path: str):
    """
    Draws bounding boxes, leader lines, and descriptive labels on an image.
    markup_items: list of { 'id': 'F-001', 'bbox_pct': [x0,y0,x1,y1], 'rule_id': '...' }
    """
    try:
        with Image.open(image_path).convert("RGB") as img:
            draw = ImageDraw.Draw(img)
            w, h = img.size
            
            # --- PROPORTIONAL SCALING ENGINE ---
            s = min(w, h) / 1000.0  # Base scale factor (1.0 for a 1000px image)
            line_width = max(3, int(4 * s))
            
            # Robust Font Loading (Windows & Linux Fallbacks)
            font_size = max(18, int(26 * s))
            font_id = None
            font_paths = ["arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
            
            for path in font_paths:
                try:
                    font_id = ImageFont.truetype(path, size=font_size)
                    if font_id: break
                except: continue
            
            if not font_id:
                # Final fallback for PIL version supporting size in load_default
                try: font_id = ImageFont.load_default(size=font_size)
                except: font_id = ImageFont.load_default()
            
            for item in markup_items:
                if not item.get("bbox_pct"): continue
                
                bx = item["bbox_pct"]
                # Convert % to absolute pixels
                x0 = (bx[0] / 100.0) * w
                y0 = (bx[1] / 100.0) * h
                x1 = (bx[2] / 100.0) * w
                y1 = (bx[3] / 100.0) * h
                
                # Draw bounding box with proportional padding
                pad = int(min(w, h) * 0.012) 
                draw.rectangle([x0 - pad, y0 - pad, x1 + pad, y1 + pad], outline="#ef4444", width=line_width)
                
                # ── LEADER LINE & LABEL ──────────────────────────────────
                cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
                
                # Proportional offsets for leader lines
                dx = int(w * 0.12) if cx < w * 0.7 else -int(w * 0.12)
                dy = -int(h * 0.08) if cy > h * 0.4 else int(h * 0.08)
                
                elbow_x, elbow_y = cx + dx, cy + dy
                start_x = x1 if dx > 0 else x0
                
                # 1. Draw elbow leader line
                draw.line([(start_x, cy), (elbow_x, cy), (elbow_x, elbow_y)], fill="#ef4444", width=line_width)
                
                # 2. Prepare Label
                fid = item.get("id", "F-???")
                rid = item.get("rule_id", "UNCATEGORIZED")
                full_label = f"[{fid}] {rid}"

                # Calculate label box size
                try:
                    left, top, right, bottom = draw.textbbox((0, 0), full_label, font=font_id)
                    tw, th = right - left, bottom - top
                except AttributeError:
                    tw, th = draw.textsize(full_label, font=font_id)

                # Scaled Padding and Radius
                ph, pv = int(12 * s), int(8 * s)
                radius = int(8 * s)
                
                if dx > 0:
                    lx0 = elbow_x
                else:
                    lx0 = elbow_x - tw - (ph * 2)
                    
                ly0 = elbow_y - (th / 2) - pv
                lx1, ly1 = lx0 + tw + (ph * 2), ly0 + th + (pv * 2)
                
                # 3. Draw rounded label background
                border_w = max(1, int(2 * s))
                try:
                    draw.rounded_rectangle([lx0, ly0, lx1, ly1], radius=radius, fill="white", outline="#ef4444", width=border_w)
                except AttributeError:
                    draw.rectangle([lx0, ly0, lx1, ly1], fill="white", outline="#ef4444", width=border_w)
                
                # 4. Draw text
                draw.text((lx0 + ph, ly0 + pv), full_label, fill="#ef4444", font=font_id)
                
            img.save(output_path)
            return True
    except Exception as e:
        print(f"Error drawing markups: {e}")
        return False
