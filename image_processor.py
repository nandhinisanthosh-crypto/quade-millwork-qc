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
            
            # Use a slightly thicker line for visibility
            line_width = max(3, int(min(w, h) / 400))
            
            # Try to load a clean font, fallback to default
            try:
                # Common Windows path for Arial
                font_id = ImageFont.truetype("arial.ttf", size=max(20, int(min(w, h) / 60)))
                font_rule = ImageFont.truetype("arial.ttf", size=max(14, int(min(w, h) / 85)))
            except:
                font_id = ImageFont.load_default()
                font_rule = ImageFont.load_default()
            
            for item in markup_items:
                if not item.get("bbox_pct"): continue
                
                bx = item["bbox_pct"]
                # Convert % to absolute pixels
                x0 = (bx[0] / 100.0) * w
                y0 = (bx[1] / 100.0) * h
                x1 = (bx[2] / 100.0) * w
                y1 = (bx[3] / 100.0) * h
                
                # Draw bounding box
                draw.rectangle([x0, y0, x1, y1], outline="#ef4444", width=line_width)
                
                # ── LEADER LINE & LABEL ──────────────────────────────────
                # Determine placement: try to avoid edges
                cx = (x0 + x1) / 2
                cy = (y0 + y1) / 2
                
                # Determine placement: try to avoid edges, using proportional offsets
                dx = int(w * 0.12) if cx < w * 0.7 else -int(w * 0.12)
                dy = -int(h * 0.1) if cy > h * 0.4 else int(h * 0.1)
                
                elbow_x = cx + dx
                elbow_y = cy + dy
                
                # Draw elbow leader line (Starting from the box edge for a clean look)
                start_x = x1 if dx > 0 else x0
                draw.line([(start_x, cy), (elbow_x, cy), (elbow_x, elbow_y)], fill="#ef4444", width=line_width)
                
                # Standardized Labeling: [F-001] Rule-ID
                fid = item.get("id", "F-???")
                rid = item.get("rule_id", "UNCATEGORIZED")
                full_label = f"[{fid}] {rid}"

                # Font sizing based on image resolution
                font_size = max(16, int(min(w, h) / 48))
                try:
                    # Try to load a clean font, fallback to default
                    font_id = ImageFont.truetype("arial.ttf", font_size)
                except:
                    font_id = ImageFont.load_default()

                # Calculate rounded label box
                try:
                    # PIL 10.0.0+ uses getbbox/getmask
                    left, top, right, bottom = draw.textbbox((0, 0), full_label, font=font_id)
                    tw, th = right - left, bottom - top
                except AttributeError:
                    # Older PIL
                    tw, th = draw.textsize(full_label, font=font_id)

                padding_h, padding_v = 12, 8
                
                # Dynamic positioning based on dx
                if dx > 0:
                    lx0 = elbow_x
                else:
                    lx0 = elbow_x - tw - (padding_h * 2)
                    
                ly0 = elbow_y - (th / 2) - padding_v
                lx1 = lx0 + tw + (padding_h * 2)
                ly1 = ly0 + th + (padding_v * 2)
                
                # Draw rounded label background (White with Red Border)
                try:
                    draw.rounded_rectangle([lx0, ly0, lx1, ly1], radius=8, fill="white", outline="#ef4444", width=2)
                except AttributeError:
                    # Fallback for older PIL
                    draw.rectangle([lx0, ly0, lx1, ly1], fill="white", outline="#ef4444", width=2)
                
                # Draw text (Red)
                draw.text((lx0 + padding_h, ly0 + padding_v), full_label, fill="#ef4444", font=font_id)
                
            img.save(output_path)
            return True
    except Exception as e:
        print(f"Error drawing markups: {e}")
        return False
