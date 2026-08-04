"""
OCR Tool Plugin for REX
Extracts text from images, screenshots, and scanned documents.
Supports Tesseract OCR with multi-language detection and format conversion.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional
from datetime import datetime
from core.error_handler import log_error

try:
    import ctypes
    from ctypes import wintypes
except ImportError:
    ctypes = None
    wintypes = None

try:
    from PIL import Image, ImageFilter, ImageEnhance
except ImportError:
    Image = None

BASE_DIR = Path(__file__).parent.parent
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"


def _get_tesseract_path() -> str:
    """Find Tesseract executable path."""
    # Check common Windows paths
    common_paths = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        os.path.expanduser(r"~\AppData\Local\Tesseract-OCR\tesseract.exe"),
    ]
    
    for path in common_paths:
        if os.path.exists(path):
            return path
    
    # Check PATH
    try:
        result = subprocess.run(
            ["where", "tesseract"] if os.name == "nt" else ["which", "tesseract"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return result.stdout.strip().split('\n')[0]
    except Exception as _e:
        log_error(_e, context="actions.ocr_tool", severity="warning")
    
    return "tesseract"  # Assume it's in PATH


def _preprocess_image(image_path: str, enhance: bool = True) -> str:
    """Preprocess image for better OCR accuracy."""
    if Image is None:
        return image_path
    
    try:
        img = Image.open(image_path)
        
        # Convert to grayscale
        if img.mode != 'L':
            img = img.convert('L')
        
        if enhance:
            # Enhance contrast
            enhancer = ImageEnhance.Contrast(img)
            img = enhancer.enhance(1.5)
            
            # Sharpen
            img = img.filter(ImageFilter.SHARPEN)
            
            # Denoise slightly
            img = img.filter(ImageFilter.MedianFilter(size=1))
        
        # Save preprocessed image
        preprocessed_path = tempfile.mktemp(suffix='.png')
        img.save(preprocessed_path, 'PNG')
        return preprocessed_path
        
    except Exception as e:
        print(f"[OCR] Preprocessing failed: {e}")
        return image_path


def extract_text_from_image(image_path: str, language: str = "eng", preprocess: bool = True) -> str:
    """
    Extract text from an image using Tesseract OCR.
    
    Args:
        image_path: Path to the image file
        language: Tesseract language code (eng, spa, fra, deu, etc.)
        preprocess: Whether to preprocess image for better accuracy
    """
    path = Path(image_path)
    
    if not path.exists():
        return f"❌ File not found: {image_path}"
    
    supported = {'.png', '.jpg', '.jpeg', '.tiff', '.tif', '.bmp', '.gif', '.webp'}
    if path.suffix.lower() not in supported:
        return f"❌ Unsupported format: {path.suffix}\nSupported: {', '.join(supported)}"
    
    # Preprocess if requested
    working_path = image_path
    if preprocess:
        working_path = _preprocess_image(image_path)
    
    try:
        tesseract = _get_tesseract_path()
        
        # Run Tesseract
        result = subprocess.run(
            [tesseract, working_path, "stdout", "-l", language],
            capture_output=True, text=True, timeout=60
        )
        
        text = result.stdout.strip()
        
        if not text:
            if result.stderr:
                return f"⚠️ OCR produced no text. Tesseract error:\n{result.stderr[:500]}"
            return "⚠️ OCR produced no text. The image may be blank or too low quality."
        
        # Format output
        output = f"📄 OCR Result: {path.name}\n"
        output += "=" * 50 + "\n\n"
        output += f"📏 Characters extracted: {len(text)}\n"
        output += f"📝 Words extracted: {len(text.split())}\n"
        output += f"🌐 Language: {language}\n\n"
        output += "─" * 50 + "\n"
        output += text
        output += "\n" + "─" * 50
        
        return output
        
    except subprocess.TimeoutExpired:
        return "❌ OCR timed out. The image may be too large or complex."
    except FileNotFoundError:
        return (
            "❌ Tesseract not found. Install it:\n"
            "  Windows: https://github.com/UB-Mannheim/tesseract/wiki\n"
            "  macOS: brew install tesseract\n"
            "  Linux: sudo apt install tesseract-ocr"
        )
    except Exception as e:
        return f"❌ OCR failed: {e}"
    finally:
        # Cleanup preprocessed temp file
        if preprocess and working_path != image_path:
            try:
                os.unlink(working_path)
            except Exception as _e:
                log_error(_e, context="actions.ocr_tool", severity="warning")


def extract_text_from_pdf(pdf_path: str, language: str = "eng") -> str:
    """
    Extract text from a PDF file using OCR (for scanned PDFs).
    """
    path = Path(pdf_path)
    
    if not path.exists():
        return f"❌ File not found: {pdf_path}"
    
    if path.suffix.lower() != '.pdf':
        return f"❌ Not a PDF file: {pdf_path}"
    
    try:
        # Try PyPDF2 first (for text-based PDFs)
        import PyPDF2
        
        text_parts = []
        with open(path, 'rb') as f:
            reader = PyPDF2.PdfReader(f)
            for page in reader.pages:
                page_text = page.extract_text()
                if page_text and page_text.strip():
                    text_parts.append(page_text.strip())
        
        if text_parts:
            full_text = "\n\n".join(text_parts)
            output = f"📄 PDF Text Extraction: {path.name}\n"
            output += "=" * 50 + "\n\n"
            output += f"📏 Characters extracted: {len(full_text)}\n"
            output += f"📝 Words extracted: {len(full_text.split())}\n"
            output += f"📑 Pages: {len(reader.pages)}\n\n"
            output += "─" * 50 + "\n"
            output += full_text
            output += "\n" + "─" * 50
            return output
        
        # Fall back to OCR for scanned PDFs
        return _ocr_pdf_scanned(pdf_path, language)
        
    except ImportError:
        return _ocr_pdf_scanned(pdf_path, language)
    except Exception as e:
        return f"❌ PDF extraction failed: {e}"


def _ocr_pdf_scanned(pdf_path: str, language: str) -> str:
    """OCR a scanned PDF by converting pages to images."""
    try:
        import fitz  # PyMuPDF
        
        doc = fitz.open(pdf_path)
        all_text = []
        
        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            pix = page.get_pixmap(dpi=300)
            
            # Save as temp image
            img_path = tempfile.mktemp(suffix='.png')
            pix.save(img_path)
            
            # OCR the image
            text = extract_text_from_image(img_path, language, preprocess=False)
            # Clean up the formatted output to get raw text
            lines = text.split('\n')
            raw_lines = []
            in_content = False
            for line in lines:
                if '─' * 10 in line:
                    in_content = not in_content
                    continue
                if in_content:
                    raw_lines.append(line)
            
            if raw_lines:
                all_text.append(f"--- Page {page_num + 1} ---\n" + "\n".join(raw_lines))
            
            # Cleanup
            try:
                os.unlink(img_path)
            except Exception as _e:
                log_error(_e, context="actions.ocr_tool", severity="warning")
        
        doc.close()
        
        if all_text:
            full_text = "\n\n".join(all_text)
            output = f"📄 OCR PDF Extraction: {Path(pdf_path).name}\n"
            output += "=" * 50 + "\n\n"
            output += f"📏 Characters extracted: {len(full_text)}\n"
            output += f"📝 Words extracted: {len(full_text.split())}\n"
            output += f"📑 Pages OCR'd: {len(all_text)}\n\n"
            output += "─" * 50 + "\n"
            output += full_text
            output += "\n" + "─" * 50
            return output
        
        return "⚠️ No text could be extracted from the PDF."
        
    except ImportError:
        return (
            "❌ PyMuPDF not installed for scanned PDF OCR.\n"
            "Install with: pip install PyMuPDF\n\n"
            "For text-based PDFs, try the file_processor tool instead."
        )
    except Exception as e:
        return f"❌ PDF OCR failed: {e}"


def detect_language(image_path: str) -> str:
    """Detect the language of text in an image."""
    try:
        tesseract = _get_tesseract_path()
        
        # Use Tesseract's OSD (Orientation and Script Detection)
        result = subprocess.run(
            [tesseract, image_path, "stdout", "--psm", "0"],
            capture_output=True, text=True, timeout=30
        )
        
        output = f"🌐 Language Detection: {Path(image_path).name}\n"
        output += "=" * 50 + "\n\n"
        
        if result.returncode == 0:
            # Parse OSD output
            for line in result.stdout.split('\n'):
                if 'Script:' in line:
                    output += f"📝 Script: {line.split(':')[-1].strip()}\n"
                elif 'Orientation:' in line:
                    output += f"🔄 Orientation: {line.split(':')[-1].strip()}\n"
                elif 'Rotate:' in line:
                    output += f"🔃 Rotation: {line.split(':')[-1].strip()}°\n"
                elif 'Lang' in line and 'conf' in line.lower():
                    output += f"🌐 {line.strip()}\n"
        else:
            output += "⚠️ Could not detect language. Try specifying a language code.\n"
            output += "Common codes: eng, spa, fra, deu, ita, por, rus, jpn, kor, chi_sim"
        
        return output
        
    except FileNotFoundError:
        return "❌ Tesseract not found. Install it to use language detection."
    except Exception as e:
        return f"❌ Language detection failed: {e}"


def batch_ocr(directory: str, language: str = "eng", extension: str = ".png") -> str:
    """
    Batch OCR all images in a directory.
    """
    dir_path = Path(directory)
    
    if not dir_path.exists():
        return f"❌ Directory not found: {directory}"
    
    # Find all image files
    extensions = {'.png', '.jpg', '.jpeg', '.tiff', '.tif', '.bmp', '.gif', '.webp'}
    if extension:
        extensions = {extension if extension.startswith('.') else f'.{extension}'}
    
    files = [f for f in dir_path.iterdir() if f.suffix.lower() in extensions]
    
    if not files:
        return f"📭 No image files found in {directory}"
    
    output = f"📁 Batch OCR: {dir_path.name}\n"
    output += "=" * 50 + "\n\n"
    output += f"📊 Found {len(files)} images to process\n\n"
    
    all_results = []
    
    for i, img_file in enumerate(sorted(files), 1):
        output += f"⏳ Processing {i}/{len(files)}: {img_file.name}...\n"
        
        result = extract_text_from_image(str(img_file), language, preprocess=True)
        
        # Extract just the text content
        lines = result.split('\n')
        text_lines = []
        in_content = False
        for line in lines:
            if '─' * 10 in line:
                in_content = not in_content
                continue
            if in_content:
                text_lines.append(line)
        
        if text_lines:
            all_results.append(f"📄 {img_file.name}:\n" + "\n".join(text_lines))
            output += f"   ✅ Extracted {len(' '.join(text_lines).split())} words\n"
        else:
            output += f"   ⚠️ No text found\n"
    
    if all_results:
        output += f"\n{'=' * 50}\n"
        output += "📝 ALL EXTRACTED TEXT\n"
        output += "=" * 50 + "\n\n"
        output += "\n\n".join(all_results)
    
    return output


def extract_text_from_screenshot() -> str:
    """
    Capture screen and extract text using OCR.
    """
    try:
        screenshot_path = tempfile.mktemp(suffix='.png')
        
        if sys.platform == 'win32':
            if ctypes is None:
                return "❌ ctypes not available for screenshot capture."
            
            user32 = ctypes.windll.user32
            gdi32 = ctypes.windll.gdi32
            
            screen_width = user32.GetSystemMetrics(0)
            screen_height = user32.GetSystemMetrics(1)
            
            # Create device contexts
            hdesktop = user32.GetDC(0)
            hdc = gdi32.CreateCompatibleDC(hdesktop)
            hbmp = gdi32.CreateCompatibleBitmap(hdesktop, screen_width, screen_height)
            gdi32.SelectObject(hdc, hbmp)
            
            # Copy screen to bitmap
            gdi32.BitBlt(hdc, 0, 0, screen_width, screen_height, hdesktop, 0, 0, 0x00CC0020)
            
            # Save as PNG using PIL
            if Image:
                from PIL import ImageWin
                img = ImageWin.BitmapFromDC(hdc, screen_width, screen_height)
                pil_img = Image.frombytes('RGB', (screen_width, screen_height), img, 'raw', 'BGRX')
                pil_img.save(screenshot_path, 'PNG')
            else:
                return "❌ PIL not installed. Cannot capture screenshot."
            
            # Cleanup
            gdi32.DeleteObject(hbmp)
            gdi32.DeleteDC(hdc)
            user32.ReleaseDC(0, hdesktop)
            
        else:
            # Linux/Mac: use scrot or similar
            subprocess.run(["scrot", screenshot_path], timeout=10)
        
        # OCR the screenshot
        result = extract_text_from_image(screenshot_path, preprocess=True)
        
        # Cleanup
        try:
            os.unlink(screenshot_path)
        except Exception as _e:
            log_error(_e, context="actions.ocr_tool", severity="warning")
        
        return result
        
    except Exception as e:
        return f"❌ Screenshot OCR failed: {e}"


def save_text_output(text: str, output_path: str = None) -> str:
    """Save extracted text to a file."""
    if not output_path:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = str(BASE_DIR / "outputs" / f"ocr_{timestamp}.txt")
    
    output_dir = Path(output_path).parent
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Clean the text (remove formatting markers)
    clean_text = text
    clean_text = clean_text.replace("─" * 50, "")
    clean_text = clean_text.replace("═" * 50, "")
    
    # Remove header lines
    lines = clean_text.split('\n')
    content_lines = []
    skip_header = True
    for line in lines:
        if skip_header and (not line.strip() or line.startswith('📄') or line.startswith('📏') or 
                           line.startswith('📝') or line.startswith('🌐') or line.startswith('📑')):
            continue
        skip_header = False
        content_lines.append(line)
    
    clean_text = '\n'.join(content_lines).strip()
    
    try:
        Path(output_path).write_text(clean_text, encoding='utf-8')
        return f"✅ Text saved to: {output_path}"
    except Exception as e:
        return f"❌ Failed to save: {e}"


# ═══════════════════════════════════════════════════════════════════
# Tool Definitions for Registration
# ═══════════════════════════════════════════════════════════════════

OCR_TOOLS = [
    {
        "name": "ocr_extract",
        "description": (
            "Extracts text from an image using OCR (Optical Character Recognition). "
            "Supports PNG, JPG, TIFF, BMP, GIF, WebP formats. "
            "Uses Tesseract OCR with optional image preprocessing for better accuracy."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "image_path": {"type": "STRING", "description": "Path to the image file"},
                "language": {"type": "STRING", "description": "Tesseract language code (default: eng). Examples: eng, spa, fra, deu, jpn, kor, chi_sim"},
                "preprocess": {"type": "BOOLEAN", "description": "Preprocess image for better accuracy (default: true)"}
            },
            "required": ["image_path"]
        }
    },
    {
        "name": "ocr_pdf",
        "description": (
            "Extracts text from a PDF file. Works with both text-based PDFs "
            "and scanned PDFs (via OCR). For scanned PDFs, converts pages to images first."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "pdf_path": {"type": "STRING", "description": "Path to the PDF file"},
                "language": {"type": "STRING", "description": "OCR language code (default: eng)"}
            },
            "required": ["pdf_path"]
        }
    },
    {
        "name": "ocr_detect_language",
        "description": (
            "Detects the language and script of text in an image. "
            "Useful for determining which language code to use for OCR."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "image_path": {"type": "STRING", "description": "Path to the image file"}
            },
            "required": ["image_path"]
        }
    },
    {
        "name": "ocr_batch",
        "description": (
            "Batch OCR all images in a directory. "
            "Processes PNG, JPG, TIFF, BMP, GIF, WebP files."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "directory": {"type": "STRING", "description": "Path to directory containing images"},
                "language": {"type": "STRING", "description": "OCR language code (default: eng)"},
                "extension": {"type": "STRING", "description": "Filter by extension (e.g., '.png', '.jpg')"}
            },
            "required": ["directory"]
        }
    },
    {
        "name": "ocr_screenshot",
        "description": (
            "Captures the current screen and extracts text using OCR. "
            "Useful for reading text from any application on screen."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "ocr_save",
        "description": (
            "Saves extracted OCR text to a file. "
            "Use after ocr_extract to persist the results."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "text": {"type": "STRING", "description": "Text content to save"},
                "output_path": {"type": "STRING", "description": "Output file path (optional, auto-generated if empty)"}
            },
            "required": ["text"]
        }
    },
]


def handle_ocr_tool(tool_name: str, parameters: dict, speak=None) -> str:
    """Route OCR tool calls to appropriate functions."""
    try:
        if tool_name == "ocr_extract":
            return extract_text_from_image(
                image_path=parameters.get("image_path", ""),
                language=parameters.get("language", "eng"),
                preprocess=parameters.get("preprocess", True)
            )
        elif tool_name == "ocr_pdf":
            return extract_text_from_pdf(
                pdf_path=parameters.get("pdf_path", ""),
                language=parameters.get("language", "eng")
            )
        elif tool_name == "ocr_detect_language":
            return detect_language(parameters.get("image_path", ""))
        elif tool_name == "ocr_batch":
            return batch_ocr(
                directory=parameters.get("directory", ""),
                language=parameters.get("language", "eng"),
                extension=parameters.get("extension", ".png")
            )
        elif tool_name == "ocr_screenshot":
            return extract_text_from_screenshot()
        elif tool_name == "ocr_save":
            return save_text_output(
                text=parameters.get("text", ""),
                output_path=parameters.get("output_path")
            )
        else:
            return f"❌ Unknown OCR tool: {tool_name}"
    except Exception as e:
        return f"❌ OCR tool error: {e}"
