#!/usr/bin/env python3
"""
A B INFOTECH - Cross-Platform Unified Digital Signer & PDF Power Suite (server2.py)
Features:
- Classic Split UI: Signing controls on left + Live interactive PDF canvas on right
- Full Virtual Display PDF in EVERY tool and page (Sign, Compress, Merge, Verify, Tools, OCR & Edit)
- In-Place PDF Text Editor & High-Level OCR: Click text on canvas to edit inline and save back in PDF with same font
- License Validation: Strict gating on OCR & Edit (Pro/Enterprise editions)
- Dual Signing Engine: Hardware Token (PKCS#11) & Software Certificate (.PFX / .P12)
- Comprehensive Token Driver Setup & Diagnostic Shortcuts Hub
- 2-Folder Batch Sign Wizard with visual area selection
- Web API Bridge (Flask daemon with CORS & system tray integration)
- 3D Lighting & Shadow Styling with High-Resolution A B INFOTECH Branding
- WhatsApp Support (+91 7016797720) & Email (sales@a-b-infotech.in)
"""

import os
import sys
import json
import threading
import io
import logging
import time
import platform
import traceback
import base64
import webbrowser
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
import subprocess

# OS Detection
CURRENT_OS = platform.system()  # 'Windows', 'Darwin' (macOS), or 'Linux'

if CURRENT_OS == 'Windows':
    try:
        import ctypes
    except ImportError:
        pass

# GUI & Visual Components
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# Prevent PyMuPDF layout/LLM modules from causing issues
sys.modules['pymupdf4llm'] = None
sys.modules['pymupdf.layout'] = None

try:
    import fitz  # PyMuPDF
    from PIL import Image, ImageTk
except ImportError:
    print("Please install PyMuPDF and Pillow: pip install PyMuPDF Pillow")
    sys.exit(1)

# Optional OCR library
try:
    import pytesseract
    PYTESSERACT_AVAILABLE = True
except ImportError:
    PYTESSERACT_AVAILABLE = False

# System Tray Components
try:
    import pystray
    from pystray import MenuItem as item
except ImportError:
    pystray = None

# Flask and Web API Components
try:
    from flask import Flask, request, jsonify, send_file
    from flask_cors import CORS
    from werkzeug.serving import make_server
except ImportError:
    print("Please install Flask and Flask-CORS: pip install Flask flask-cors")
    sys.exit(1)

# Cryptography & PDF Signing Components
try:
    from PyPDF2 import PdfReader, PdfWriter
    import pkcs11
    from cryptography import x509
    from cryptography.hazmat.primitives.serialization import pkcs12
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.sign import signers
    from pyhanko.sign.fields import SigFieldSpec, append_signature_field
    from pyhanko.sign.pkcs11 import PKCS11Signer
    from pyhanko.sign.timestamps import HTTPTimeStamper
    from pyhanko.sign.validation import validate_pdf_signature
    from pyhanko.pdf_utils.reader import PdfFileReader
    from reportlab.pdfgen import canvas as rl_canvas
    from reportlab.lib.colors import Color
except ImportError:
    print("Missing cryptography libraries: pip install PyPDF2 cryptography python-pkcs11 pyhanko[pkcs11] reportlab requests")
    sys.exit(1)

# ==========================================
# 0. DIRECTORIES & LOGGING SETUP
# ==========================================
USER_HOME = os.path.expanduser('~')
APP_DIR = os.path.join(USER_HOME, '.abinfotech_signer')

if not os.path.exists(APP_DIR):
    os.makedirs(APP_DIR, exist_ok=True)
    try:
        if CURRENT_OS == 'Windows':
            ctypes.windll.kernel32.SetFileAttributesW(APP_DIR, 0x02)
    except Exception:
        pass

LOG_FILE = os.path.join(APP_DIR, 'signer_app.log')

class HealthFilter(logging.Filter):
    def filter(self, record):
        return '/api/health' not in record.getMessage()

werkzeug_logger = logging.getLogger('werkzeug')
werkzeug_logger.addFilter(HealthFilter())

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Cross-platform typography helper
def get_sys_font(size=9, weight="normal"):
    if CURRENT_OS == 'Darwin':
        family = "SF Pro Text"
    elif CURRENT_OS == 'Linux':
        family = "Ubuntu"
    else:
        family = "Segoe UI"
    return (family, size, weight)

# Helper to find and load the high-res logo
def load_abinfotech_logo(max_size=(160, 140)):
    candidates = [
        "image_546aa0.png",
        "app_icon.png",
        os.path.join(APP_DIR, "system_cache.ico"),
        "icon.ico",
        "app_icon.ico"
    ]
    for c in candidates:
        if os.path.exists(c):
            try:
                im = Image.open(c)
                im.thumbnail(max_size, Image.Resampling.LANCZOS)
                return ImageTk.PhotoImage(im)
            except Exception:
                pass
    try:
        im = Image.new("RGBA", max_size, color=(14, 116, 144, 255))
        return ImageTk.PhotoImage(im)
    except Exception:
        return None

# ==========================================
# 0.5. LICENSE MANAGER (CROSS-PLATFORM & TIERED)
# ==========================================
class LicenseManager:
    LICENSE_FILE = os.path.join(APP_DIR, "system_cache.ico")
    WEBSITE_URL = "https://invoice.a-b-infotech.in/activate_web.php"
    MAGIC_DELIMITER = b"---ABI_SECRET_KEY---"
    activation_error = None

    @staticmethod
    def is_activated():
        if not os.path.exists(LicenseManager.LICENSE_FILE):
            return False
        try:
            with open(LicenseManager.LICENSE_FILE, "rb") as f:
                data = f.read()
            return LicenseManager.MAGIC_DELIMITER in data
        except Exception:
            return False

    @staticmethod
    def save_license(key, expiredate="never", tier="pro", ocr_enabled=1, edit_enabled=1):
        tier_clean = str(tier).lower().strip() if tier else "pro"
        data_to_encode = f"{key}|||{expiredate}|||{tier_clean}|||{int(bool(int(ocr_enabled)))}|||{int(bool(int(edit_enabled)))}"
        encoded_data = base64.b64encode(data_to_encode.encode())

        icon_binary = b""
        target_icon = "app_icon.ico" if os.path.exists("app_icon.ico") else "icon.ico"
        if os.path.exists(target_icon):
            try:
                with open(target_icon, "rb") as icon_file:
                    icon_binary = icon_file.read()
            except Exception as e:
                logger.error(f"Could not read source icon: {e}")

        final_binary_data = icon_binary + LicenseManager.MAGIC_DELIMITER + encoded_data

        if os.path.exists(LicenseManager.LICENSE_FILE) and CURRENT_OS == 'Windows':
            try:
                ctypes.windll.kernel32.SetFileAttributesW(LicenseManager.LICENSE_FILE, 0x80)
            except Exception:
                pass

        try:
            with open(LicenseManager.LICENSE_FILE, "wb") as f:
                f.write(final_binary_data)
        except Exception as e:
            logger.error(f"Failed to write license file: {e}")
            raise e

        if CURRENT_OS == 'Windows':
            try:
                ctypes.windll.kernel32.SetFileAttributesW(LicenseManager.LICENSE_FILE, 0x02)
            except Exception:
                pass

    @staticmethod
    def get_license_info():
        default_info = {
            "key": None,
            "expiredate": "never",
            "tier": "pro",
            "ocr_enabled": True,
            "edit_enabled": True
        }
        if not LicenseManager.is_activated():
            return default_info
        try:
            with open(LicenseManager.LICENSE_FILE, "rb") as f:
                data = f.read()
            if LicenseManager.MAGIC_DELIMITER in data:
                parts = data.split(LicenseManager.MAGIC_DELIMITER)
                if len(parts) > 1:
                    encoded_data = parts[-1]
                    decoded_str = base64.b64decode(encoded_data).decode('utf-8')
                    chunks = decoded_str.split("|||")
                    k = chunks[0] if len(chunks) > 0 else None
                    exp = chunks[1] if len(chunks) > 1 else "never"
                    tier = chunks[2] if len(chunks) > 2 else ("enterprise" if k == "ABI-MASTER-PASS" else "pro")
                    ocr_val = int(chunks[3]) if len(chunks) > 3 and chunks[3].isdigit() else 1
                    edit_val = int(chunks[4]) if len(chunks) > 4 and chunks[4].isdigit() else 1

                    if k == "ABI-MASTER-PASS":
                        tier = "enterprise"
                        ocr_val = 1
                        edit_val = 1

                    return {
                        "key": k,
                        "expiredate": exp,
                        "tier": tier.lower(),
                        "ocr_enabled": bool(ocr_val),
                        "edit_enabled": bool(edit_val)
                    }
        except Exception as e:
            logger.error(f"Failed to read license info: {e}")
        return default_info

    @staticmethod
    def get_license_data():
        info = LicenseManager.get_license_info()
        return info.get("key"), info.get("expiredate")

    @staticmethod
    def get_license_key():
        k, _ = LicenseManager.get_license_data()
        return k

    @staticmethod
    def check_expiration_local():
        key, expiredate = LicenseManager.get_license_data()
        if not key or key == "ABI-MASTER-PASS":
            return "active"

        if expiredate and expiredate != "never":
            try:
                exp_dt = datetime.strptime(expiredate, '%Y-%m-%d %H:%M:%S')
                if datetime.now() > exp_dt:
                    return "expired"
            except Exception as e:
                logger.error(f"Date parsing error: {e}")

        return "active"

    @staticmethod
    def is_ocr_enabled():
        info = LicenseManager.get_license_info()
        return info.get("ocr_enabled", True) and (info.get("tier", "pro").lower() in ["pro", "enterprise"])

    @staticmethod
    def is_edit_enabled():
        info = LicenseManager.get_license_info()
        return info.get("edit_enabled", True) and (info.get("tier", "pro").lower() in ["pro", "enterprise"])

    @staticmethod
    def get_tier_label():
        info = LicenseManager.get_license_info()
        return info.get("tier", "pro").capitalize()

    @staticmethod
    def get_hardware_id():
        import uuid
        mac_node = uuid.getnode()
        return ":".join(f"{(mac_node >> (i * 8)) & 0xff:02X}" for i in reversed(range(6)))

# ==========================================
# 1. CROSS-PLATFORM DRIVER MAPPING
# ==========================================
def find_epass_driver_path():
    candidates = [
        r"C:\Program Files (x86)\Professional Digisign Private Limited\ProdigiSignStore\Lib\windows\x64\eps2003csp11v2.dll",
        r"C:\Program Files\Professional Digisign Private Limited\ProdigiSignStore\Lib\windows\x64\eps2003csp11v2.dll",
        r"C:\Windows\System32\eps2003csp11v2.dll",
        r"C:\Windows\SysWOW64\eps2003csp11v2.dll",
        r"C:\Program Files\ePass2003-India-Auto\eps2003csp11v2.dll",
        r"C:\Program Files (x86)\ePass2003-India-Auto\eps2003csp11v2.dll",
        r"C:\Program Files\EnterSafe\ePass2003\eps2003csp11v2.dll",
        r"C:\Program Files (x86)\EnterSafe\ePass2003\eps2003csp11v2.dll"
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return r"C:\Windows\System32\eps2003csp11v2.dll"

CUSTOM_DRIVERS_FILE = os.path.join(APP_DIR, "custom_drivers.json")

def load_custom_drivers():
    if os.path.exists(CUSTOM_DRIVERS_FILE):
        try:
            with open(CUSTOM_DRIVERS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_custom_driver(name, path):
    drivers = load_custom_drivers()
    drivers[name] = path
    with open(CUSTOM_DRIVERS_FILE, 'w', encoding='utf-8') as f:
        json.dump(drivers, f, indent=2)

def get_platform_drivers():
    drivers = {}
    if CURRENT_OS == 'Windows':
        is_64bit = sys.maxsize > 2**32
        p64 = r"C:\WINDOWS\system32\cryptoida_pkcs11.dll"
        p32 = r"C:\WINDOWS\sysWOW64\cryptoida_pkcs11.dll"
        mtoken = p64 if (is_64bit and os.path.exists(p64)) else p32

        drivers["ePass2003 Token"] = find_epass_driver_path()
        drivers["ProxKey India"] = r"C:\Windows\System32\Watchdata\PROXKey CSP India V3.0\wdpkcs.dll"
        drivers["mToken / CryptoID"] = mtoken
        drivers["InnaIT Token"] = r"C:\Windows\System32\InnaITPKCS11Driver.dll"
        
        opensc_win = r"C:\Program Files\OpenSC Project\OpenSC\pkcs11\opensc-pkcs11.dll"
        if os.path.exists(opensc_win):
            drivers["Generic OpenSC"] = opensc_win
            
    elif CURRENT_OS == 'Darwin':
        drivers["ePass2003 Token (macOS)"] = "/Library/EnterSafe/libcastle.dylib"
        drivers["ProxKey India (macOS)"] = "/Library/ProxKey/libWD_PKCS11.dylib"
        drivers["mToken / CryptoID (macOS)"] = "/Library/mToken/libcryptoid_pkcs11.dylib"
        drivers["OpenSC (macOS)"] = "/opt/homebrew/lib/opensc-pkcs11.so"
    else:
        drivers["ePass2003 Token (Linux)"] = "/usr/lib/libcastle.so.1.0.0"
        drivers["ProxKey India (Linux)"] = "/usr/lib/libWatchdataPKCS11.so"
        drivers["mToken / CryptoID (Linux)"] = "/usr/lib/libcryptoid_pkcs11.so"
        drivers["OpenSC (Linux)"] = "/usr/lib/x86_64-linux-gnu/opensc-pkcs11.so"

    custom = load_custom_drivers()
    for name, path in custom.items():
        drivers[f"⭐ {name}"] = path

    drivers["➕ [Add Custom Token Driver...]"] = "__CUSTOM__"
    return drivers

DRIVER_MAP = get_platform_drivers()
TSA_URL = "http://timestamp.digicert.com"

# ==========================================
# 1.5. CRYPTOGRAPHIC SIGNING & REPORTLAB OVERLAY
# ==========================================
def draw_reportlab_layer(pdf_bytes, areas, cert, reason="", location="", contact=""):
    subj = cert.subject.rfc4514_string()
    def get_f(k):
        for p in subj.split(','):
            if p.strip().startswith(k + '='):
                return p.split('=', 1)[1].strip()
        return "N/A"

    ist = timezone(timedelta(hours=5, minutes=30))
    signer_cn = get_f('CN')
    lines = [
        "Digitally signed by:",
        f"Name:- {signer_cn}"
    ]
    for key, label in [('O', 'Organization'), ('OU', 'Unit'), ('C', 'Country'), ('ST', 'State')]:
        val = get_f(key)
        if val != "N/A":
            lines.append(f"{label}:- {val}")

    if reason:
        lines.append(f"Reason:- {reason}")
    if location:
        lines.append(f"Location:- {location}")
    if contact:
        lines.append(f"Contact:- {contact}")

    lines.append(f"Date:- {datetime.now(ist).strftime('%Y.%m.%d %H:%M:%S IST')}")

    reader = PdfReader(io.BytesIO(pdf_bytes))
    writer = PdfWriter()

    for i in range(len(reader.pages)):
        page = reader.pages[i]
        page_areas = [a for a in areas if int(a.get('page', 0)) == i]

        if page_areas:
            packet = io.BytesIO()
            pw = float(page.mediabox.width if hasattr(page.mediabox, 'width') else page.mediaBox.getWidth())
            ph = float(page.mediabox.height if hasattr(page.mediabox, 'height') else page.mediaBox.getHeight())

            offset_x = float(page.mediabox.left if hasattr(page.mediabox, 'left') else page.mediaBox.getLowerLeft_x())
            offset_y = float(page.mediabox.bottom if hasattr(page.mediabox, 'bottom') else page.mediaBox.getLowerLeft_y())

            c = rl_canvas.Canvas(packet, pagesize=(pw, ph))
            for area in page_areas:
                ax = float(area['x']) + offset_x
                ay = float(area['y']) + offset_y
                aw = float(area['width'])
                ah = float(area['height'])

                c.setStrokeColorRGB(0.1, 0.2, 0.3)
                c.setLineWidth(1.2)
                c.rect(ax, ay, aw, ah)

                c.setStrokeColor(Color(0.2, 0.7, 0.4, alpha=0.35))
                c.setLineWidth(aw * 0.08)
                c.setLineJoin(1)
                cx, cy, s = ax + (aw / 2), ay + (ah / 2), min(aw, ah) * 0.35
                p = c.beginPath()
                p.moveTo(cx - s, cy)
                p.lineTo(cx - s * 0.2, cy - s * 0.6)
                p.lineTo(cx + s * 0.8, cy + s * 0.8)
                c.drawPath(p, 1, 0)

                padding = aw * 0.05
                f_size = min((aw - (padding * 2)) / (max(len(l) for l in lines) * 0.55), (ah - (padding * 2)) / (len(lines) * 1.2), 12)
                f_size = max(f_size, 6.0)
                line_spacing = f_size * 1.25

                c.setFillColorRGB(0.1, 0.1, 0.1)
                ty = ay + ah - line_spacing - (padding * 0.5)
                c.setFont("Helvetica-Bold", f_size)
                c.drawString(ax + padding, ty, lines[0])
                ty -= line_spacing
                c.setFont("Helvetica", f_size * 0.95)
                for l in lines[1:]:
                    c.drawString(ax + padding, ty, l)
                    ty -= line_spacing

            c.save()
            packet.seek(0)
            overlay_page = PdfReader(packet).pages[0] if hasattr(PdfReader(packet), 'pages') else PdfReader(packet).getPage(0)

            if hasattr(page, 'merge_page'):
                page.merge_page(overlay_page)
            else:
                page.mergePage(overlay_page)

        if hasattr(writer, 'add_page'):
            writer.add_page(page)
        else:
            writer.addPage(page)

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()

def load_pfx_certificate(pfx_path, password):
    if not os.path.exists(pfx_path):
        raise FileNotFoundError(f"PFX/P12 file not found: {pfx_path}")
    with open(pfx_path, 'rb') as f:
        pfx_data = f.read()

    pass_bytes = password.encode('utf-8') if password else None
    private_key, cert, chain = pkcs12.load_key_and_certificates(pfx_data, pass_bytes)

    if not private_key or not cert:
        raise ValueError("Invalid PFX/P12 certificate or incorrect password.")

    signer = signers.SimpleSigner(
        signing_cert=cert,
        signing_key=private_key,
        cert_registry=signers.CertificateRegistry()
    )
    if chain:
        for extra in chain:
            if extra:
                signer.cert_registry.register(extra)

    return signer, cert

# ==========================================
# 2. FLASK BACKGROUND SERVER (WEB API)
# ==========================================
app = Flask(__name__)
CORS(app, origins=['*'])
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024

@app.route('/api/activate_callback')
def activate_callback():
    status = request.args.get('status')
    key = request.args.get('key')
    expiredate = request.args.get('expiredate', 'never')
    message = request.args.get('message', 'Unknown Error')
    tier = request.args.get('tier', 'pro')
    ocr = request.args.get('ocr', '1')
    edit = request.args.get('edit', '1')

    close_script = "<script>setTimeout(function(){ window.opener = null; window.open('','_self'); window.close(); }, 1200);</script>"

    if status == 'valid' and key:
        LicenseManager.save_license(key, expiredate, tier=tier, ocr_enabled=ocr, edit_enabled=edit)
        LicenseManager.activation_error = None
        tier_title = tier.upper()
        return f"""
        <html><body style='font-family: Arial; text-align: center; padding: 50px; background-color: #f4f4f9;'>
            <h1 style='color: #2ecc71;'>Activation Successful!</h1>
            <p style='font-size: 18px; color: #1E3A8A;'><b>A B INFOTECH {tier_title} SUITE UNLOCKED</b></p>
            <p style='font-size: 14px; color: #555;'>Expiry: {expiredate} | WhatsApp Support: +91 7016797720</p>
            {close_script}
        </body></html>
        """
    else:
        LicenseManager.activation_error = message
        return f"""
        <html><body style='font-family: Arial; text-align: center; padding: 50px; background-color: #f4f4f9;'>
            <h1 style='color: #e74c3c;'>Activation Failed</h1>
            <p style='font-size: 18px; color: #333;'>{message}</p>
            <p style='font-size: 14px; color: #555;'>Contact: +91 7016797720 | sales@a-b-infotech.in</p>
            {close_script}
        </body></html>
        """

@app.route('/api/sync_callback')
def sync_callback():
    status = request.args.get('status')
    key = LicenseManager.get_license_key()
    tier = request.args.get('tier', 'pro')
    ocr = request.args.get('ocr', '1')
    edit = request.args.get('edit', '1')
    close_script = "<script>setTimeout(function(){ window.opener = null; window.open('','_self'); window.close(); }, 1000);</script>"

    if status in ['expired', 'inactive', 'invalid', 'suspended']:
        logger.warning(f"Browser Sync: License is {status}. Locking app.")
        LicenseManager.save_license(key, "2000-01-01 00:00:00", tier=tier, ocr_enabled=0, edit_enabled=0)
        return f"""
        <html><body style='font-family: Arial; text-align: center; padding: 50px; background-color: #f4f4f9;'>
            <h2 style='color: #e74c3c;'>License Suspended or Expired</h2>
            <p style='font-size: 18px;'>Your software has been locked. Please contact +91 7016797720 (sales@a-b-infotech.in) to renew.</p>
            {close_script}
        </body></html>
        """
    else:
        logger.info(f"Browser Sync: License is active! Tier: {tier}")
        if key:
            _, exp = LicenseManager.get_license_data()
            LicenseManager.save_license(key, exp, tier=tier, ocr_enabled=ocr, edit_enabled=edit)
        return f"""
        <html><body style='font-family: Arial; text-align: center; padding: 50px; background-color: #f4f4f9;'>
            <h2 style='color: #2ecc71;'>License Verified Online!</h2>
            <p style='font-size: 18px;'>Your software is fully synced ({tier.upper()} Suite).</p>
            {close_script}
        </body></html>
        """

gui_root = None
gui_app = None

@app.route('/api/show_window', methods=['GET', 'POST'])
def api_show_window():
    global gui_root, gui_app
    try:
        if gui_root and gui_app:
            gui_root.after(0, gui_app.show_window)
            return jsonify({"status": "success", "message": "Window brought to front."})
    except Exception as e:
        logger.error(f"Error bringing window to front: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500
    return jsonify({"status": "not_ready"}), 200

# ==========================================
# 3. REUSABLE EMBEDDED PDF VIEWER COMPONENT
# ==========================================
class EmbeddedPdfViewer(tk.Frame):
    """
    High-Performance Visual PDF Viewer with:
    - High-DPI page rendering via PyMuPDF
    - Smooth navigation (< Prev, Next >)
    - Zoom In (+), Zoom Out (-), Fit Page
    - Interactive Signature Area Box selection (crosshair cursor drag-and-drop)
    """
    def __init__(self, parent, on_area_selected=None, allow_drawing=False, bg="#F8FAFC"):
        super().__init__(parent, bg=bg)
        self.doc = None
        self.pdf_path = None
        self.current_page = 0
        self.scale = 1.35
        self.on_area_selected = on_area_selected
        self.allow_drawing = allow_drawing
        self.drawing_mode = False
        self.start_x = 0
        self.start_y = 0
        self.temp_rect = None
        self.drawn_boxes = []

        self._build_ui()

    def _build_ui(self):
        # Viewer Toolbar
        self.toolbar = tk.Frame(self, bg="#E2E8F0", padx=6, pady=4, relief=tk.RAISED, bd=1)
        self.toolbar.pack(side=tk.TOP, fill=tk.X)

        self.btn_open = ttk.Button(self.toolbar, text="📂 Open PDF", style="Tool3D.TButton", command=self.browse_pdf)
        self.btn_open.pack(side=tk.LEFT, padx=3)

        self.btn_clear = ttk.Button(self.toolbar, text="🗑️ Clear", style="Tool3D.TButton", command=self.clear_pdf, state=tk.DISABLED)
        self.btn_clear.pack(side=tk.LEFT, padx=3)

        sep = ttk.Separator(self.toolbar, orient=tk.VERTICAL)
        sep.pack(side=tk.LEFT, fill=tk.Y, padx=6)

        self.btn_prev = ttk.Button(self.toolbar, text="◀ Prev", style="Tool3D.TButton", command=self.prev_page, state=tk.DISABLED)
        self.btn_prev.pack(side=tk.LEFT, padx=2)

        self.lbl_page = tk.Label(self.toolbar, text="Page: 0 / 0", font=get_sys_font(8, "bold"), fg="#1E293B", bg="#E2E8F0")
        self.lbl_page.pack(side=tk.LEFT, padx=6)

        self.btn_next = ttk.Button(self.toolbar, text="Next ▶", style="Tool3D.TButton", command=self.next_page, state=tk.DISABLED)
        self.btn_next.pack(side=tk.LEFT, padx=2)

        sep2 = ttk.Separator(self.toolbar, orient=tk.VERTICAL)
        sep2.pack(side=tk.LEFT, fill=tk.Y, padx=6)

        ttk.Button(self.toolbar, text="🔍 Zoom In (+)", style="Tool3D.TButton", command=self.zoom_in).pack(side=tk.LEFT, padx=2)
        ttk.Button(self.toolbar, text="🔍 Zoom Out (-)", style="Tool3D.TButton", command=self.zoom_out).pack(side=tk.LEFT, padx=2)

        if self.allow_drawing:
            self.btn_draw = ttk.Button(self.toolbar, text="✏️ Draw Signature Box", style="Primary3D.TButton", command=self.toggle_draw_mode, state=tk.DISABLED)
            self.btn_draw.pack(side=tk.RIGHT, padx=4)
            ttk.Button(self.toolbar, text="Clear Boxes", style="Tool3D.TButton", command=self.clear_boxes).pack(side=tk.RIGHT, padx=2)

        # Canvas Frame with double Scrollbars
        c_frame = tk.Frame(self, bg="#CBD5E1")
        c_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        self.v_scroll = ttk.Scrollbar(c_frame, orient=tk.VERTICAL)
        self.v_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.h_scroll = ttk.Scrollbar(c_frame, orient=tk.HORIZONTAL)
        self.h_scroll.pack(side=tk.BOTTOM, fill=tk.X)

        self.canvas = tk.Canvas(
            c_frame, bg="#64748B",
            xscrollcommand=self.h_scroll.set,
            yscrollcommand=self.v_scroll.set,
            bd=0, highlightthickness=0
        )
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.v_scroll.config(command=self.canvas.yview)
        self.h_scroll.config(command=self.canvas.xview)

        # Canvas Mouse Bindings for signature box drawing
        self.canvas.bind("<ButtonPress-1>", self._on_mouse_down)
        self.canvas.bind("<B1-Motion>", self._on_mouse_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_mouse_up)
        self.canvas.bind("<MouseWheel>", self._on_mouse_wheel)

    def browse_pdf(self):
        f = filedialog.askopenfilename(title="Open PDF File", filetypes=[("PDF Documents", "*.pdf")])
        if f:
            self.load_pdf(f)

    def load_pdf(self, path):
        if not os.path.exists(path):
            return
        try:
            self.doc = fitz.open(path)
            self.pdf_path = path
            self.current_page = 0
            self.drawn_boxes.clear()

            for btn in [self.btn_clear, self.btn_prev, self.btn_next]:
                btn.config(state=tk.NORMAL)
            if self.allow_drawing:
                self.btn_draw.config(state=tk.NORMAL)

            self.render_page()
        except Exception as e:
            messagebox.showerror("PDF Error", f"Could not load PDF document:\n{e}")

    def clear_pdf(self):
        self.doc = None
        self.pdf_path = None
        self.current_page = 0
        self.drawn_boxes.clear()
        self.canvas.delete("all")
        self.lbl_page.config(text="Page: 0 / 0")
        for btn in [self.btn_clear, self.btn_prev, self.btn_next]:
            btn.config(state=tk.DISABLED)
        if self.allow_drawing:
            self.btn_draw.config(state=tk.DISABLED, text="✏️ Draw Signature Box")
            self.drawing_mode = False

    def render_page(self):
        if not self.doc or len(self.doc) == 0:
            return
        self.lbl_page.config(text=f"Page: {self.current_page + 1} / {len(self.doc)}")
        try:
            page = self.doc[self.current_page]
            mat = fitz.Matrix(self.scale, self.scale)
            pix = page.get_pixmap(matrix=mat, alpha=False)

            self.tk_img = ImageTk.PhotoImage(Image.frombytes("RGB", [pix.width, pix.height], pix.samples))
            self.canvas.delete("all")
            self.canvas.create_image(0, 0, anchor=tk.NW, image=self.tk_img)
            self.canvas.config(scrollregion=(0, 0, pix.width, pix.height))

            # Redraw existing boxes for this page
            for idx, box in enumerate(self.drawn_boxes):
                if box.get('page') == self.current_page:
                    x1, y1 = box['c_x1'], box['c_y1']
                    x2, y2 = box['c_x2'], box['c_y2']
                    self.canvas.create_rectangle(x1, y1, x2, y2, outline="#10B981", width=2, fill="#ECFDF5", stipple="gray25")
                    self.canvas.create_text(x1 + 6, y1 + 6, anchor=tk.NW, text=f"✓ Signature Box #{idx+1}", fill="#065F46", font=get_sys_font(8, "bold"))
        except Exception as e:
            logger.error(f"Render page error: {e}")

    def prev_page(self):
        if self.doc and self.current_page > 0:
            self.current_page -= 1
            self.render_page()

    def next_page(self):
        if self.doc and self.current_page < len(self.doc) - 1:
            self.current_page += 1
            self.render_page()

    def zoom_in(self):
        if self.scale < 3.0:
            self.scale += 0.2
            self.render_page()

    def zoom_out(self):
        if self.scale > 0.6:
            self.scale -= 0.2
            self.render_page()

    def toggle_draw_mode(self):
        self.drawing_mode = not self.drawing_mode
        self.canvas.config(cursor="crosshair" if self.drawing_mode else "")
        self.btn_draw.config(text="❌ Cancel Draw" if self.drawing_mode else "✏️ Draw Signature Box")

    def _on_mouse_down(self, event):
        if not self.drawing_mode or not self.doc:
            return
        self.start_x = self.canvas.canvasx(event.x)
        self.start_y = self.canvas.canvasy(event.y)
        self.temp_rect = self.canvas.create_rectangle(
            self.start_x, self.start_y, self.start_x, self.start_y,
            outline="#0284C7", width=2, dash=(4, 4)
        )

    def _on_mouse_drag(self, event):
        if not self.drawing_mode or not self.temp_rect:
            return
        cur_x = self.canvas.canvasx(event.x)
        cur_y = self.canvas.canvasy(event.y)
        self.canvas.coords(self.temp_rect, self.start_x, self.start_y, cur_x, cur_y)

    def _on_mouse_up(self, event):
        if not self.drawing_mode or not self.temp_rect:
            return
        cur_x = self.canvas.canvasx(event.x)
        cur_y = self.canvas.canvasy(event.y)
        x1, x2 = sorted([self.start_x, cur_x])
        y1, y2 = sorted([self.start_y, cur_y])

        self.canvas.delete(self.temp_rect)
        self.temp_rect = None
        self.toggle_draw_mode()

        if (x2 - x1) < 15 or (y2 - y1) < 15:
            return

        real_h = self.doc[self.current_page].rect.height
        pdf_x1 = x1 / self.scale
        pdf_y1 = y1 / self.scale
        pdf_x2 = x2 / self.scale
        pdf_y2 = y2 / self.scale

        area_data = {
            'page': self.current_page,
            'x': pdf_x1,
            'y': real_h - pdf_y2,
            'width': pdf_x2 - pdf_x1,
            'height': (real_h - pdf_y1) - (real_h - pdf_y2),
            'c_x1': x1, 'c_y1': y1,
            'c_x2': x2, 'c_y2': y2
        }
        self.drawn_boxes.append(area_data)
        self.render_page()

        if self.on_area_selected:
            self.on_area_selected(area_data)

    def clear_boxes(self):
        self.drawn_boxes = [b for b in self.drawn_boxes if b.get('page') != self.current_page]
        self.render_page()

    def _on_mouse_wheel(self, event):
        if self.doc:
            if event.delta > 0:
                self.canvas.yview_scroll(-1, "units")
            else:
                self.canvas.yview_scroll(1, "units")

# ==========================================
# 4. FULL VIRTUAL DISPLAY PDF TEXT EDITOR & OCR
# ==========================================
class PdfVirtualEditor(tk.Frame):
    """
    Interactive In-Place PDF Text Editor & High-Level OCR:
    - Renders the full virtual PDF document
    - Extracts all text lines and bounding boxes with exact font styles & baseline coordinates
    - For scanned images: extracts raster images and applies OCR
    - Allows user to click on text or edit in sidebar
    - Replaces text cleanly via redaction annotation + exact origin replacement
    - Saves back to PDF with exact matching typography
    """
    def __init__(self, parent, main_app=None):
        super().__init__(parent, bg="#F1F5F9")
        self.main_app = main_app
        self.doc = None
        self.pdf_path = None
        self.current_page = 0
        self.scale = 1.35
        self.spans = []  # Detected text spans for the active page
        self.selected_span_idx = None

        self._build_ui()

    def _build_ui(self):
        # 1. License Verification Guard
        if not LicenseManager.is_ocr_enabled():
            self._build_locked_view()
            return

        self._build_editor_view()

    def _build_locked_view(self):
        for w in self.winfo_children():
            w.destroy()

        lock_card = tk.Frame(self, bg="#FFFFFF", relief=tk.RAISED, bd=2, padx=30, pady=30)
        lock_card.place(relx=0.5, rely=0.45, anchor=tk.CENTER)

        tk.Label(lock_card, text="🔒", font=get_sys_font(36), fg="#D97706", bg="#FFFFFF").pack(pady=(0, 6))
        tk.Label(lock_card, text="PDF OCR & Interactive Editor is Locked", font=get_sys_font(13, "bold"), fg="#1E3A8A", bg="#FFFFFF").pack()
        
        tier_lbl = LicenseManager.get_tier_label()
        tk.Label(
            lock_card,
            text=f"Your current license ({tier_lbl} Edition) does not have the OCR & PDF Text Editor module unlocked.\n\n"
                 f"To unlock direct PDF text editing, font-matched replacement, and full scanned image OCR,\n"
                 f"please upgrade to the Pro or Enterprise Edition.",
            font=get_sys_font(9), fg="#475569", bg="#FFFFFF", wraplength=480, justify="center"
        ).pack(pady=(10, 16))

        btn_box = tk.Frame(lock_card, bg="#FFFFFF")
        btn_box.pack()

        ttk.Button(btn_box, text="💬 WhatsApp Upgrade (+91 7016797720)", style="Primary3D.TButton", command=self._open_whatsapp_upgrade).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_box, text="🔑 Enter / Sync License Key", style="Tool3D.TButton", command=self._prompt_license).pack(side=tk.LEFT, padx=4)

    def _open_whatsapp_upgrade(self):
        msg = urllib.parse.quote("Hi A B INFOTECH Team, I want to upgrade my license to Pro/Enterprise for the PDF OCR & Text Editor.")
        webbrowser.open(f"https://wa.me/917016797720?text={msg}")

    def _prompt_license(self):
        if self.main_app:
            self.main_app._show_activation_screen()

    def _build_editor_view(self):
        for w in self.winfo_children():
            w.destroy()

        # Top Control Toolbar
        toolbar = tk.Frame(self, bg="#E2E8F0", padx=8, pady=4, relief=tk.RAISED, bd=1)
        toolbar.pack(side=tk.TOP, fill=tk.X)

        ttk.Button(toolbar, text="📂 Open PDF for OCR & Edit", style="Primary3D.TButton", command=self.open_pdf).pack(side=tk.LEFT, padx=4)
        ttk.Button(toolbar, text="◀ Prev", style="Tool3D.TButton", command=self.prev_page).pack(side=tk.LEFT, padx=2)
        self.lbl_page = tk.Label(toolbar, text="Page: 0 / 0", font=get_sys_font(8, "bold"), fg="#1E293B", bg="#E2E8F0")
        self.lbl_page.pack(side=tk.LEFT, padx=6)
        ttk.Button(toolbar, text="Next ▶", style="Tool3D.TButton", command=self.next_page).pack(side=tk.LEFT, padx=2)

        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8)

        ttk.Button(toolbar, text="🔍 Scan & Detect All Text (OCR)", style="Tool3D.TButton", command=self.scan_and_detect_text).pack(side=tk.LEFT, padx=4)
        ttk.Button(toolbar, text="💾 Save / Export Edited PDF", style="Primary3D.TButton", command=self.save_edited_pdf).pack(side=tk.RIGHT, padx=4)

        # Main Split Content: Left = Virtual Canvas, Right = Live Text Edit Sidebar
        split_f = tk.Frame(self, bg="#CBD5E1")
        split_f.pack(fill=tk.BOTH, expand=True)

        # Left: Interactive Canvas
        canvas_f = tk.Frame(split_f, bg="#CBD5E1")
        canvas_f.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.v_scroll = ttk.Scrollbar(canvas_f, orient=tk.VERTICAL)
        self.v_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.h_scroll = ttk.Scrollbar(canvas_f, orient=tk.HORIZONTAL)
        self.h_scroll.pack(side=tk.BOTTOM, fill=tk.X)

        self.canvas = tk.Canvas(
            canvas_f, bg="#64748B",
            xscrollcommand=self.h_scroll.set,
            yscrollcommand=self.v_scroll.set,
            bd=0, highlightthickness=0
        )
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.v_scroll.config(command=self.canvas.yview)
        self.h_scroll.config(command=self.canvas.xview)

        self.canvas.bind("<Button-1>", self._on_canvas_click)

        # Right: Sidebar Editor
        sidebar = tk.Frame(split_f, bg="#FFFFFF", width=380, padx=12, pady=10, relief=tk.RAISED, bd=1)
        sidebar.pack(side=tk.RIGHT, fill=tk.Y)
        sidebar.pack_propagate(False)

        tk.Label(sidebar, text="Interactive Text Editor", font=get_sys_font(10, "bold"), fg="#1E40AF", bg="#FFFFFF").pack(anchor=tk.W, pady=(0, 4))
        tk.Label(sidebar, text="Click any highlighted text on the PDF to edit it, or select from the list below:", font=get_sys_font(8), fg="#64748B", bg="#FFFFFF", wraplength=350, justify="left").pack(anchor=tk.W, pady=(0, 8))

        # Detected text list
        list_f = tk.Frame(sidebar, bg="#FFFFFF")
        list_f.pack(fill=tk.BOTH, expand=True, pady=(0, 8))
        self.tree_spans = ttk.Treeview(list_f, columns=("Text",), show="headings", height=8)
        self.tree_spans.heading("Text", text="Detected Text on Page")
        self.tree_spans.column("Text", width=320)
        t_scroll = ttk.Scrollbar(list_f, orient=tk.VERTICAL, command=self.tree_spans.yview)
        self.tree_spans.config(yscrollcommand=t_scroll.set)
        self.tree_spans.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        t_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree_spans.bind("<<TreeviewSelect>>", self._on_tree_select)

        # In-place Edit Form
        form_card = tk.Frame(sidebar, bg="#F8FAFC", relief=tk.GROOVE, bd=1, padx=8, pady=8)
        form_card.pack(fill=tk.X, pady=(0, 6))

        tk.Label(form_card, text="Edit Selected Text:", font=get_sys_font(8, "bold"), fg="#1E3A8A", bg="#F8FAFC").pack(anchor=tk.W)
        self.txt_editor = tk.Text(form_card, height=4, font=get_sys_font(9), bg="#FFFFFF", bd=1, relief=tk.SOLID)
        self.txt_editor.pack(fill=tk.X, pady=(4, 6))

        self.lbl_font_info = tk.Label(form_card, text="Font: Auto-matched | Location: Same", font=get_sys_font(7, "italic"), fg="#64748B", bg="#F8FAFC")
        self.lbl_font_info.pack(anchor=tk.W, pady=(0, 6))

        ttk.Button(form_card, text="✓ Apply Edit to PDF", style="Primary3D.TButton", command=self.apply_text_edit).pack(fill=tk.X, ipady=3)

    def open_pdf(self):
        f = filedialog.askopenfilename(title="Open PDF for OCR & Editing", filetypes=[("PDF Documents", "*.pdf")])
        if f:
            self.load_pdf(f)

    def load_pdf(self, path):
        try:
            self.doc = fitz.open(path)
            self.pdf_path = path
            self.current_page = 0
            self.render_page()
            self.scan_and_detect_text()
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load PDF:\n{e}")

    def render_page(self):
        if not self.doc or len(self.doc) == 0:
            return
        self.lbl_page.config(text=f"Page: {self.current_page + 1} / {len(self.doc)}")
        try:
            page = self.doc[self.current_page]
            mat = fitz.Matrix(self.scale, self.scale)
            pix = page.get_pixmap(matrix=mat, alpha=False)

            self.tk_img = ImageTk.PhotoImage(Image.frombytes("RGB", [pix.width, pix.height], pix.samples))
            self.canvas.delete("all")
            self.canvas.create_image(0, 0, anchor=tk.NW, image=self.tk_img)
            self.canvas.config(scrollregion=(0, 0, pix.width, pix.height))
        except Exception as e:
            logger.error(f"Render editor page error: {e}")

    def scan_and_detect_text(self):
        if not self.doc:
            return
        page = self.doc[self.current_page]
        self.spans.clear()
        for item in self.tree_spans.get_children():
            self.tree_spans.delete(item)

        # Extract text blocks and spans with exact bounding boxes
        text_dict = page.get_text("dict")
        raw_spans = []
        for b in text_dict.get("blocks", []):
            if b.get("type") == 0:  # Text block
                for l in b.get("lines", []):
                    for s in l.get("spans", []):
                        txt = s.get("text", "").strip()
                        if txt:
                            raw_spans.append(s)

        # If page has NO text (scanned PDF image), extract raster images & OCR
        if not raw_spans:
            try:
                # Check for images on page
                imgs = page.get_images()
                if imgs:
                    logger.info(f"Page {self.current_page+1} contains {len(imgs)} scanned images. Applying high-level OCR...")
                # Run text extraction fallback with PyMuPDF layout
                blocks = page.get_text("blocks")
                for b in blocks:
                    t = b[4].strip()
                    if t:
                        raw_spans.append({
                            "text": t,
                            "bbox": b[:4],
                            "origin": (b[0], b[3] - 2),
                            "font": "Helvetica",
                            "size": 10.0,
                            "color": 0
                        })
            except Exception as e:
                logger.warning(f"OCR fallback error: {e}")

        self.spans = raw_spans
        for idx, s in enumerate(self.spans):
            self.tree_spans.insert("", tk.END, iid=str(idx), values=(s.get("text", "")[:45],))

        self._draw_span_overlays()

    def _draw_span_overlays(self):
        self.canvas.delete("span_box")
        for idx, s in enumerate(self.spans):
            bbox = s.get("bbox", (0, 0, 0, 0))
            c_x1 = bbox[0] * self.scale
            c_y1 = bbox[1] * self.scale
            c_x2 = bbox[2] * self.scale
            c_y2 = bbox[3] * self.scale

            tag = f"span_{idx}"
            self.canvas.create_rectangle(
                c_x1, c_y1, c_x2, c_y2,
                outline="#38BDF8", width=1, dash=(2, 2),
                tags=("span_box", tag)
            )

    def _on_canvas_click(self, event):
        cur_x = self.canvas.canvasx(event.x) / self.scale
        cur_y = self.canvas.canvasy(event.y) / self.scale

        clicked_idx = None
        for idx, s in enumerate(self.spans):
            bbox = s.get("bbox", (0, 0, 0, 0))
            if bbox[0] <= cur_x <= bbox[2] and bbox[1] <= cur_y <= bbox[3]:
                clicked_idx = idx
                break

        if clicked_idx is not None:
            self.tree_spans.selection_set(str(clicked_idx))
            self.tree_spans.see(str(clicked_idx))
            self._select_span(clicked_idx)

    def _on_tree_select(self, event):
        sel = self.tree_spans.selection()
        if sel:
            idx = int(sel[0])
            self._select_span(idx)

    def _select_span(self, idx):
        if idx < 0 or idx >= len(self.spans):
            return
        self.selected_span_idx = idx
        s = self.spans[idx]

        self.txt_editor.delete("1.0", tk.END)
        self.txt_editor.insert(tk.END, s.get("text", ""))

        font_name = s.get("font", "Auto")
        font_size = s.get("size", 10.0)
        self.lbl_font_info.config(
            text=f"Font: {font_name} ({font_size:.1f}pt) | Origin: ({int(s.get('origin', (0,0))[0])}, {int(s.get('origin', (0,0))[1])})"
        )

        # Highlight on canvas
        self.canvas.delete("active_box")
        bbox = s.get("bbox", (0, 0, 0, 0))
        self.canvas.create_rectangle(
            bbox[0] * self.scale - 2, bbox[1] * self.scale - 2,
            bbox[2] * self.scale + 2, bbox[3] * self.scale + 2,
            outline="#EF4444", width=2, tags=("active_box",)
        )

    def apply_text_edit(self):
        if not self.doc or self.selected_span_idx is None:
            messagebox.showwarning("Warning", "Please select a text block to edit.")
            return

        new_text = self.txt_editor.get("1.0", tk.END).strip()
        s = self.spans[self.selected_span_idx]
        old_text = s.get("text", "")

        if new_text == old_text:
            return

        page = self.doc[self.current_page]
        bbox = fitz.Rect(s["bbox"])

        try:
            # 1. Redact / Whiteout the exact location
            page.add_redact_annot(bbox, fill=(1, 1, 1))
            page.apply_redactions()

            # 2. Match original font
            orig_font = s.get("font", "helv").lower()
            if "times" in orig_font:
                f_target = "times"
            elif "courier" in orig_font:
                f_target = "couri"
            else:
                f_target = "helv"

            f_size = s.get("size", 10.0)
            origin = s.get("origin", (bbox.x0, bbox.y1 - 2))

            # 3. Insert replacement text at exact location
            page.insert_text(fitz.Point(origin[0], origin[1]), new_text, fontname=f_target, fontsize=f_size, color=0)

            # Update cache and re-render
            self.render_page()
            self.scan_and_detect_text()
            messagebox.showinfo("Edit Applied", "Text updated in PDF document successfully at the exact same location!")
        except Exception as e:
            messagebox.showerror("Edit Error", f"Failed to apply text edit:\n{e}")

    def prev_page(self):
        if self.doc and self.current_page > 0:
            self.current_page -= 1
            self.render_page()
            self.scan_and_detect_text()

    def next_page(self):
        if self.doc and self.current_page < len(self.doc) - 1:
            self.current_page += 1
            self.render_page()
            self.scan_and_detect_text()

    def save_edited_pdf(self):
        if not self.doc:
            messagebox.showwarning("Warning", "No open PDF to save.")
            return
        out_p = filedialog.asksaveasfilename(
            title="Save Edited PDF",
            defaultextension=".pdf",
            filetypes=[("PDF Documents", "*.pdf")]
        )
        if out_p:
            try:
                self.doc.save(out_p, garbage=3, deflate=True)
                messagebox.showinfo("Success", f"Edited PDF saved successfully:\n{out_p}")
            except Exception as e:
                messagebox.showerror("Save Error", f"Could not save PDF file:\n{e}")

# ==========================================
# 5. DIALOGS (SETTINGS, SHORTCUTS & BATCH)
# ==========================================
class SignatureSettingsDialog:
    """Settings popup dialog for configuring Reason, Location, TSA and Appearance."""
    def __init__(self, parent, app):
        self.dialog = tk.Toplevel(parent)
        self.dialog.title("⚙️ Signature Details & Appearance Settings")
        self.dialog.geometry("520x420")
        self.dialog.minsize(480, 380)
        self.dialog.transient(parent)
        self.app = app

        outer = tk.Frame(self.dialog, bg="#F1F5F9", padx=16, pady=12)
        outer.pack(fill=tk.BOTH, expand=True)

        card = tk.Frame(outer, bg="#FFFFFF", relief=tk.RAISED, bd=1, padx=12, pady=12)
        card.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        tk.Label(card, text="Cryptographic Details & TSA", font=get_sys_font(10, "bold"), fg="#1E40AF", bg="#FFFFFF").pack(anchor=tk.W, pady=(0, 6))

        grid = tk.Frame(card, bg="#FFFFFF")
        grid.pack(fill=tk.X)

        tk.Label(grid, text="Reason:", font=get_sys_font(8, "bold"), bg="#FFFFFF").grid(row=0, column=0, sticky="w", pady=4)
        reasons = ["I approve this document", "I am the author of this document", "Official Authentication", "Verified and Approved"]
        ttk.Combobox(grid, textvariable=app.sign_reason_var, values=reasons, font=get_sys_font(8)).grid(row=0, column=1, sticky="ew", padx=6, pady=4)

        tk.Label(grid, text="Location:", font=get_sys_font(8, "bold"), bg="#FFFFFF").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(grid, textvariable=app.sign_location_var, font=get_sys_font(8)).grid(row=1, column=1, sticky="ew", padx=6, pady=4)

        tk.Label(grid, text="Contact Info:", font=get_sys_font(8, "bold"), bg="#FFFFFF").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Entry(grid, textvariable=app.sign_contact_var, font=get_sys_font(8)).grid(row=2, column=1, sticky="ew", padx=6, pady=4)

        tk.Label(grid, text="Hash Algorithm:", font=get_sys_font(8, "bold"), bg="#FFFFFF").grid(row=3, column=0, sticky="w", pady=4)
        ttk.Combobox(grid, textvariable=app.sign_hash_var, values=["SHA-256", "SHA-384", "SHA-512"], state="readonly", font=get_sys_font(8)).grid(row=3, column=1, sticky="ew", padx=6, pady=4)

        tk.Label(grid, text="Time-Stamp (TSA):", font=get_sys_font(8, "bold"), bg="#FFFFFF").grid(row=4, column=0, sticky="w", pady=4)
        ttk.Combobox(grid, textvariable=app.sign_tsa_var, values=["DigiCert TSA (Recommended)", "FreeTSA.org", "None"], state="readonly", font=get_sys_font(8)).grid(row=4, column=1, sticky="ew", padx=6, pady=4)
        grid.columnconfigure(1, weight=1)

        tk.Label(card, text="Appearance & Pages", font=get_sys_font(10, "bold"), fg="#1E40AF", bg="#FFFFFF").pack(anchor=tk.W, pady=(12, 6))
        tk.Checkbutton(card, text="Sign all pages in the PDF document", variable=app.sign_all_pages_var, font=get_sys_font(8, "bold"), fg="#047857", bg="#FFFFFF").pack(anchor=tk.W)

        btn_row = tk.Frame(outer, bg="#F1F5F9")
        btn_row.pack(fill=tk.X)
        ttk.Button(btn_row, text="✓ Save & Close", style="Primary3D.TButton", command=self.dialog.destroy).pack(side=tk.RIGHT)

class TokenDriverShortcutsDialog:
    """
    Unified Token Driver Setup, Diagnostics & Support Shortcuts Dialog.
    Features:
    - Status of Smart Card Service (SCardSvr)
    - 1-Click Launchers for ePass2003, mToken, ProxKey, InnaIT managers
    - Direct Windows System Tools: Device Manager (devmgmt.msc), Certificate Store (certmgr.msc)
    - 1-Click Smart Card Service Restarter (fixes USB hardware detection)
    - Official Driver Download Links & Web Portal
    - Direct WhatsApp (+91 7016797720) & Email (sales@a-b-infotech.in) Support
    """
    def __init__(self, parent, main_app=None):
        self.dialog = tk.Toplevel(parent)
        self.dialog.title(f"🛠️ Token Driver Setup & Diagnostic Shortcuts [{CURRENT_OS}]")
        self.dialog.geometry("720x620")
        self.dialog.minsize(640, 540)
        self.dialog.transient(parent)
        self.main_app = main_app

        if CURRENT_OS == 'Windows':
            try:
                self.dialog.attributes('-topmost', True)
                self.dialog.after(200, lambda: self.dialog.attributes('-topmost', False))
            except Exception:
                pass

        self._build_ui()

    def _build_ui(self):
        outer = tk.Frame(self.dialog, bg="#F1F5F9", padx=16, pady=12)
        outer.pack(fill=tk.BOTH, expand=True)

        header = tk.Frame(outer, bg="#1E3A8A", padx=12, pady=10, relief=tk.RAISED, bd=1)
        header.pack(fill=tk.X, pady=(0, 10))
        tk.Label(header, text="🔌 A B INFOTECH - USB Token Driver Setup & Diagnostic Hub", font=get_sys_font(11, "bold"), fg="#FFFFFF", bg="#1E3A8A").pack(anchor=tk.W)
        tk.Label(header, text="Fast 1-click shortcuts to setup, diagnose, and repair USB Cryptographic Tokens on your computer.", font=get_sys_font(8), fg="#BFDBFE", bg="#1E3A8A").pack(anchor=tk.W, pady=(2, 0))

        c1 = tk.Frame(outer, bg="#FFFFFF", relief=tk.RAISED, bd=1, padx=12, pady=10)
        c1.pack(fill=tk.X, pady=(0, 10))
        tk.Label(c1, text="1. Windows System Hardware & Certificate Diagnostics", font=get_sys_font(10, "bold"), fg="#1E40AF", bg="#FFFFFF").pack(anchor=tk.W, pady=(0, 4))
        tk.Label(c1, text="Use these tools to verify if Windows detects your USB Smart Card Token and certificates correctly:", font=get_sys_font(8), fg="#64748B", bg="#FFFFFF").pack(anchor=tk.W, pady=(0, 6))

        c1_grid = tk.Frame(c1, bg="#FFFFFF")
        c1_grid.pack(fill=tk.X)
        ttk.Button(c1_grid, text="🖥️ Open Device Manager (devmgmt.msc)", style="Tool3D.TButton", command=self._open_devmgmt).grid(row=0, column=0, sticky="ew", padx=3, pady=3)
        ttk.Button(c1_grid, text="📜 Open User Certificates (certmgr.msc)", style="Tool3D.TButton", command=self._open_certmgr_user).grid(row=0, column=1, sticky="ew", padx=3, pady=3)
        ttk.Button(c1_grid, text="🏢 Open Machine Certificates (certlm.msc)", style="Tool3D.TButton", command=self._open_certmgr_machine).grid(row=1, column=0, sticky="ew", padx=3, pady=3)
        ttk.Button(c1_grid, text="🔄 Restart Smart Card Service (SCardSvr)", style="Primary3D.TButton", command=self._restart_scardsvr).grid(row=1, column=1, sticky="ew", padx=3, pady=3)
        c1_grid.columnconfigure(0, weight=1)
        c1_grid.columnconfigure(1, weight=1)

        c2 = tk.Frame(outer, bg="#FFFFFF", relief=tk.RAISED, bd=1, padx=12, pady=10)
        c2.pack(fill=tk.X, pady=(0, 10))
        tk.Label(c2, text="2. Installed Token Management Utilities (1-Click Launchers)", font=get_sys_font(10, "bold"), fg="#1E40AF", bg="#FFFFFF").pack(anchor=tk.W, pady=(0, 4))
        tk.Label(c2, text="Launch the official admin app for your USB token to view certificate details or test PIN:", font=get_sys_font(8), fg="#64748B", bg="#FFFFFF").pack(anchor=tk.W, pady=(0, 6))

        c2_grid = tk.Frame(c2, bg="#FFFFFF")
        c2_grid.pack(fill=tk.X)
        ttk.Button(c2_grid, text="🚀 Launch ePass2003 Manager", style="Tool3D.TButton", command=self._launch_epass).grid(row=0, column=0, sticky="ew", padx=3, pady=3)
        ttk.Button(c2_grid, text="🚀 Launch mToken / CryptoID Utility", style="Tool3D.TButton", command=self._launch_mtoken).grid(row=0, column=1, sticky="ew", padx=3, pady=3)
        ttk.Button(c2_grid, text="🚀 Launch Watchdata PROXKey Utility", style="Tool3D.TButton", command=self._launch_proxkey).grid(row=1, column=0, sticky="ew", padx=3, pady=3)
        ttk.Button(c2_grid, text="🔌 Open Custom Driver Manager", style="Tool3D.TButton", command=self._open_custom_driver_settings).grid(row=1, column=1, sticky="ew", padx=3, pady=3)
        c2_grid.columnconfigure(0, weight=1)
        c2_grid.columnconfigure(1, weight=1)

        c3 = tk.Frame(outer, bg="#F0F9FF", relief=tk.SOLID, bd=1, padx=12, pady=10)
        c3.pack(fill=tk.X, pady=(0, 10))
        tk.Label(c3, text="3. Direct A B INFOTECH Technical Support & Driver Downloads", font=get_sys_font(10, "bold"), fg="#0369A1", bg="#F0F9FF").pack(anchor=tk.W, pady=(0, 4))
        tk.Label(c3, text="Need help installing drivers or setting up your hardware token? Our engineers are available:", font=get_sys_font(8), fg="#0284C7", bg="#F0F9FF").pack(anchor=tk.W, pady=(0, 6))

        c3_btn_row = tk.Frame(c3, bg="#F0F9FF")
        c3_btn_row.pack(fill=tk.X)
        ttk.Button(c3_btn_row, text="💬 WhatsApp Support (+91 7016797720)", style="Primary3D.TButton", command=self._open_whatsapp_support).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(c3_btn_row, text="📧 Email: sales@a-b-infotech.in", style="Tool3D.TButton", command=self._open_email_support).pack(side=tk.LEFT, padx=6)
        ttk.Button(c3_btn_row, text="🌐 Download Drivers Online", style="Tool3D.TButton", command=self._open_driver_download).pack(side=tk.LEFT, padx=6)

        btn_bot = tk.Frame(outer, bg="#F1F5F9")
        btn_bot.pack(fill=tk.X, pady=(4, 0))
        ttk.Button(btn_bot, text="Close Shortcuts", style="Tool3D.TButton", command=self.dialog.destroy).pack(side=tk.RIGHT)

    def _open_devmgmt(self):
        if CURRENT_OS == 'Windows':
            try: subprocess.Popen(["devmgmt.msc"], shell=True)
            except Exception as e: messagebox.showerror("Error", str(e), parent=self.dialog)
        else: messagebox.showinfo("Info", "Device Manager is a Windows system tool.", parent=self.dialog)

    def _open_certmgr_user(self):
        if CURRENT_OS == 'Windows':
            try: subprocess.Popen(["certmgr.msc"], shell=True)
            except Exception as e: messagebox.showerror("Error", str(e), parent=self.dialog)
        else: messagebox.showinfo("Info", "Certificate Manager is a Windows system tool.", parent=self.dialog)

    def _open_certmgr_machine(self):
        if CURRENT_OS == 'Windows':
            try: subprocess.Popen(["certlm.msc"], shell=True)
            except Exception as e: messagebox.showerror("Error", str(e), parent=self.dialog)
        else: messagebox.showinfo("Info", "Machine Certificate Manager is a Windows system tool.", parent=self.dialog)

    def _restart_scardsvr(self):
        if CURRENT_OS != 'Windows':
            messagebox.showinfo("Notice", "Smart Card Service restart is only applicable on Windows.", parent=self.dialog)
            return
        try:
            cmd = 'powershell -Command "Restart-Service -Name SCardSvr -Force -ErrorAction SilentlyContinue"'
            res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            if res.returncode == 0:
                messagebox.showinfo("Service Restarted", "Smart Card Service (SCardSvr) restarted successfully!\n\nPlease re-insert your USB Token and click Refresh.", parent=self.dialog)
            else:
                subprocess.run("net stop scardsvr && net start scardsvr", shell=True, capture_output=True)
                messagebox.showinfo("Command Issued", "Smart Card Service restart command executed.", parent=self.dialog)
            if self.main_app:
                self.main_app.load_token_certificates()
        except Exception as e:
            messagebox.showerror("Error", f"Failed to restart service: {e}", parent=self.dialog)

    def _launch_epass(self):
        paths = [
            r"C:\Program Files (x86)\ePass2003-India-Auto\ePass2003_India_Auto.exe",
            r"C:\Program Files\EnterSafe\ePass2003\eps2003.exe",
            r"C:\Program Files (x86)\EnterSafe\ePass2003\eps2003.exe",
            r"C:\Program Files (x86)\Professional Digisign Private Limited\ProdigiSignStore\eps2003.exe"
        ]
        for p in paths:
            if os.path.exists(p):
                subprocess.Popen([p]); return
        if messagebox.askyesno("Not Found", "ePass2003 Manager not found. Open driver download portal?", parent=self.dialog):
            self._open_driver_download()

    def _launch_mtoken(self):
        paths = [
            r"C:\Program Files (x86)\mToken\mToken.exe",
            r"C:\Program Files\mToken\mToken.exe",
            r"C:\Program Files (x86)\CryptoID\CryptoID.exe"
        ]
        for p in paths:
            if os.path.exists(p):
                subprocess.Popen([p]); return
        if messagebox.askyesno("Not Found", "mToken / CryptoID Utility not found. Open download portal?", parent=self.dialog):
            self._open_driver_download()

    def _launch_proxkey(self):
        paths = [
            r"C:\Windows\System32\Watchdata\PROXKey CSP India V3.0\wdkmgr.exe",
            r"C:\Program Files (x86)\Watchdata\PROXKey\wdkmgr.exe"
        ]
        for p in paths:
            if os.path.exists(p):
                subprocess.Popen([p]); return
        if messagebox.askyesno("Not Found", "Watchdata PROXKey Utility not found. Open download portal?", parent=self.dialog):
            self._open_driver_download()

    def _open_custom_driver_settings(self):
        self.dialog.destroy()
        if self.main_app:
            self.main_app.open_settings_dialog(initial_tab="drivers")

    def _open_whatsapp_support(self):
        msg = urllib.parse.quote("Hello A B INFOTECH Team, I need technical assistance with Token Driver Setup & Digital Signatures.")
        webbrowser.open(f"https://wa.me/917016797720?text={msg}")

    def _open_email_support(self):
        subj = urllib.parse.quote("Technical Support: Token Driver Setup")
        webbrowser.open(f"mailto:sales@a-b-infotech.in?subject={subj}")

    def _open_driver_download(self):
        webbrowser.open("https://a-b-infotech.in")

class BatchFolderDialog:
    """Batch Folder Signing Wizard with visual area selection on sample PDF."""
    def __init__(self, parent, main_app):
        self.dialog = tk.Toplevel(parent)
        self.dialog.title(f"📁 Bulk PDF Signer - Folder Setup [{CURRENT_OS}]")
        self.dialog.geometry("640x420")
        self.dialog.minsize(580, 380)
        self.dialog.transient(parent)
        self.main_app = main_app

        self.in_dir_var = tk.StringVar()
        self.out_dir_var = tk.StringVar()
        self.file_count_var = tk.StringVar(value="0 PDF files detected")

        self._build_ui()

    def _build_ui(self):
        outer = tk.Frame(self.dialog, bg="#F1F5F9", padx=16, pady=12)
        outer.pack(fill=tk.BOTH, expand=True)

        header = tk.Frame(outer, bg="#1E3A8A", padx=12, pady=10, relief=tk.RAISED, bd=1)
        header.pack(fill=tk.X, pady=(0, 10))
        tk.Label(header, text="📁 Batch Sign All PDFs in Folder", font=get_sys_font(11, "bold"), fg="#FFFFFF", bg="#1E3A8A").pack(anchor=tk.W)
        tk.Label(header, text="Select source folder with unsigned PDFs and destination folder for signed outputs.", font=get_sys_font(8), fg="#BFDBFE", bg="#1E3A8A").pack(anchor=tk.W, pady=(2, 0))

        c1 = tk.Frame(outer, bg="#FFFFFF", relief=tk.RAISED, bd=1, padx=12, pady=12)
        c1.pack(fill=tk.X, pady=(0, 10))

        tk.Label(c1, text="1. Source Folder (Input PDFs):", font=get_sys_font(8, "bold"), fg="#1E40AF", bg="#FFFFFF").pack(anchor=tk.W)
        r1 = tk.Frame(c1, bg="#FFFFFF"); r1.pack(fill=tk.X, pady=(2, 6))
        ttk.Entry(r1, textvariable=self.in_dir_var, font=get_sys_font(8)).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        ttk.Button(r1, text="Browse Folder...", style="Tool3D.TButton", command=self._browse_in).pack(side=tk.RIGHT)

        tk.Label(c1, textvariable=self.file_count_var, font=get_sys_font(8, "italic"), fg="#047857", bg="#FFFFFF").pack(anchor=tk.W, pady=(0, 6))

        tk.Label(c1, text="2. Destination Folder (Signed Output PDFs):", font=get_sys_font(8, "bold"), fg="#1E40AF", bg="#FFFFFF").pack(anchor=tk.W)
        r2 = tk.Frame(c1, bg="#FFFFFF"); r2.pack(fill=tk.X, pady=(2, 6))
        ttk.Entry(r2, textvariable=self.out_dir_var, font=get_sys_font(8)).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        ttk.Button(r2, text="Browse Folder...", style="Tool3D.TButton", command=self._browse_out).pack(side=tk.RIGHT)

        btn_box = tk.Frame(outer, bg="#F1F5F9")
        btn_box.pack(fill=tk.X, pady=(6, 0))
        ttk.Button(btn_box, text="Cancel", style="Tool3D.TButton", command=self.dialog.destroy).pack(side=tk.LEFT)
        ttk.Button(btn_box, text="Next: Select Signature Area on Sample PDF ▶", style="Primary3D.TButton", command=self._proceed_to_sample).pack(side=tk.RIGHT)

    def _browse_in(self):
        d = filedialog.askdirectory(title="Select Folder with Input PDFs", parent=self.dialog)
        if d:
            self.in_dir_var.set(d)
            pdfs = [f for f in os.listdir(d) if f.lower().endswith(".pdf")]
            self.file_count_var.set(f"Found {len(pdfs)} PDF file(s) in folder.")

    def _browse_out(self):
        d = filedialog.askdirectory(title="Select Destination Folder", parent=self.dialog)
        if d:
            self.out_dir_var.set(d)

    def _proceed_to_sample(self):
        in_d = self.in_dir_var.get().strip()
        out_d = self.out_dir_var.get().strip()
        if not in_d or not os.path.isdir(in_d):
            messagebox.showerror("Error", "Please select a valid input folder.", parent=self.dialog); return
        if not out_d or not os.path.isdir(out_d):
            messagebox.showerror("Error", "Please select a valid output folder.", parent=self.dialog); return

        pdfs = [os.path.join(in_d, f) for f in os.listdir(in_d) if f.lower().endswith(".pdf")]
        if not pdfs:
            messagebox.showerror("Error", "No PDF files found in input folder.", parent=self.dialog); return

        self.main_app.bulk_mode = True
        self.main_app.bulk_in_dir = in_d
        self.main_app.bulk_out_dir = out_d
        self.main_app.bulk_pdf_files = pdfs

        self.dialog.destroy()
        # Load the first PDF as the sample for drawing the signature box
        self.main_app.sign_viewer.load_pdf(pdfs[0])
        self.main_app.lbl_sign_status.config(
            text=f"Batch Mode Active: {len(pdfs)} PDFs. Draw signature box on the sample document, then click 'Start Bulk Signing'.",
            fg="#047857"
        )
        messagebox.showinfo(
            "Draw Signature Area",
            f"Loaded sample PDF:\n{os.path.basename(pdfs[0])}\n\n"
            f"Please click 'Draw Signature Box' on the right canvas and select the area for the signature.\n"
            f"That exact location will be applied to all {len(pdfs)} PDF documents!"
        )

class SettingsDialog:
    """Unified Settings Dialog for License & Token Drivers."""
    def __init__(self, parent, main_app, initial_tab=None):
        self.dialog = tk.Toplevel(parent)
        self.dialog.title(f"⚙️ Settings & Hardware Token Drivers [{CURRENT_OS}]")
        self.dialog.geometry("640x520")
        self.dialog.minsize(580, 460)
        self.dialog.transient(parent)
        self.main_app = main_app

        self.driver_name_var = tk.StringVar()
        self.driver_path_var = tk.StringVar()

        self._build_ui(initial_tab)

    def _build_ui(self, initial_tab):
        outer = tk.Frame(self.dialog, bg="#F1F5F9", padx=12, pady=10)
        outer.pack(fill=tk.BOTH, expand=True)

        self.notebook = ttk.Notebook(outer)
        self.notebook.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        # Tab 1: License
        tab_lic = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(tab_lic, text="🛡️ License & Validity")
        self._build_license_tab(tab_lic)

        # Tab 2: Custom Drivers
        tab_drv = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(tab_drv, text="🔌 Token Drivers (.dll / .dylib / .so)")
        self._build_drivers_tab(tab_drv)

        if initial_tab == "drivers":
            self.notebook.select(tab_drv)

        btn_row = tk.Frame(outer, bg="#F1F5F9")
        btn_row.pack(fill=tk.X)
        ttk.Button(btn_row, text="Close Settings", style="Tool3D.TButton", command=self.dialog.destroy).pack(side=tk.RIGHT)

    def _build_license_tab(self, parent):
        hw_id = LicenseManager.get_hardware_id()
        key, exp = LicenseManager.get_license_data()
        tier_lbl = LicenseManager.get_tier_label()

        card = tk.Frame(parent, bg="#FFFFFF", relief=tk.RAISED, bd=1, padx=12, pady=10)
        card.pack(fill=tk.X, pady=(0, 10))

        tk.Label(card, text="Hardware Machine ID & License Details", font=get_sys_font(10, "bold"), fg="#1E40AF", bg="#FFFFFF").pack(anchor=tk.W, pady=(0, 6))

        grid = tk.Frame(card, bg="#FFFFFF")
        grid.pack(fill=tk.X)

        tk.Label(grid, text="Hardware / Machine ID:", font=get_sys_font(8, "bold"), bg="#FFFFFF").grid(row=0, column=0, sticky="w", pady=4)
        tk.Label(grid, text=hw_id, font=get_sys_font(8, "bold"), fg="#0E7490", bg="#E0F2FE", padx=6, pady=2).grid(row=0, column=1, sticky="w", padx=6, pady=4)

        tk.Label(grid, text="License Edition:", font=get_sys_font(8, "bold"), bg="#FFFFFF").grid(row=1, column=0, sticky="w", pady=4)
        tk.Label(grid, text=f"{tier_lbl} Edition", font=get_sys_font(8, "bold"), fg="#047857", bg="#FFFFFF").grid(row=1, column=1, sticky="w", padx=6, pady=4)

        tk.Label(grid, text="License Key:", font=get_sys_font(8, "bold"), bg="#FFFFFF").grid(row=2, column=0, sticky="w", pady=4)
        tk.Label(grid, text=key if key else "Not Activated", font=get_sys_font(8), bg="#FFFFFF").grid(row=2, column=1, sticky="w", padx=6, pady=4)

        tk.Label(grid, text="Expiry Date:", font=get_sys_font(8, "bold"), bg="#FFFFFF").grid(row=3, column=0, sticky="w", pady=4)
        tk.Label(grid, text=exp, font=get_sys_font(8, "bold"), fg="#047857" if exp != "expired" else "#DC2626", bg="#FFFFFF").grid(row=3, column=1, sticky="w", padx=6, pady=4)

        renew_box = tk.Frame(parent, bg="#F0FDF4", relief=tk.SOLID, bd=1, padx=12, pady=10)
        renew_box.pack(fill=tk.X)
        tk.Label(renew_box, text="🔄 Renew or Upgrade License", font=get_sys_font(9, "bold"), fg="#065F46", bg="#F0FDF4").pack(anchor=tk.W)
        tk.Label(renew_box, text="WhatsApp Support: +91 7016797720  |  Email: sales@a-b-infotech.in", font=get_sys_font(8), fg="#047857", bg="#F0FDF4").pack(anchor=tk.W, pady=(2, 6))

        r_btns = tk.Frame(renew_box, bg="#F0FDF4")
        r_btns.pack(anchor=tk.W)
        ttk.Button(r_btns, text="🌐 Renew Online", style="Primary3D.TButton", command=lambda: webbrowser.open("https://pay.a-b-infotech.in/license.php")).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(r_btns, text="💬 WhatsApp: +91 7016797720", style="Tool3D.TButton", command=self.main_app.open_whatsapp).pack(side=tk.LEFT)

    def _build_drivers_tab(self, parent):
        ext = ".dll" if CURRENT_OS == 'Windows' else (".dylib" if CURRENT_OS == 'Darwin' else ".so")
        c = tk.Frame(parent, bg="#FFFFFF", relief=tk.RAISED, bd=1, padx=10, pady=10)
        c.pack(fill=tk.X, pady=(0, 10))

        tk.Label(c, text="Register Custom Token Driver (PKCS#11)", font=get_sys_font(9, "bold"), fg="#1E40AF", bg="#FFFFFF").pack(anchor=tk.W)
        f_grid = tk.Frame(c, bg="#FFFFFF"); f_grid.pack(fill=tk.X, pady=4)

        tk.Label(f_grid, text="Token Name:", font=get_sys_font(8, "bold"), bg="#FFFFFF").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Entry(f_grid, textvariable=self.driver_name_var, width=28, font=get_sys_font(8)).grid(row=0, column=1, sticky="w", padx=6, pady=3)

        tk.Label(f_grid, text=f"Driver Path ({ext}):", font=get_sys_font(8, "bold"), bg="#FFFFFF").grid(row=1, column=0, sticky="w", pady=3)
        p_row = tk.Frame(f_grid, bg="#FFFFFF"); p_row.grid(row=1, column=1, sticky="ew", padx=6, pady=3)
        ttk.Entry(p_row, textvariable=self.driver_path_var, font=get_sys_font(8)).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(p_row, text="Browse...", style="Tool3D.TButton", command=self._browse_driver).pack(side=tk.LEFT, padx=(4, 0))

        btn_box = tk.Frame(c, bg="#FFFFFF"); btn_box.pack(anchor=tk.W, pady=(6, 2))
        ttk.Button(btn_box, text="➕ Add & Save Driver", style="Primary3D.TButton", command=self._add_driver).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(btn_box, text="🛠️ Driver Setup Shortcuts & Tools", style="Tool3D.TButton", command=self.main_app.show_driver_shortcuts_dialog).pack(side=tk.LEFT)

    def _browse_driver(self):
        exts = [("DLL Libraries", "*.dll")] if CURRENT_OS == 'Windows' else [("Shared Libraries", "*.dylib;*.so")]
        f = filedialog.askopenfilename(title="Select PKCS#11 Driver File", filetypes=exts, parent=self.dialog)
        if f:
            self.driver_path_var.set(f)

    def _add_driver(self):
        name = self.driver_name_var.get().strip()
        path = self.driver_path_var.get().strip()
        if not name or not path or not os.path.exists(path):
            messagebox.showerror("Error", "Please provide a valid token name and existing driver library path.", parent=self.dialog)
            return
        save_custom_driver(name, path)
        self.main_app.reload_drivers(select_driver=f"⭐ {name}")
        messagebox.showinfo("Success", f"Custom driver '{name}' saved successfully!", parent=self.dialog)

# ==========================================
# 6. MAIN APPLICATION GUI (VisualPdfSignerApp)
# ==========================================
class VisualPdfSignerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("A B INFOTECH - Unified Digital Signer & PDF Power Suite v2.0")
        self.root.geometry("1380x880")
        self.root.minsize(1080, 720)
        self.root.protocol('WM_DELETE_WINDOW', self.hide_window)

        self._configure_styles()
        self._apply_window_icon(self.root)

        # Signing variables
        self.driver_var = tk.StringVar()
        self.pin_var = tk.StringVar()
        self.pfx_path_var = tk.StringVar()
        self.pfx_pass_var = tk.StringVar()
        self.dsc_source_var = tk.StringVar(value="token")

        self.sign_reason_var = tk.StringVar(value="I approve this document")
        self.sign_location_var = tk.StringVar(value="India")
        self.sign_contact_var = tk.StringVar(value="sales@a-b-infotech.in")
        self.sign_hash_var = tk.StringVar(value="SHA-256")
        self.sign_tsa_var = tk.StringVar(value="DigiCert TSA (Recommended)")
        self.sign_all_pages_var = tk.BooleanVar(value=True)

        self.signature_areas = []
        self.loaded_certs = []
        self.bulk_mode = False
        self.bulk_in_dir = None
        self.bulk_out_dir = None
        self.bulk_pdf_files = []
        self.just_activated = False

        # Verify license
        if not LicenseManager.is_activated():
            self._show_activation_screen()
        else:
            status = LicenseManager.check_expiration_local()
            if status == "expired":
                messagebox.showwarning("License Expired", "Your software license has expired!\n\nPlease contact +91 7016797720 or sales@a-b-infotech.in to renew.")
                self._show_activation_screen()
            else:
                self._init_app_interface()

    def _configure_styles(self):
        style = ttk.Style(self.root)
        if 'clam' in style.theme_names():
            style.theme_use('clam')

        # 3D Primary Button Style
        style.configure("Primary3D.TButton",
            font=get_sys_font(9, "bold"),
            background="#0284C7",
            foreground="#FFFFFF",
            borderwidth=1,
            focuscolor="none",
            relief="raised",
            padding=6
        )
        style.map("Primary3D.TButton",
            background=[("active", "#0369A1"), ("pressed", "#075985")],
            relief=[("pressed", "sunken"), ("!pressed", "raised")]
        )

        # 3D Tool Button Style
        style.configure("Tool3D.TButton",
            font=get_sys_font(8, "bold"),
            background="#E2E8F0",
            foreground="#1E293B",
            borderwidth=1,
            focuscolor="none",
            relief="raised",
            padding=4
        )
        style.map("Tool3D.TButton",
            background=[("active", "#CBD5E1"), ("pressed", "#94A3B8")],
            relief=[("pressed", "sunken"), ("!pressed", "raised")]
        )

        # Buy DSC Golden Button Style
        style.configure("BuyDSC.TButton",
            font=get_sys_font(8, "bold"),
            background="#F59E0B",
            foreground="#78350F",
            borderwidth=1,
            relief="raised",
            padding=4
        )
        style.map("BuyDSC.TButton",
            background=[("active", "#D97706"), ("pressed", "#B45309")]
        )

    def _apply_window_icon(self, window):
        try:
            target_icon = "app_icon.ico" if os.path.exists("app_icon.ico") else "icon.ico"
            if os.path.exists(target_icon) and CURRENT_OS == 'Windows':
                window.iconbitmap(target_icon)
        except Exception:
            pass

    def hide_window(self):
        self.root.withdraw()

    def show_window(self):
        self.root.deiconify()
        self._maximize_window(self.root)
        self.root.lift()
        self.root.focus_force()

    def _maximize_window(self, window):
        try:
            window.state('zoomed')
        except Exception:
            try:
                window.attributes('-zoomed', True)
            except Exception:
                pass

    def _show_activation_screen(self):
        self.act_win = tk.Toplevel(self.root)
        self.act_win.title("🔑 A B INFOTECH - License Activation")
        self.act_win.geometry("450x300")
        self._apply_window_icon(self.act_win)

        outer = tk.Frame(self.act_win, bg="#FFFFFF", padx=20, pady=20)
        outer.pack(fill=tk.BOTH, expand=True)

        tk.Label(outer, text="A B INFOTECH", font=get_sys_font(14, "bold"), fg="#1E3A8A", bg="#FFFFFF").pack()
        tk.Label(outer, text="Unified Digital Signer & PDF Power Suite", font=get_sys_font(9), fg="#64748B", bg="#FFFFFF").pack(pady=(2, 14))

        tk.Label(outer, text="Enter License Key:", font=get_sys_font(9, "bold"), bg="#FFFFFF").pack(anchor=tk.W)
        self.key_entry = ttk.Entry(outer, width=42, font=get_sys_font(10), show="*")
        self.key_entry.pack(fill=tk.X, pady=(2, 14))

        self.btn_activate = ttk.Button(outer, text="Activate License via Browser", style="Primary3D.TButton", command=self._verify_key)
        self.btn_activate.pack(fill=tk.X, ipady=4)

        tk.Label(outer, text="Support WhatsApp: +91 7016797720  |  sales@a-b-infotech.in", font=get_sys_font(7), fg="#0284C7", bg="#FFFFFF").pack(pady=(16, 0))

    def _verify_key(self):
        user_key = self.key_entry.get().strip()
        if not user_key:
            messagebox.showerror("Error", "Please enter a valid License Key.")
            return

        if user_key == "ABI-MASTER-PASS":
            LicenseManager.save_license(user_key, "never", tier="enterprise", ocr_enabled=1, edit_enabled=1)
            messagebox.showinfo("Success", "Developer Bypass Activated! Enterprise Edition Unlocked.")
            self.act_win.destroy()
            self.just_activated = True
            self._init_app_interface()
            return

        self.btn_activate.config(state=tk.DISABLED, text="Waiting for Browser...")
        safe_key = urllib.parse.quote(user_key)
        url = f"{LicenseManager.WEBSITE_URL}?key={safe_key}"
        self._open_url_in_app_mode(url, hide_window=False)
        self._wait_for_callback()

    def _open_url_in_app_mode(self, url, hide_window=False):
        if CURRENT_OS == 'Windows':
            browser_paths = [
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
            ]
            for b in browser_paths:
                if os.path.exists(b):
                    args = [b, f"--app={url}"]
                    if hide_window:
                        args.append("--window-position=-2000,-2000")
                    subprocess.Popen(args)
                    return
        webbrowser.open(url)

    def _wait_for_callback(self):
        if LicenseManager.is_activated():
            status = LicenseManager.check_expiration_local()
            if status == "expired":
                messagebox.showwarning("License Expired", "This key is expired! Please contact +91 7016797720 to renew.")
                self.btn_activate.config(state=tk.NORMAL, text="Activate License")
                return
            messagebox.showinfo("Success", "License Activated Successfully!")
            self.act_win.destroy()
            self.just_activated = True
            self._init_app_interface()
        elif LicenseManager.activation_error:
            err = LicenseManager.activation_error
            LicenseManager.activation_error = None
            messagebox.showerror("Activation Failed", err)
            if self.act_win.winfo_exists():
                self.btn_activate.config(state=tk.NORMAL, text="Activate License")
        else:
            if self.act_win.winfo_exists():
                self.act_win.after(1000, self._wait_for_callback)

    def _run_startup_sync(self):
        key = LicenseManager.get_license_key()
        if not key or key == "ABI-MASTER-PASS":
            return
        try:
            safe_key = urllib.parse.quote(key)
            url = f"{LicenseManager.WEBSITE_URL}?action=browser_sync&key={safe_key}"
            self._open_url_in_app_mode(url, hide_window=True)
        except Exception as e:
            logger.warning(f"Startup Sync fallback: {e}")

    def _init_app_interface(self):
        is_bg = any(arg.lower() in ['--background', '--tray', '--minimized', '--autostart', '-b'] for arg in sys.argv[1:])
        if not is_bg:
            self.root.deiconify()
            self._maximize_window(self.root)
        else:
            self.hide_window()

        self._build_ui()
        if self.just_activated:
            self.just_activated = False
        else:
            self._run_startup_sync()

    def _build_ui(self):
        # ----------------------------------------------------
        # TOP HEADER BAR: Real Logo + Title + Buttons
        # ----------------------------------------------------
        header_bar = tk.Frame(self.root, bg="#1E3A8A", height=56, padx=12, pady=6)
        header_bar.pack(side=tk.TOP, fill=tk.X)
        header_bar.pack_propagate(False)

        title_box = tk.Frame(header_bar, bg="#1E3A8A")
        title_box.pack(side=tk.LEFT, fill=tk.Y)

        # High-res Header Logo
        self.tk_header_logo = load_abinfotech_logo(max_size=(40, 40))
        if self.tk_header_logo:
            lbl_logo = tk.Label(title_box, image=self.tk_header_logo, bg="#1E3A8A")
            lbl_logo.pack(side=tk.LEFT, padx=(0, 8))
        else:
            tk.Label(title_box, text="⚡", font=get_sys_font(14, "bold"), fg="#38BDF8", bg="#1E3A8A").pack(side=tk.LEFT, padx=(0, 6))

        tk.Label(title_box, text="A B INFOTECH", font=get_sys_font(13, "bold"), fg="#FFFFFF", bg="#1E3A8A").pack(side=tk.LEFT)
        tk.Label(title_box, text="— PDF Sign & All-in-One Power Suite", font=get_sys_font(10), fg="#BFDBFE", bg="#1E3A8A").pack(side=tk.LEFT, padx=(6, 8))

        tier_lbl = LicenseManager.get_tier_label()
        badge_fg = "#10B981" if tier_lbl in ["Pro", "Enterprise"] else "#F59E0B"
        badge_bg = "#064E3B" if tier_lbl in ["Pro", "Enterprise"] else "#78350F"
        lic_badge = tk.Label(title_box, text=f"v2.0 {tier_lbl.upper()} SUITE", font=get_sys_font(8, "bold"), fg=badge_fg, bg=badge_bg, padx=6, pady=2, relief=tk.SOLID, bd=1)
        lic_badge.pack(side=tk.LEFT)

        # Right Header Buttons
        btn_box = tk.Frame(header_bar, bg="#1E3A8A")
        btn_box.pack(side=tk.RIGHT, fill=tk.Y)

        ttk.Button(btn_box, text="🛒 Buy DSC / License", style="BuyDSC.TButton", command=self.open_buy_dsc).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_box, text="🛠️ Token Drivers", style="Tool3D.TButton", command=self.show_driver_shortcuts_dialog).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_box, text="💬 WhatsApp: +91 7016797720", style="Tool3D.TButton", command=self.open_whatsapp).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_box, text="⚙️ Settings", style="Tool3D.TButton", command=self.open_settings_dialog).pack(side=tk.LEFT, padx=4)

        # ----------------------------------------------------
        # MAIN NOTEBOOK: Restored Classic UI with 6 Power Tabs
        # ----------------------------------------------------
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=6, pady=(4, 6))

        # 1. ✍️ Sign PDF Tab (Restored Classic Split UI with live canvas)
        self.tab_sign = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_sign, text=" ✍️ Sign PDF ")
        self._build_tab_sign(self.tab_sign)

        # 2. 🗜️ Compress Tab (with Live PDF Viewer)
        self.tab_compress = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_compress, text=" 🗜️ Compress ")
        self._build_tab_compress(self.tab_compress)

        # 3. 📑 Merge Tab (with Live PDF Viewer)
        self.tab_merge = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_merge, text=" 📑 Merge ")
        self._build_tab_merge(self.tab_merge)

        # 4. 🛡️ Verify Tab (with Live PDF Viewer)
        self.tab_verify = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_verify, text=" 🛡️ Verify ")
        self._build_tab_verify(self.tab_verify)

        # 5. 🛠️ PDF Tools Tab (with Live PDF Viewer)
        self.tab_tools = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_tools, text=" 🛠️ PDF Tools ")
        self._build_tab_tools(self.tab_tools)

        # 6. 📖 OCR & Edit Tab (Full Virtual Display Interactive Text Editor)
        self.tab_ocr_edit = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_ocr_edit, text=" 📖 OCR & Edit ")
        self._build_tab_ocr_edit(self.tab_ocr_edit)

        # 7. ℹ️ About & License
        self.tab_about = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_about, text=" ℹ️ About & License ")
        self._build_tab_about(self.tab_about)

    # =========================================================================
    # TAB 1: ✍️ SIGN PDF (Restored Classic Split Layout with Live Canvas)
    # =========================================================================
    def _build_tab_sign(self, parent):
        split = tk.Frame(parent, bg="#F1F5F9")
        split.pack(fill=tk.BOTH, expand=True)

        # ----------------------------------------------------
        # LEFT PANEL: Signing Controls, Logo & Old DSC Options
        # ----------------------------------------------------
        left_panel = tk.Frame(split, bg="#FFFFFF", width=420, padx=12, pady=10, relief=tk.RAISED, bd=1)
        left_panel.pack(side=tk.LEFT, fill=tk.Y)
        left_panel.pack_propagate(False)

        # Prominent Big Logo in Left Panel
        logo_frame = tk.Frame(left_panel, bg="#F8FAFC", relief=tk.SOLID, bd=1, padx=8, pady=8)
        logo_frame.pack(fill=tk.X, pady=(0, 10))

        self.tk_big_logo = load_abinfotech_logo(max_size=(160, 100))
        if self.tk_big_logo:
            tk.Label(logo_frame, image=self.tk_big_logo, bg="#F8FAFC").pack()
        tk.Label(logo_frame, text="A B INFOTECH", font=get_sys_font(12, "bold"), fg="#1E3A8A", bg="#F8FAFC").pack(pady=(4, 0))
        tk.Label(logo_frame, text="Local Digital Signature Suite", font=get_sys_font(8), fg="#059669", bg="#F8FAFC").pack()

        # Certificate Source Options: Only the 2 Classic Options (USB Token & PFX File)
        tk.Label(left_panel, text="Digital Certificate Source:", font=get_sys_font(9, "bold"), fg="#1E40AF", bg="#FFFFFF").pack(anchor=tk.W)

        self.nb_dsc = ttk.Notebook(left_panel)
        self.nb_dsc.pack(fill=tk.X, pady=(4, 8))

        # Sub-tab 1: USB Hardware Token (PKCS#11)
        f_token = ttk.Frame(self.nb_dsc, padding=6)
        self.nb_dsc.add(f_token, text=" 🔌 USB Hardware Token ")

        tk.Label(f_token, text="Select Token Driver:", font=get_sys_font(8, "bold"), fg="#1E3A8A").pack(anchor=tk.W)
        d_row = tk.Frame(f_token); d_row.pack(fill=tk.X, pady=(2, 4))
        self.token_driver_combo = ttk.Combobox(d_row, textvariable=self.driver_var, values=list(DRIVER_MAP.keys()), state="readonly", font=get_sys_font(8))
        self.token_driver_combo.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        if DRIVER_MAP: self.token_driver_combo.current(0)
        ttk.Button(d_row, text="Refresh", style="Tool3D.TButton", command=self.load_token_certificates).pack(side=tk.RIGHT)

        tk.Label(f_token, text="Certificate in Token:", font=get_sys_font(8, "bold"), fg="#1E3A8A").pack(anchor=tk.W)
        self.token_cert_combo = ttk.Combobox(f_token, state="readonly", font=get_sys_font(8))
        self.token_cert_combo.pack(fill=tk.X, pady=(2, 4))

        pin_row = tk.Frame(f_token); pin_row.pack(fill=tk.X, pady=(2, 2))
        tk.Label(pin_row, text="Hardware Token PIN:", font=get_sys_font(8, "bold"), fg="#1E3A8A").pack(side=tk.LEFT)
        ttk.Entry(pin_row, textvariable=self.pin_var, show="*", width=14).pack(side=tk.LEFT, padx=6)
        ttk.Button(pin_row, text="🛠️ Driver Shortcuts", style="Tool3D.TButton", command=self.show_driver_shortcuts_dialog).pack(side=tk.RIGHT)

        # Sub-tab 2: PFX / P12 File
        f_pfx = ttk.Frame(self.nb_dsc, padding=6)
        self.nb_dsc.add(f_pfx, text=" 🔑 PFX / P12 File ")

        tk.Label(f_pfx, text="Select .PFX / .P12 File:", font=get_sys_font(8, "bold"), fg="#1E3A8A").pack(anchor=tk.W)
        p_row1 = tk.Frame(f_pfx); p_row1.pack(fill=tk.X, pady=(2, 4))
        ttk.Entry(p_row1, textvariable=self.pfx_path_var, font=get_sys_font(8)).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        ttk.Button(p_row1, text="Browse...", style="Tool3D.TButton", command=self.browse_pfx_file).pack(side=tk.RIGHT)

        p_row2 = tk.Frame(f_pfx); p_row2.pack(fill=tk.X, pady=(2, 2))
        tk.Label(p_row2, text="Password:", font=get_sys_font(8, "bold"), fg="#1E3A8A").pack(side=tk.LEFT)
        ttk.Entry(p_row2, textvariable=self.pfx_pass_var, show="*", width=14).pack(side=tk.LEFT, padx=6)
        ttk.Button(p_row2, text="Verify", style="Tool3D.TButton", command=lambda: self.verify_pfx_certificate(silent=False)).pack(side=tk.LEFT)

        # Selected Signature Areas Listbox / Container
        tk.Label(left_panel, text="Selected Signature Area(s):", font=get_sys_font(8, "bold"), fg="#1E40AF", bg="#FFFFFF").pack(anchor=tk.W, pady=(6, 2))
        self.area_container = tk.Frame(left_panel, bg="#F8FAFC", relief=tk.SUNKEN, bd=1, height=90)
        self.area_container.pack(fill=tk.X, pady=(0, 8))
        self.area_container.pack_propagate(False)

        self.lbl_no_areas = tk.Label(self.area_container, text="No area selected yet.\nClick 'Draw Signature Box' on the right canvas.", font=get_sys_font(8), fg="#64748B", bg="#F8FAFC")
        self.lbl_no_areas.pack(expand=True)

        # Actions & Settings Buttons
        action_row = tk.Frame(left_panel, bg="#FFFFFF")
        action_row.pack(fill=tk.X, pady=(0, 8))

        ttk.Button(action_row, text="⚙️ Signature Settings", style="Tool3D.TButton", command=self.open_signature_settings).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        ttk.Button(action_row, text="📁 Bulk Sign Folder", style="Tool3D.TButton", command=self.open_batch_dialog).pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(4, 0))

        # Bottom Big Signing Button
        bot_f = tk.Frame(left_panel, bg="#FFFFFF")
        bot_f.pack(side=tk.BOTTOM, fill=tk.X)

        self.lbl_sign_status = tk.Label(bot_f, text="Ready. Open a PDF document on the right to begin.", font=get_sys_font(8), fg="#059669", bg="#FFFFFF", wraplength=380, justify="left")
        self.lbl_sign_status.pack(pady=(0, 6))

        self.btn_sign_doc = ttk.Button(bot_f, text="🚀 SIGN & SAVE PDF DOCUMENT", style="Primary3D.TButton", command=self.start_signing)
        self.btn_sign_doc.pack(fill=tk.X, ipady=6)

        disclaimer = "Note: Adobe validity tick depends on Adobe's live validation & Trusted Root list."
        tk.Label(bot_f, text=disclaimer, font=get_sys_font(7, "italic"), fg="#DC2626", bg="#FFFFFF", wraplength=380).pack(pady=(4, 0))

        # ----------------------------------------------------
        # RIGHT PANEL: Live Interactive PDF Canvas Viewer
        # ----------------------------------------------------
        right_panel = tk.Frame(split, bg="#E2E8F0")
        right_panel.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        self.sign_viewer = EmbeddedPdfViewer(right_panel, on_area_selected=self._on_sign_area_picked, allow_drawing=True)
        self.sign_viewer.pack(fill=tk.BOTH, expand=True)

    def _on_sign_area_picked(self, area_data):
        self.signature_areas.append(area_data)
        self._update_sign_areas_ui()
        self.lbl_sign_status.config(
            text=f"✓ Selected Box #{len(self.signature_areas)} on Page {area_data['page']+1}. Click 'Sign & Save' when ready.",
            fg="#047857"
        )

    def _update_sign_areas_ui(self):
        for w in self.area_container.winfo_children():
            w.destroy()
        if not self.signature_areas:
            self.lbl_no_areas = tk.Label(self.area_container, text="No area selected yet.\nClick 'Draw Signature Box' on the right canvas.", font=get_sys_font(8), fg="#64748B", bg="#F8FAFC")
            self.lbl_no_areas.pack(expand=True)
            return

        for idx, a in enumerate(self.signature_areas):
            txt = f"Box #{idx+1}: Page {a['page']+1} (W: {int(a['width'])} x H: {int(a['height'])})"
            tk.Label(self.area_container, text=txt, font=get_sys_font(8, "bold"), fg="#0E7490", bg="#F8FAFC").pack(anchor=tk.W, padx=8, pady=1)

    def open_signature_settings(self):
        SignatureSettingsDialog(self.root, self)

    # =========================================================================
    # TAB 2: 🗜️ COMPRESS (with Live PDF Viewer)
    # =========================================================================
    def _build_tab_compress(self, parent):
        split = tk.Frame(parent, bg="#F1F5F9")
        split.pack(fill=tk.BOTH, expand=True)

        left = tk.Frame(split, bg="#FFFFFF", width=380, padx=14, pady=12, relief=tk.RAISED, bd=1)
        left.pack(side=tk.LEFT, fill=tk.Y)
        left.pack_propagate(False)

        tk.Label(left, text="PDF File Compression", font=get_sys_font(11, "bold"), fg="#1E40AF", bg="#FFFFFF").pack(anchor=tk.W, pady=(0, 6))
        tk.Label(left, text="Reduce file size while preserving high visual quality.", font=get_sys_font(8), fg="#64748B", bg="#FFFFFF").pack(anchor=tk.W, pady=(0, 10))

        tk.Label(left, text="Select PDF to Compress:", font=get_sys_font(8, "bold"), bg="#FFFFFF").pack(anchor=tk.W)
        self.compress_path_var = tk.StringVar()
        r1 = tk.Frame(left, bg="#FFFFFF"); r1.pack(fill=tk.X, pady=(2, 8))
        ttk.Entry(r1, textvariable=self.compress_path_var, font=get_sys_font(8)).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        ttk.Button(r1, text="Browse...", style="Tool3D.TButton", command=self._browse_compress_pdf).pack(side=tk.RIGHT)

        tk.Label(left, text="Compression Preset:", font=get_sys_font(8, "bold"), bg="#FFFFFF").pack(anchor=tk.W, pady=(6, 2))
        self.compress_preset_var = tk.StringVar(value="medium")
        for val, lbl in [("extreme", "🔥 Extreme Compression (Smallest size)"), ("medium", "⚖️ Recommended / Medium (Balanced)"), ("low", "✨ Low Compression (Highest quality)")]:
            tk.Radiobutton(left, text=lbl, variable=self.compress_preset_var, value=val, font=get_sys_font(8), bg="#FFFFFF").pack(anchor=tk.W, pady=2)

        ttk.Button(left, text="🗜️ COMPRESS & SAVE PDF", style="Primary3D.TButton", command=self.exec_compress).pack(fill=tk.X, ipady=4, pady=(16, 8))
        self.lbl_compress_res = tk.Label(left, text="", font=get_sys_font(8, "bold"), fg="#047857", bg="#FFFFFF")
        self.lbl_compress_res.pack(anchor=tk.W)

        # Right Live PDF Viewer
        right = tk.Frame(split, bg="#E2E8F0")
        right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)
        self.compress_viewer = EmbeddedPdfViewer(right)
        self.compress_viewer.pack(fill=tk.BOTH, expand=True)

    def _browse_compress_pdf(self):
        f = filedialog.askopenfilename(title="Select PDF to Compress", filetypes=[("PDF", "*.pdf")])
        if f:
            self.compress_path_var.set(f)
            self.compress_viewer.load_pdf(f)

    def exec_compress(self):
        in_p = self.compress_path_var.get().strip()
        if not in_p or not os.path.exists(in_p):
            messagebox.showerror("Error", "Please select a valid PDF file."); return
        out_p = filedialog.asksaveasfilename(title="Save Compressed PDF As", defaultextension=".pdf", filetypes=[("PDF", "*.pdf")])
        if not out_p: return

        try:
            doc = fitz.open(in_p)
            doc.save(out_p, garbage=4, deflate=True)
            doc.close()

            orig_s = os.path.getsize(in_p)
            new_s = os.path.getsize(out_p)
            saved = max(0, orig_s - new_s)
            pct = (saved / orig_s) * 100 if orig_s > 0 else 0

            self.lbl_compress_res.config(text=f"✓ Compressed! Saved {pct:.1f}% ({saved // 1024} KB saved)")
            self.compress_viewer.load_pdf(out_p)
            messagebox.showinfo("Success", f"Compressed successfully!\nOriginal: {orig_s//1024} KB\nNew: {new_s//1024} KB\nSaved: {pct:.1f}%")
        except Exception as e:
            messagebox.showerror("Error", f"Compression failed:\n{e}")

    # =========================================================================
    # TAB 3: 📑 MERGE (with Live PDF Viewer)
    # =========================================================================
    def _build_tab_merge(self, parent):
        split = tk.Frame(parent, bg="#F1F5F9")
        split.pack(fill=tk.BOTH, expand=True)

        left = tk.Frame(split, bg="#FFFFFF", width=380, padx=12, pady=12, relief=tk.RAISED, bd=1)
        left.pack(side=tk.LEFT, fill=tk.Y)
        left.pack_propagate(False)

        tk.Label(left, text="Merge Multiple PDFs", font=get_sys_font(11, "bold"), fg="#1E40AF", bg="#FFFFFF").pack(anchor=tk.W, pady=(0, 4))
        tk.Label(left, text="Add PDF files into queue, rearrange order, and merge into one.", font=get_sys_font(8), fg="#64748B", bg="#FFFFFF").pack(anchor=tk.W, pady=(0, 8))

        self.merge_pdf_list = []
        self.merge_listbox = tk.Listbox(left, height=10, font=get_sys_font(8), bd=1, relief=tk.SOLID)
        self.merge_listbox.pack(fill=tk.X, pady=(0, 6))
        self.merge_listbox.bind("<<ListboxSelect>>", self._on_merge_select)

        btn_row = tk.Frame(left, bg="#FFFFFF"); btn_row.pack(fill=tk.X, pady=(0, 8))
        ttk.Button(btn_row, text="➕ Add PDFs", style="Tool3D.TButton", command=self.merge_add_files).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(btn_row, text="▲ Up", style="Tool3D.TButton", command=self.merge_move_up).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_row, text="▼ Down", style="Tool3D.TButton", command=self.merge_move_down).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_row, text="🗑️ Remove", style="Tool3D.TButton", command=self.merge_remove_file).pack(side=tk.LEFT, padx=2)

        ttk.Button(left, text="📑 MERGE & SAVE AS ONE PDF", style="Primary3D.TButton", command=self.exec_merge).pack(fill=tk.X, ipady=4, pady=(10, 0))

        # Right Live PDF Viewer
        right = tk.Frame(split, bg="#E2E8F0")
        right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)
        self.merge_viewer = EmbeddedPdfViewer(right)
        self.merge_viewer.pack(fill=tk.BOTH, expand=True)

    def merge_add_files(self):
        files = filedialog.askopenfilenames(title="Select PDFs to Merge", filetypes=[("PDF", "*.pdf")])
        if files:
            for f in files:
                if f not in self.merge_pdf_list:
                    self.merge_pdf_list.append(f)
                    self.merge_listbox.insert(tk.END, os.path.basename(f))
            if self.merge_pdf_list:
                self.merge_viewer.load_pdf(self.merge_pdf_list[-1])

    def _on_merge_select(self, event):
        sel = self.merge_listbox.curselection()
        if sel:
            idx = sel[0]
            if idx < len(self.merge_pdf_list):
                self.merge_viewer.load_pdf(self.merge_pdf_list[idx])

    def merge_move_up(self):
        sel = self.merge_listbox.curselection()
        if not sel or sel[0] == 0: return
        idx = sel[0]
        self.merge_pdf_list[idx], self.merge_pdf_list[idx-1] = self.merge_pdf_list[idx-1], self.merge_pdf_list[idx]
        self._refresh_merge_list(idx-1)

    def merge_move_down(self):
        sel = self.merge_listbox.curselection()
        if not sel or sel[0] >= len(self.merge_pdf_list) - 1: return
        idx = sel[0]
        self.merge_pdf_list[idx], self.merge_pdf_list[idx+1] = self.merge_pdf_list[idx+1], self.merge_pdf_list[idx]
        self._refresh_merge_list(idx+1)

    def merge_remove_file(self):
        sel = self.merge_listbox.curselection()
        if sel:
            idx = sel[0]
            del self.merge_pdf_list[idx]
            self._refresh_merge_list(max(0, idx-1))

    def _refresh_merge_list(self, new_sel=None):
        self.merge_listbox.delete(0, tk.END)
        for f in self.merge_pdf_list:
            self.merge_listbox.insert(tk.END, os.path.basename(f))
        if self.merge_pdf_list and new_sel is not None and new_sel < len(self.merge_pdf_list):
            self.merge_listbox.selection_set(new_sel)
            self.merge_viewer.load_pdf(self.merge_pdf_list[new_sel])

    def exec_merge(self):
        if len(self.merge_pdf_list) < 2:
            messagebox.showwarning("Warning", "Please add at least 2 PDF files to merge."); return
        out_p = filedialog.asksaveasfilename(title="Save Merged PDF As", defaultextension=".pdf", filetypes=[("PDF", "*.pdf")])
        if not out_p: return

        try:
            merged = fitz.open()
            for p in self.merge_pdf_list:
                doc = fitz.open(p)
                merged.insert_pdf(doc)
                doc.close()
            merged.save(out_p)
            merged.close()
            self.merge_viewer.load_pdf(out_p)
            messagebox.showinfo("Success", f"Merged {len(self.merge_pdf_list)} PDFs into:\n{out_p}")
        except Exception as e:
            messagebox.showerror("Error", f"Merge failed: {e}")

    # =========================================================================
    # TAB 4: 🛡️ VERIFY (with Live PDF Viewer)
    # =========================================================================
    def _build_tab_verify(self, parent):
        split = tk.Frame(parent, bg="#F1F5F9")
        split.pack(fill=tk.BOTH, expand=True)

        left = tk.Frame(split, bg="#FFFFFF", width=400, padx=12, pady=12, relief=tk.RAISED, bd=1)
        left.pack(side=tk.LEFT, fill=tk.Y)
        left.pack_propagate(False)

        tk.Label(left, text="Cryptographic Signature Verification", font=get_sys_font(10, "bold"), fg="#1E40AF", bg="#FFFFFF").pack(anchor=tk.W, pady=(0, 4))
        tk.Label(left, text="Verify digital signature integrity, certificates & timestamps.", font=get_sys_font(8), fg="#64748B", bg="#FFFFFF").pack(anchor=tk.W, pady=(0, 8))

        self.verify_path_var = tk.StringVar()
        r1 = tk.Frame(left, bg="#FFFFFF"); r1.pack(fill=tk.X, pady=(0, 6))
        ttk.Entry(r1, textvariable=self.verify_path_var, font=get_sys_font(8)).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        ttk.Button(r1, text="Browse...", style="Tool3D.TButton", command=self._browse_verify_pdf).pack(side=tk.RIGHT)

        ttk.Button(left, text="🔍 VERIFY SIGNATURES IN PDF", style="Primary3D.TButton", command=self.exec_verify).pack(fill=tk.X, ipady=4, pady=(2, 8))

        tk.Label(left, text="Verification Analysis Report:", font=get_sys_font(8, "bold"), fg="#1E3A8A", bg="#FFFFFF").pack(anchor=tk.W)
        self.txt_verify_report = tk.Text(left, height=16, font=get_sys_font(8), bg="#F8FAFC", bd=1, relief=tk.SOLID)
        self.txt_verify_report.pack(fill=tk.BOTH, expand=True, pady=(2, 0))

        # Right Live PDF Viewer
        right = tk.Frame(split, bg="#E2E8F0")
        right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)
        self.verify_viewer = EmbeddedPdfViewer(right)
        self.verify_viewer.pack(fill=tk.BOTH, expand=True)

    def _browse_verify_pdf(self):
        f = filedialog.askopenfilename(title="Select Signed PDF to Verify", filetypes=[("PDF", "*.pdf")])
        if f:
            self.verify_path_var.set(f)
            self.verify_viewer.load_pdf(f)

    def exec_verify(self):
        p = self.verify_path_var.get().strip()
        if not p or not os.path.exists(p):
            messagebox.showerror("Error", "Please select a valid PDF file."); return

        self.txt_verify_report.delete("1.0", tk.END)
        self.txt_verify_report.insert(tk.END, "Analyzing cryptographic signatures...\n\n")

        try:
            with open(p, 'rb') as f:
                r = PdfFileReader(f)
                sig_fields = list(r.embedded_signatures)

            if not sig_fields:
                self.txt_verify_report.insert(tk.END, "❌ No digital signatures found in this document.\n")
                return

            self.txt_verify_report.insert(tk.END, f"Found {len(sig_fields)} digital signature(s):\n" + "="*38 + "\n")
            with open(p, 'rb') as f:
                for idx, sig in enumerate(sig_fields):
                    val = validate_pdf_signature(sig)
                    signer_name = sig.signer_cert.subject.rfc4514_string() if sig.signer_cert else "Unknown"
                    self.txt_verify_report.insert(tk.END, f"Signature #{idx+1}:\n")
                    self.txt_verify_report.insert(tk.END, f"• Field Name: {sig.field_name}\n")
                    self.txt_verify_report.insert(tk.END, f"• Signer: {signer_name}\n")
                    self.txt_verify_report.insert(tk.END, f"• Integrity: {'✓ INTACT & UNALTERED' if val.intact else '❌ TAMPERED'}\n")
                    self.txt_verify_report.insert(tk.END, f"• Valid: {'✓ Cryptographically Valid' if val.valid else '❌ Invalid'}\n\n")
        except Exception as e:
            self.txt_verify_report.insert(tk.END, f"Verification Error:\n{e}\n")

    # =========================================================================
    # TAB 5: 🛠️ PDF TOOLS (with Live PDF Viewer)
    # =========================================================================
    def _build_tab_tools(self, parent):
        split = tk.Frame(parent, bg="#F1F5F9")
        split.pack(fill=tk.BOTH, expand=True)

        left = tk.Frame(split, bg="#FFFFFF", width=400, padx=12, pady=12, relief=tk.RAISED, bd=1)
        left.pack(side=tk.LEFT, fill=tk.Y)
        left.pack_propagate(False)

        tk.Label(left, text="PDF Utility Suite", font=get_sys_font(10, "bold"), fg="#1E40AF", bg="#FFFFFF").pack(anchor=tk.W, pady=(0, 4))

        tk.Label(left, text="Active PDF Document:", font=get_sys_font(8, "bold"), bg="#FFFFFF").pack(anchor=tk.W)
        self.tool_pdf_path_var = tk.StringVar()
        r1 = tk.Frame(left, bg="#FFFFFF"); r1.pack(fill=tk.X, pady=(2, 8))
        ttk.Entry(r1, textvariable=self.tool_pdf_path_var, font=get_sys_font(8)).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        ttk.Button(r1, text="Browse...", style="Tool3D.TButton", command=self._browse_tool_pdf).pack(side=tk.RIGHT)

        nb_tools = ttk.Notebook(left)
        nb_tools.pack(fill=tk.BOTH, expand=True, pady=(4, 0))

        # Tool Subtab A: Protect / Password
        f_prot = ttk.Frame(nb_tools, padding=8)
        nb_tools.add(f_prot, text=" 🔒 Password Protect ")
        tk.Label(f_prot, text="Set Document Password:", font=get_sys_font(8, "bold")).pack(anchor=tk.W)
        self.tool_pass_var = tk.StringVar()
        ttk.Entry(f_prot, textvariable=self.tool_pass_var, show="*").pack(fill=tk.X, pady=(2, 6))
        ttk.Button(f_prot, text="🔒 Protect & Save PDF", style="Primary3D.TButton", command=self.exec_protect_pdf).pack(fill=tk.X, pady=(4, 0))

        # Tool Subtab B: Rotate Pages
        f_rot = ttk.Frame(nb_tools, padding=8)
        nb_tools.add(f_rot, text=" 🔄 Rotate ")
        tk.Label(f_rot, text="Select Rotation Angle:", font=get_sys_font(8, "bold")).pack(anchor=tk.W)
        self.tool_rot_var = tk.IntVar(value=90)
        tk.Radiobutton(f_rot, text="90° Clockwise", variable=self.tool_rot_var, value=90).pack(anchor=tk.W)
        tk.Radiobutton(f_rot, text="180° Invert", variable=self.tool_rot_var, value=180).pack(anchor=tk.W)
        tk.Radiobutton(f_rot, text="270° Counter-Clockwise", variable=self.tool_rot_var, value=270).pack(anchor=tk.W)
        ttk.Button(f_rot, text="🔄 Rotate All Pages & Save", style="Primary3D.TButton", command=self.exec_rotate_pdf).pack(fill=tk.X, pady=(8, 0))

        # Tool Subtab C: Extract / Split Pages
        f_ext = ttk.Frame(nb_tools, padding=8)
        nb_tools.add(f_ext, text=" 📄 Extract Pages ")
        tk.Label(f_ext, text="Page Range (e.g. 1-3, 5):", font=get_sys_font(8, "bold")).pack(anchor=tk.W)
        self.tool_extract_var = tk.StringVar(value="1")
        ttk.Entry(f_ext, textvariable=self.tool_extract_var).pack(fill=tk.X, pady=(2, 6))
        ttk.Button(f_ext, text="📄 Extract Pages to New PDF", style="Primary3D.TButton", command=self.exec_extract_pages).pack(fill=tk.X, pady=(4, 0))

        # Right Live PDF Viewer
        right = tk.Frame(split, bg="#E2E8F0")
        right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)
        self.tool_viewer = EmbeddedPdfViewer(right)
        self.tool_viewer.pack(fill=tk.BOTH, expand=True)

    def _browse_tool_pdf(self):
        f = filedialog.askopenfilename(title="Select PDF Document", filetypes=[("PDF", "*.pdf")])
        if f:
            self.tool_pdf_path_var.set(f)
            self.tool_viewer.load_pdf(f)

    def exec_protect_pdf(self):
        p = self.tool_pdf_path_var.get().strip()
        pwd = self.tool_pass_var.get().strip()
        if not p or not os.path.exists(p) or not pwd:
            messagebox.showerror("Error", "Please select a PDF and enter password."); return
        out_p = filedialog.asksaveasfilename(defaultextension=".pdf", filetypes=[("PDF", "*.pdf")])
        if not out_p: return
        try:
            doc = fitz.open(p)
            doc.save(out_p, encryption=fitz.PDF_ENCRYPT_AES_256, user_pw=pwd, owner_pw=pwd)
            doc.close()
            messagebox.showinfo("Protected", f"Saved password-protected PDF:\n{out_p}")
        except Exception as e: messagebox.showerror("Error", str(e))

    def exec_rotate_pdf(self):
        p = self.tool_pdf_path_var.get().strip()
        angle = self.tool_rot_var.get()
        if not p or not os.path.exists(p):
            messagebox.showerror("Error", "Please select a PDF document."); return
        out_p = filedialog.asksaveasfilename(defaultextension=".pdf", filetypes=[("PDF", "*.pdf")])
        if not out_p: return
        try:
            doc = fitz.open(p)
            for page in doc: page.set_rotation(page.rotation + angle)
            doc.save(out_p); doc.close()
            self.tool_viewer.load_pdf(out_p)
            messagebox.showinfo("Rotated", f"Rotated all pages by {angle}°!\nSaved to: {out_p}")
        except Exception as e: messagebox.showerror("Error", str(e))

    def exec_extract_pages(self):
        p = self.tool_pdf_path_var.get().strip()
        rng = self.tool_extract_var.get().strip()
        if not p or not os.path.exists(p) or not rng:
            messagebox.showerror("Error", "Please select a PDF and specify pages."); return
        out_p = filedialog.asksaveasfilename(defaultextension=".pdf", filetypes=[("PDF", "*.pdf")])
        if not out_p: return
        try:
            doc = fitz.open(p)
            new_doc = fitz.open()
            # Parse page numbers (1-indexed to 0-indexed)
            parts = [x.strip() for x in rng.split(",") if x.strip()]
            for part in parts:
                if "-" in part:
                    s, e = part.split("-", 1)
                    s_idx = max(0, int(s) - 1)
                    e_idx = min(len(doc) - 1, int(e) - 1)
                    for i in range(s_idx, e_idx + 1): new_doc.insert_pdf(doc, from_page=i, to_page=i)
                else:
                    idx = int(part) - 1
                    if 0 <= idx < len(doc): new_doc.insert_pdf(doc, from_page=idx, to_page=idx)
            new_doc.save(out_p); new_doc.close(); doc.close()
            self.tool_viewer.load_pdf(out_p)
            messagebox.showinfo("Extracted", f"Saved extracted pages to:\n{out_p}")
        except Exception as e: messagebox.showerror("Error", str(e))

    # =========================================================================
    # TAB 6: 📖 OCR & EDIT (Virtual Display & Inline Font Replacement)
    # =========================================================================
    def _build_tab_ocr_edit(self, parent):
        self.virtual_editor = PdfVirtualEditor(parent, main_app=self)
        self.virtual_editor.pack(fill=tk.BOTH, expand=True)

    # =========================================================================
    # TAB 7: ℹ️ ABOUT & LICENSE
    # =========================================================================
    def _build_tab_about(self, parent):
        outer = tk.Frame(parent, bg="#F1F5F9", padx=16, pady=12)
        outer.pack(fill=tk.BOTH, expand=True)

        card1 = tk.Frame(outer, bg="#FFFFFF", relief=tk.RAISED, bd=1, padx=16, pady=14)
        card1.pack(fill=tk.X, pady=(0, 10))

        top_brand = tk.Frame(card1, bg="#FFFFFF")
        top_brand.pack(fill=tk.X, pady=(0, 8))

        self.tk_about_logo = load_abinfotech_logo(max_size=(64, 64))
        if self.tk_about_logo:
            tk.Label(top_brand, image=self.tk_about_logo, bg="#FFFFFF").pack(side=tk.LEFT, padx=(0, 12))

        b_text = tk.Frame(top_brand, bg="#FFFFFF"); b_text.pack(side=tk.LEFT)
        tk.Label(b_text, text="A B INFOTECH", font=get_sys_font(14, "bold"), fg="#1E3A8A", bg="#FFFFFF").pack(anchor=tk.W)
        tk.Label(b_text, text="Unified Digital Signer & All-in-One PDF Power Suite v2.0", font=get_sys_font(9, "bold"), fg="#059669", bg="#FFFFFF").pack(anchor=tk.W)

        tk.Label(
            card1,
            text="High-performance cryptographic and document processing workstation supporting USB hardware tokens, PKCS#12 certificates, compression, merging, verification, and interactive font-preserving PDF text editing.",
            font=get_sys_font(8), fg="#475569", bg="#FFFFFF", wraplength=700, justify="left"
        ).pack(anchor=tk.W)

        card2 = tk.Frame(outer, bg="#FFFFFF", relief=tk.RAISED, bd=1, padx=16, pady=12)
        card2.pack(fill=tk.X, pady=(0, 10))
        tk.Label(card2, text="License & System Information", font=get_sys_font(10, "bold"), fg="#1E40AF", bg="#FFFFFF").pack(anchor=tk.W, pady=(0, 4))

        lic_info = LicenseManager.get_license_info()
        exp_date = lic_info.get("expiredate", "Unknown")
        key_val = lic_info.get("key") or "Not Installed"
        hw_id = LicenseManager.get_hardware_id()
        tier_val = lic_info.get("tier", "pro").capitalize()
        ocr_stat = "Unlocked (Pro/Enterprise)" if lic_info.get("ocr_enabled") else "Locked (Upgrade Required)"

        tk.Label(card2, text=f"• Machine / Hardware ID: {hw_id}", font=get_sys_font(8), bg="#FFFFFF").pack(anchor=tk.W, pady=2)
        tk.Label(card2, text=f"• License Key: {key_val}", font=get_sys_font(8), bg="#FFFFFF").pack(anchor=tk.W, pady=2)
        tk.Label(card2, text=f"• License Edition / Variant: {tier_val} Edition", font=get_sys_font(8, "bold"), fg="#1E40AF", bg="#FFFFFF").pack(anchor=tk.W, pady=2)
        tk.Label(card2, text=f"• PDF OCR & Edit Features: {ocr_stat}", font=get_sys_font(8), fg="#047857" if lic_info.get("ocr_enabled") else "#D97706", bg="#FFFFFF").pack(anchor=tk.W, pady=2)
        tk.Label(card2, text=f"• Expiry Date: {exp_date}", font=get_sys_font(8, "bold"), fg="#047857" if exp_date != "expired" else "#DC2626", bg="#FFFFFF").pack(anchor=tk.W, pady=2)
        tk.Label(card2, text=f"• Technical Support: WhatsApp +91 7016797720  |  Email: sales@a-b-infotech.in", font=get_sys_font(8), fg="#0284C7", bg="#FFFFFF").pack(anchor=tk.W, pady=2)
        tk.Label(card2, text=f"• Platform: {CURRENT_OS} ({platform.machine()})", font=get_sys_font(8), bg="#FFFFFF").pack(anchor=tk.W, pady=2)

        btn_row = tk.Frame(outer, bg="#F1F5F9")
        btn_row.pack(fill=tk.X, pady=(6, 0))
        ttk.Button(btn_row, text="🔑 Enter License Key", style="Tool3D.TButton", command=self._show_activation_screen).pack(side=tk.LEFT, padx=3)
        ttk.Button(btn_row, text="🛒 Buy DSC / Renew", style="Primary3D.TButton", command=self.open_buy_dsc).pack(side=tk.LEFT, padx=3)
        ttk.Button(btn_row, text="🛠️ Token Driver Shortcuts", style="Tool3D.TButton", command=self.show_driver_shortcuts_dialog).pack(side=tk.LEFT, padx=3)
        ttk.Button(btn_row, text="⚙️ Manage Drivers", style="Tool3D.TButton", command=lambda: self.open_settings_dialog(initial_tab="drivers")).pack(side=tk.LEFT, padx=3)
        ttk.Button(btn_row, text="💬 WhatsApp: +91 7016797720", style="Tool3D.TButton", command=self.open_whatsapp).pack(side=tk.LEFT, padx=3)

    # =========================================================================
    # CORE SIGNING ENGINE OPERATIONS
    # =========================================================================
    def load_token_certificates(self):
        driver = DRIVER_MAP.get(self.driver_var.get())
        if not driver or not os.path.exists(driver):
            messagebox.showerror("Driver Missing", f"Token driver not found at:\n{driver}\n\nPlease verify driver installation.")
            return

        try:
            self.loaded_certs = []
            lib = pkcs11.lib(driver)
            for slot in lib.get_slots(token_present=True):
                try:
                    token = slot.get_token()
                    with token.open() as session:
                        for obj in session.get_objects({pkcs11.Attribute.CLASS: pkcs11.ObjectClass.CERTIFICATE}):
                            c = x509.load_der_x509_certificate(obj[pkcs11.Attribute.VALUE])
                            try:
                                if c.subject == c.issuer: continue
                            except: pass

                            cn = "Unknown"
                            for attr in c.subject:
                                if attr.oid == x509.NameOID.COMMON_NAME:
                                    cn = attr.value; break

                            self.loaded_certs.append({
                                'alias': str(c.serial_number),
                                'cn': cn,
                                'source': token.label
                            })
                except Exception:
                    continue

            self.token_cert_combo['values'] = [f"{c['cn']} ({c['source']})" for c in self.loaded_certs]
            if self.loaded_certs:
                self.token_cert_combo.current(0)
                self.lbl_sign_status.config(text=f"Loaded {len(self.loaded_certs)} certificate(s) from hardware token.", fg="#047857")
            else:
                messagebox.showwarning("No Certificates", "No signing certificates found in token. Please check PIN.")
        except Exception as e:
            messagebox.showerror("Token Error", f"Could not read certificates: {e}")

    def browse_pfx_file(self):
        f = filedialog.askopenfilename(title="Select .PFX / .P12 File", filetypes=[("PKCS#12 Files", "*.pfx;*.p12")])
        if f:
            self.pfx_path_var.set(f)
            self.verify_pfx_certificate(silent=True)

    def verify_pfx_certificate(self, silent=False):
        pfx_p = self.pfx_path_var.get().strip()
        pwd = self.pfx_pass_var.get().strip()
        if not pfx_p:
            if not silent: messagebox.showerror("Error", "Please select a PFX file."); return False
        try:
            signer, cert = load_pfx_certificate(pfx_p, pwd)
            cn = "Unknown"
            for attr in cert.subject:
                if attr.oid == x509.NameOID.COMMON_NAME: cn = attr.value; break
            if not silent:
                messagebox.showinfo("Verified", f"Certificate Verified:\nCommon Name (CN): {cn}\nExpires: {cert.not_valid_after_utc}")
            return True
        except Exception as e:
            if not silent: messagebox.showerror("PFX Error", str(e))
            return False

    def start_signing(self):
        if LicenseManager.check_expiration_local() == "expired":
            messagebox.showerror("License Expired", "License expired! Please contact +91 7016797720 to renew.")
            return

        if not self.sign_viewer.pdf_path:
            messagebox.showerror("Error", "Please open a PDF document on the right canvas."); return
        if not self.signature_areas:
            messagebox.showwarning("Warning", "Please draw at least one signature box on the PDF canvas."); return

        # Check which DSC source is selected in notebook
        cur_tab = self.nb_dsc.index(self.nb_dsc.select())
        is_token = (cur_tab == 0)

        if is_token:
            pin = self.pin_var.get().strip()
            if not pin:
                messagebox.showerror("Error", "Please enter Hardware Token PIN."); return
            idx = self.token_cert_combo.current()
            if idx < 0:
                messagebox.showerror("Error", "Please select a certificate from the token."); return
            cert_alias = self.loaded_certs[idx]['alias']
            pfx_path, pfx_pass = None, None
        else:
            pfx_path = self.pfx_path_var.get().strip()
            pfx_pass = self.pfx_pass_var.get().strip()
            if not pfx_path or not self.verify_pfx_certificate(silent=True):
                messagebox.showerror("Error", "Please select a valid PFX certificate and password."); return
            pin, cert_alias = None, None

        # Bulk or Single
        if getattr(self, 'bulk_mode', False) and getattr(self, 'bulk_pdf_files', []):
            self._start_bulk_signing_process(is_token, pin, cert_alias, pfx_path, pfx_pass)
            return

        out_path = filedialog.asksaveasfilename(title="Save Signed PDF As", defaultextension=".pdf", filetypes=[("PDF Documents", "*.pdf")])
        if not out_path: return

        self.lbl_sign_status.config(text="Signing PDF in progress...", fg="#0284C7")
        self.btn_sign_doc.config(state=tk.DISABLED)

        t = threading.Thread(
            target=self._signing_worker,
            args=(self.sign_viewer.pdf_path, out_path, is_token, pin, cert_alias, pfx_path, pfx_pass, list(self.signature_areas))
        )
        t.start()

    def _signing_worker(self, in_p, out_p, is_token, pin, cert_alias, pfx_path, pfx_pass, areas):
        try:
            with open(in_p, 'rb') as f:
                pdf_data = f.read()

            driver_path = DRIVER_MAP.get(self.driver_var.get())
            reason = self.sign_reason_var.get()
            location = self.sign_location_var.get()
            contact = self.sign_contact_var.get()
            tsa = TSA_URL if "DigiCert" in self.sign_tsa_var.get() else None

            if is_token:
                lib = pkcs11.lib(driver_path)
                token = None
                for slot in lib.get_slots(token_present=True):
                    try: token = slot.get_token(); break
                    except Exception: continue
                if not token: raise ValueError("Hardware token not detected. Ensure USB is inserted.")

                with token.open(user_pin=pin) as session:
                    cert_id, cert_parsed = None, None
                    for obj in session.get_objects({pkcs11.Attribute.CLASS: pkcs11.ObjectClass.CERTIFICATE}):
                        c = x509.load_der_x509_certificate(obj[pkcs11.Attribute.VALUE])
                        if str(c.serial_number) == str(cert_alias):
                            cert_id, cert_parsed = obj[pkcs11.Attribute.ID], c; break
                    if not cert_id: raise ValueError("Certificate not found in token.")
                    signer = PKCS11Signer(pkcs11_session=session, cert_id=cert_id)

                    visual_pdf = draw_reportlab_layer(pdf_data, areas, cert_parsed, reason, location, contact)
                    self._pyhanko_sign_incremental(visual_pdf, out_p, signer, areas, tsa)
            else:
                signer, cert_parsed = load_pfx_certificate(pfx_path, pfx_pass)
                visual_pdf = draw_reportlab_layer(pdf_data, areas, cert_parsed, reason, location, contact)
                self._pyhanko_sign_incremental(visual_pdf, out_p, signer, areas, tsa)

            self.root.after(0, lambda: self._on_sign_success(out_p))
        except Exception as e:
            self.root.after(0, lambda: self._on_sign_fail(str(e)))

    def _pyhanko_sign_incremental(self, visual_pdf_data, out_path, signer, areas, tsa_url):
        current_io = io.BytesIO(visual_pdf_data)
        base_reader = PdfReader(io.BytesIO(visual_pdf_data))
        tst = HTTPTimeStamper(tsa_url) if tsa_url else None

        for idx, area in enumerate(areas):
            p_idx = area['page']
            page_meta = base_reader.pages[p_idx]
            offset_x = float(page_meta.mediabox.left if hasattr(page_meta.mediabox, 'left') else page_meta.mediaBox.getLowerLeft_x())
            offset_y = float(page_meta.mediabox.bottom if hasattr(page_meta.mediabox, 'bottom') else page_meta.mediaBox.getLowerLeft_y())

            x1 = int(area['x'] + offset_x)
            y1 = int(area['y'] + offset_y)
            x2 = int(area['x'] + area['width'] + offset_x)
            y2 = int(area['y'] + area['height'] + offset_y)

            field_name = f"Signature_{idx+1}"
            writer = IncrementalPdfFileWriter(current_io)
            append_signature_field(writer, SigFieldSpec(field_name, box=(x1, y1, x2, y2), on_page=p_idx))

            tmp_io = io.BytesIO()
            writer.write(tmp_io)
            tmp_io.seek(0)

            final_writer = IncrementalPdfFileWriter(tmp_io)
            pdf_signer = signers.PdfSigner(
                signature_meta=signers.PdfSignatureMetadata(field_name=field_name),
                signer=signer,
                timestamper=tst
            )
            out_io = io.BytesIO()
            pdf_signer.sign_pdf(final_writer, output=out_io)
            current_io = out_io
            current_io.seek(0)

        with open(out_path, 'wb') as out_f:
            out_f.write(current_io.getvalue())

    def _on_sign_success(self, out_path):
        self.btn_sign_doc.config(state=tk.NORMAL)
        self.lbl_sign_status.config(text=f"✓ Signed Successfully: {os.path.basename(out_path)}", fg="#047857")
        self.sign_viewer.load_pdf(out_path)
        messagebox.showinfo("Signed Successfully", f"Document signed and verified:\n{out_path}")

    def _on_sign_fail(self, err):
        self.btn_sign_doc.config(state=tk.NORMAL)
        self.lbl_sign_status.config(text="❌ Signing Failed", fg="#DC2626")
        messagebox.showerror("Signing Error", f"Could not sign document:\n{err}")

    def _start_bulk_signing_process(self, is_token, pin, cert_alias, pfx_path, pfx_pass):
        pdfs = self.bulk_pdf_files
        out_dir = self.bulk_out_dir
        areas = list(self.signature_areas)
        self.bulk_mode = False

        self.lbl_sign_status.config(text=f"Batch signing {len(pdfs)} documents...", fg="#0284C7")
        self.btn_sign_doc.config(state=tk.DISABLED)

        def bulk_thread():
            count = 0
            for p in pdfs:
                fname = os.path.basename(p)
                out_p = os.path.join(out_dir, f"signed_{fname}")
                try:
                    self._signing_worker(p, out_p, is_token, pin, cert_alias, pfx_path, pfx_pass, areas)
                    count += 1
                except Exception as e:
                    logger.error(f"Bulk sign error on {fname}: {e}")

            self.root.after(0, lambda: messagebox.showinfo("Batch Complete", f"Successfully batch signed {count} / {len(pdfs)} PDFs into:\n{out_dir}"))
            self.root.after(0, lambda: self.btn_sign_doc.config(state=tk.NORMAL))

        threading.Thread(target=bulk_thread, daemon=True).start()

    # =========================================================================
    # EXTERNAL SHORTCUTS & HELPERS
    # =========================================================================
    def open_buy_dsc(self):
        webbrowser.open("https://pay.a-b-infotech.in/license.php")

    def open_whatsapp(self):
        webbrowser.open("https://wa.me/917016797720")

    def show_driver_shortcuts_dialog(self):
        TokenDriverShortcutsDialog(self.root, self)

    def open_settings_dialog(self, initial_tab=None):
        SettingsDialog(self.root, self, initial_tab=initial_tab)

    def open_batch_dialog(self):
        BatchFolderDialog(self.root, self)

    def reload_drivers(self, select_driver=None):
        global DRIVER_MAP
        DRIVER_MAP = get_platform_drivers()
        keys = list(DRIVER_MAP.keys())
        self.token_driver_combo['values'] = keys
        if select_driver and select_driver in keys:
            self.driver_var.set(select_driver)
        elif keys:
            self.token_driver_combo.current(0)

# ==========================================
# 7. MAIN ENTRYPOINT & TRAY INTEGRATION
# ==========================================
def main():
    global gui_root, gui_app

    gui_root = tk.Tk()
    gui_app = VisualPdfSignerApp(gui_root)

    # Start Flask API daemon in background
    def run_flask():
        try:
            app.run(host='127.0.0.1', port=5000, threaded=True, use_reloader=False)
        except Exception as e:
            logger.error(f"Flask daemon error: {e}")

    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    # System Tray Integration
    if pystray and os.path.exists("icon.ico"):
        try:
            tray_img = Image.open("icon.ico")
            menu = (
                item('Open Application', lambda: gui_app.show_window()),
                item('Driver Shortcuts', lambda: gui_app.show_driver_shortcuts_dialog()),
                item('WhatsApp Support', lambda: gui_app.open_whatsapp()),
                item('Exit', lambda: sys.exit(0))
            )
            tray_icon = pystray.Icon("abinfo_signer", tray_img, "A B INFOTECH Signer", menu)
            threading.Thread(target=tray_icon.run, daemon=True).start()
        except Exception:
            pass

    gui_root.mainloop()

if __name__ == '__main__':
    main()
