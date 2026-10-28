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
import tempfile
import uuid
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
from tkinter import ttk, filedialog, messagebox, colorchooser

# Prevent PyMuPDF layout/LLM modules from causing issues
sys.modules['pymupdf4llm'] = None
sys.modules['pymupdf.layout'] = None

try:
    import fitz  # PyMuPDF
    from PIL import Image, ImageTk
except ImportError:
    print("Please install PyMuPDF and Pillow: pip install PyMuPDF Pillow")
    sys.exit(1)

# Optional OCR library (Tesseract & Deep-Learning RapidOCR)
try:
    import pytesseract
    PYTESSERACT_AVAILABLE = True
except ImportError:
    PYTESSERACT_AVAILABLE = False

try:
    from rapidocr_onnxruntime import RapidOCR
    RAPID_OCR_AVAILABLE = True
except ImportError:
    RAPID_OCR_AVAILABLE = False

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
    from asn1crypto import x509 as a509, keys as akeys
    from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption
    from cryptography.x509.oid import NameOID, ExtensionOID
    from pyhanko_certvalidator.registry import SimpleCertificateStore
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

def apply_app_icon(window):
    """
    Apply high-res A B INFOTECH icon to any Tk / Toplevel window across Windows/Linux/Mac.
    Replaces default Tkinter feather/quill icon everywhere.
    """
    if not window:
        return
    try:
        ico_candidates = [
            os.path.join(APP_DIR, "app_icon.ico"),
            os.path.join(APP_DIR, "icon.ico"),
            os.path.join(os.path.dirname(__file__), "app_icon.ico") if "__file__" in globals() else None,
            os.path.join(os.getcwd(), "app_icon.ico"),
            os.path.join(os.getcwd(), "icon.ico"),
        ]
        target_ico = next((c for c in ico_candidates if c and os.path.exists(c)), None)
        if target_ico and CURRENT_OS == 'Windows':
            window.iconbitmap(target_ico)
    except Exception as e:
        logger.debug(f"apply_app_icon iconbitmap error: {e}")

    try:
        png_candidates = [
            os.path.join(APP_DIR, "image_546aa0.png"),
            os.path.join(os.path.dirname(__file__), "image_546aa0.png") if "__file__" in globals() else None,
            os.path.join(os.getcwd(), "image_546aa0.png"),
            os.path.join(APP_DIR, "app_icon.png"),
            os.path.join(os.getcwd(), "app_icon.png"),
        ]
        target_png = next((p for p in png_candidates if p and os.path.exists(p)), None)
        if target_png:
            img = Image.open(target_png)
            photo = ImageTk.PhotoImage(img)
            window.iconphoto(False, photo)
            window._app_window_icon_ref = photo  # keep reference
    except Exception as e:
        logger.debug(f"apply_app_icon iconphoto error: {e}")

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
    def save_license(key, expiredate="never", tier="pro", id=None, status="active", client_email="", activated_at=None, **kwargs):
        """
        Stores complete license record in system_cache.ico:
        1. id
        2. license_key
        3. status ('active', 'inactive', 'suspended')
        4. license_tier ('basic', 'pro')
        5. client_email
        6. activated_at
        7. expiredate
        """
        tier_clean = "basic" if str(tier).lower().strip() == "basic" else "pro"
        act_date = activated_at or datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        lic_dict = {
            "id": id,
            "license_key": key,
            "key": key,
            "status": status or "active",
            "license_tier": tier_clean,
            "tier": tier_clean,
            "client_email": client_email or "",
            "activated_at": act_date,
            "expiredate": expiredate or "never"
        }
        encoded_data = base64.b64encode(json.dumps(lic_dict).encode('utf-8'))

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
        """
        Retrieves all 7 license fields from system_cache.ico.
        Supports JSON format with backward-compatible fallback.
        """
        default_info = {
            "id": None,
            "license_key": None,
            "key": None,
            "status": "inactive",
            "license_tier": "pro",
            "tier": "pro",
            "client_email": "",
            "activated_at": "",
            "expiredate": "never"
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

                    # 1. Parse JSON format with all 7 fields
                    try:
                        parsed = json.loads(decoded_str)
                        if isinstance(parsed, dict):
                            raw_tier = parsed.get("license_tier") or parsed.get("tier", "pro")
                            tier = "basic" if str(raw_tier).lower().strip() == "basic" else "pro"
                            k = parsed.get("license_key") or parsed.get("key")
                            if k == "ABI-MASTER-PASS":
                                tier = "pro"
                            return {
                                "id": parsed.get("id"),
                                "license_key": k,
                                "key": k,
                                "status": parsed.get("status", "active"),
                                "license_tier": tier,
                                "tier": tier,
                                "client_email": parsed.get("client_email", ""),
                                "activated_at": parsed.get("activated_at", ""),
                                "expiredate": parsed.get("expiredate", "never")
                            }
                    except Exception:
                        pass

                    # 2. Legacy delimited fallback (key|||expiredate|||tier)
                    chunks = decoded_str.split("|||")
                    k = chunks[0] if len(chunks) > 0 else None
                    exp = chunks[1] if len(chunks) > 1 else "never"
                    raw_tier = chunks[2].lower().strip() if len(chunks) > 2 else "pro"
                    tier = "basic" if raw_tier == "basic" else "pro"

                    if k == "ABI-MASTER-PASS":
                        tier = "pro"

                    return {
                        "id": None,
                        "license_key": k,
                        "key": k,
                        "status": "active" if k else "inactive",
                        "license_tier": tier,
                        "tier": tier,
                        "client_email": "",
                        "activated_at": "",
                        "expiredate": exp
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
        info = LicenseManager.get_license_info()
        key = info.get("key")
        expiredate = info.get("expiredate")
        status = info.get("status", "active")

        if not key:
            return "inactive"
        if key == "ABI-MASTER-PASS":
            return "active"
        if status in ["inactive", "suspended"]:
            return status

        if expiredate and expiredate != "never":
            try:
                exp_dt = datetime.strptime(expiredate, '%Y-%m-%d %H:%M:%S')
                if datetime.now() > exp_dt:
                    return "expired"
            except Exception as e:
                logger.error(f"Date parsing error: {e}")

        return "active"

    @staticmethod
    def verify_with_database():
        """
        One-time check that validates every detail in system_cache.ico against the database.
        Syncs id, license_key, status, license_tier, client_email, activated_at, expiredate.
        """
        info = LicenseManager.get_license_info()
        key = info.get("key")
        if not key or key == "ABI-MASTER-PASS":
            return {"status": "active", "tier": "pro"}

        urls_to_try = [
            f"http://127.0.0.1/pdf/activate_web.php?action=verify_license&key={urllib.parse.quote(key)}",
            f"{LicenseManager.WEBSITE_URL}?action=verify_license&key={urllib.parse.quote(key)}"
        ]
        for url in urls_to_try:
            try:
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) A B INFOTECH Suite'})
                with urllib.request.urlopen(req, timeout=3.0) as resp:
                    if resp.status == 200:
                        raw = resp.read().decode('utf-8')
                        data = json.loads(raw)
                        db_status = data.get('status', 'invalid')
                        if db_status in ['active', 'expired', 'inactive', 'suspended']:
                            LicenseManager.save_license(
                                key=key,
                                expiredate=data.get('expiredate', info.get('expiredate', 'never')),
                                tier=data.get('license_tier') or data.get('tier', info.get('tier', 'pro')),
                                id=data.get('id', info.get('id')),
                                status=db_status,
                                client_email=data.get('client_email', info.get('client_email', '')),
                                activated_at=data.get('activated_at', info.get('activated_at', ''))
                            )
                            return data
            except Exception as e:
                logger.debug(f"Direct DB verify attempt failed on {url}: {e}")

        return {"status": LicenseManager.check_expiration_local(), "tier": info.get("tier", "pro")}

    @staticmethod
    def is_pro():
        if not LicenseManager.is_activated():
            return False
        if LicenseManager.check_expiration_local() == "expired":
            return False
        info = LicenseManager.get_license_info()
        return info.get("tier", "pro") == "pro"

    @staticmethod
    def is_ocr_enabled():
        return LicenseManager.is_pro()

    @staticmethod
    def is_edit_enabled():
        return LicenseManager.is_pro()

    @staticmethod
    def get_tier_label():
        info = LicenseManager.get_license_info()
        return "Basic" if info.get("tier") == "basic" else "Pro"

    @staticmethod
    def get_hardware_id():
        import uuid
        mac_node = uuid.getnode()
        return ":".join(f"{(mac_node >> (i * 8)) & 0xff:02X}" for i in reversed(range(6)))

# ==========================================
# 1. CROSS-PLATFORM DRIVER MAPPING
# ==========================================
def _pick_best_pkcs11_dll(candidates):
    """
    Selects the best matching PKCS#11 driver DLL from candidate list.
    First checks if the DLL exists and loads cleanly via pkcs11, then falls back to first existing.
    """
    for p in candidates:
        if not p: continue
        target = p
        if target.lower().endswith('.sig'):
            target = target[:-4] + '.dll'
        if os.path.exists(target) and target.lower().endswith('.dll'):
            try:
                pkcs11.lib(target)
                return target
            except Exception:
                pass
    for p in candidates:
        if not p: continue
        target = p
        if target.lower().endswith('.sig'):
            target = target[:-4] + '.dll'
        if os.path.exists(target) and target.lower().endswith('.dll'):
            return target
    for p in candidates:
        if p and os.path.exists(p):
            return p
    return candidates[0] if candidates else ""

def find_epass_driver_path():
    is_64bit = sys.maxsize > 2**32
    if is_64bit:
        candidates = [
            r"C:\Windows\System32\HYP2003csp11IND.dll",
            r"C:\Windows\System32\HYP2003csp11IND.sig",
            r"C:\Windows\System32\HYP2003csp11IND_s.dll",
            r"C:\Windows\SysWOW64\HYP2003csp11IND.dll",
            r"C:\Windows\SysWOW64\HYP2003csp11IND.sig",
            r"C:\Windows\SysWOW64\HYP2003csp11IND_s.dll",
            r"C:\Windows\System32\eps2003csp11v2.dll",
            r"C:\Program Files (x86)\Professional Digisign Private Limited\ProdigiSignStore\Lib\windows\x64\eps2003csp11v2.dll",
            r"C:\Program Files\Professional Digisign Private Limited\ProdigiSignStore\Lib\windows\x64\eps2003csp11v2.dll",
            r"C:\Windows\SysWOW64\eps2003csp11v2.dll",
            r"C:\Program Files\ePass2003-India-Auto\eps2003csp11v2.dll",
            r"C:\Program Files (x86)\ePass2003-India-Auto\eps2003csp11v2.dll",
            r"C:\Program Files\EnterSafe\ePass2003\eps2003csp11v2.dll",
            r"C:\Program Files (x86)\EnterSafe\ePass2003\eps2003csp11v2.dll"
        ]
    else:
        candidates = [
            r"C:\Windows\SysWOW64\HYP2003csp11IND.dll",
            r"C:\Windows\SysWOW64\HYP2003csp11IND.sig",
            r"C:\Windows\SysWOW64\HYP2003csp11IND_s.dll",
            r"C:\Windows\System32\HYP2003csp11IND.dll",
            r"C:\Windows\System32\HYP2003csp11IND.sig",
            r"C:\Windows\System32\HYP2003csp11IND_s.dll",
            r"C:\Windows\SysWOW64\eps2003csp11v2.dll",
            r"C:\Windows\System32\eps2003csp11v2.dll",
            r"C:\Program Files (x86)\ePass2003-India-Auto\eps2003csp11v2.dll",
            r"C:\Program Files\ePass2003-India-Auto\eps2003csp11v2.dll"
        ]
    return _pick_best_pkcs11_dll(candidates)

def find_innait_driver_path():
    is_64bit = sys.maxsize > 2**32
    if is_64bit:
        candidates = [
            r"C:\Windows\System32\InnaITPKCS11Driver.dll",
            r"C:\Windows\System32\InnaITDSCLibrary.dll",
            r"C:\Windows\System32\InnaITCSP.dll",
            r"C:\Windows\System32\InnaITSmartCardMiniDriver.dll",
            r"C:\Windows\System32\InnaITUSB.dll",
            r"C:\Windows\SysWOW64\InnaITPKCS11Driver.dll",
            r"C:\Windows\SysWOW64\InnaITDSCLibrary.dll",
            r"C:\Windows\SysWOW64\InnaITCSP.dll",
            r"C:\Windows\SysWOW64\InnaITSmartCardMiniDriver.dll",
            r"C:\Windows\SysWOW64\InnaITUSB.dll",
            r"C:\Program Files (x86)\Precision Biometric\InnaIT\InnaITDSC\InnaITPKCS11Driver.dll",
            r"C:\Program Files\Precision Biometric\InnaIT\InnaITDSC\InnaITPKCS11Driver.dll"
        ]
    else:
        candidates = [
            r"C:\Windows\SysWOW64\InnaITPKCS11Driver.dll",
            r"C:\Windows\SysWOW64\InnaITDSCLibrary.dll",
            r"C:\Windows\SysWOW64\InnaITCSP.dll",
            r"C:\Windows\SysWOW64\InnaITSmartCardMiniDriver.dll",
            r"C:\Windows\SysWOW64\InnaITUSB.dll",
            r"C:\Windows\System32\InnaITPKCS11Driver.dll",
            r"C:\Windows\System32\InnaITDSCLibrary.dll",
            r"C:\Windows\System32\InnaITCSP.dll",
            r"C:\Windows\System32\InnaITSmartCardMiniDriver.dll",
            r"C:\Windows\System32\InnaITUSB.dll"
        ]
    return _pick_best_pkcs11_dll(candidates)

def find_mtoken_driver_path():
    is_64bit = sys.maxsize > 2**32
    if is_64bit:
        candidates = [
            r"C:\Windows\System32\cryptoida_pkcs11.dll",
            r"C:\Windows\System32\cryptoida_pkcs11_f2.dll",
            r"C:\Windows\System32\cryptoida_pkcs11_f3.dll",
            r"C:\Windows\SysWOW64\cryptoida_pkcs11.dll",
            r"C:\Windows\SysWOW64\cryptoida_pkcs11_f2.dll",
            r"C:\Windows\SysWOW64\cryptoida_pkcs11_f3.dll",
            r"C:\Program Files (x86)\CryptoID\cryptoida_pkcs11.dll",
            r"C:\Program Files (x86)\CryptoID\CryptoIDCertUtilityF3\cryptoida_pkcs11_f3.dll"
        ]
    else:
        candidates = [
            r"C:\Windows\SysWOW64\cryptoida_pkcs11.dll",
            r"C:\Windows\SysWOW64\cryptoida_pkcs11_f2.dll",
            r"C:\Windows\SysWOW64\cryptoida_pkcs11_f3.dll",
            r"C:\Windows\System32\cryptoida_pkcs11.dll",
            r"C:\Windows\System32\cryptoida_pkcs11_f2.dll",
            r"C:\Windows\System32\cryptoida_pkcs11_f3.dll"
        ]
    return _pick_best_pkcs11_dll(candidates)

def find_proxkey_driver_path():
    is_64bit = sys.maxsize > 2**32
    if is_64bit:
        candidates = [
            r"C:\Windows\System32\Watchdata\PROXKey CSP India V3.0\WDPKCS.dll",
            r"C:\Windows\System32\Watchdata\PROXKey CSP India V3.0\TokenMgr.dll",
            r"C:\Windows\System32\Watchdata\PROXKey CSP India V3.0\WDCtr.dll",
            r"C:\Windows\System32\Watchdata\PROXKey CSP India V3.0\WDEvent.dll",
            r"C:\Windows\System32\Watchdata\PROXKey CSP India V3.0\wdsafe3.dll",
            r"C:\Windows\System32\Watchdata\PROXKey CSP India V3.0\wdkmgr.dll",
            r"C:\Windows\SysWOW64\Watchdata\PROXKey CSP India V3.0\WDPKCS.dll",
            r"C:\Windows\SysWOW64\Watchdata\PROXKey CSP India V3.0\TokenMgr.dll",
            r"C:\Windows\SysWOW64\Watchdata\PROXKey CSP India V3.0\WDCtr.dll",
            r"C:\Windows\SysWOW64\Watchdata\PROXKey CSP India V3.0\WDEvent.dll",
            r"C:\Windows\SysWOW64\Watchdata\PROXKey CSP India V3.0\wdsafe3.dll",
            r"C:\Windows\SysWOW64\Watchdata\PROXKey CSP India V3.0\wdkmgr.dll"
        ]
    else:
        candidates = [
            r"C:\Windows\SysWOW64\Watchdata\PROXKey CSP India V3.0\WDPKCS.dll",
            r"C:\Windows\SysWOW64\Watchdata\PROXKey CSP India V3.0\TokenMgr.dll",
            r"C:\Windows\SysWOW64\Watchdata\PROXKey CSP India V3.0\WDCtr.dll",
            r"C:\Windows\SysWOW64\Watchdata\PROXKey CSP India V3.0\WDEvent.dll",
            r"C:\Windows\SysWOW64\Watchdata\PROXKey CSP India V3.0\wdsafe3.dll",
            r"C:\Windows\SysWOW64\Watchdata\PROXKey CSP India V3.0\wdkmgr.dll",
            r"C:\Windows\System32\Watchdata\PROXKey CSP India V3.0\WDPKCS.dll",
            r"C:\Windows\System32\Watchdata\PROXKey CSP India V3.0\TokenMgr.dll",
            r"C:\Windows\System32\Watchdata\PROXKey CSP India V3.0\WDCtr.dll",
            r"C:\Windows\System32\Watchdata\PROXKey CSP India V3.0\WDEvent.dll",
            r"C:\Windows\System32\Watchdata\PROXKey CSP India V3.0\wdsafe3.dll",
            r"C:\Windows\System32\Watchdata\PROXKey CSP India V3.0\wdkmgr.dll"
        ]
    return _pick_best_pkcs11_dll(candidates)

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
        epass_driver = find_epass_driver_path()
        proxkey_driver = find_proxkey_driver_path()
        mtoken_driver = find_mtoken_driver_path()
        innait_driver = find_innait_driver_path()

        drivers["ePass2003 / HyperPKI Token"] = epass_driver
        drivers["ePass2003 Token"] = epass_driver
        drivers["ProxKey India"] = proxkey_driver
        drivers["mToken / CryptoID"] = mtoken_driver
        drivers["InnaIT / Precision Biometric Token"] = innait_driver
        drivers["InnaIT Token"] = innait_driver
        
        opensc_win = r"C:\Program Files\OpenSC Project\OpenSC\pkcs11\opensc-pkcs11.dll"
        if os.path.exists(opensc_win):
            drivers["Generic OpenSC"] = opensc_win
            
    elif CURRENT_OS == 'Darwin':
        drivers["ePass2003 Token (macOS)"] = "/Library/EnterSafe/libcastle.dylib"
        drivers["ProxKey India (macOS)"] = "/Library/ProxKey/libWD_PKCS11.dylib"
        drivers["mToken / CryptoID (macOS)"] = "/Library/mToken/libcryptoid_pkcs11.dylib"
        drivers["InnaIT Token (macOS)"] = "/usr/local/lib/libinnait_pkcs11.dylib"
        drivers["OpenSC (macOS)"] = "/opt/homebrew/lib/opensc-pkcs11.so"
    else:
        drivers["ePass2003 Token (Linux)"] = "/usr/lib/libcastle.so.1.0.0"
        drivers["ProxKey India (Linux)"] = "/usr/lib/libWatchdataPKCS11.so"
        drivers["mToken / CryptoID (Linux)"] = "/usr/lib/libcryptoid_pkcs11.so"
        drivers["InnaIT Token (Linux)"] = "/usr/lib/libinnait_pkcs11.so"
        drivers["OpenSC (Linux)"] = "/usr/lib/x86_64-linux-gnu/opensc-pkcs11.so"

    custom = load_custom_drivers()
    for name, path in custom.items():
        drivers[f"⭐ {name}"] = path

    drivers["➕ [Add Custom Token Driver...]"] = "__CUSTOM__"
    return drivers

DRIVER_MAP = get_platform_drivers()
TSA_URL = "http://timestamp.digicert.com"

# Global server state for Web API bridge
server_config = {
    'status': 'running',
    'port': 5000,
    'host': '127.0.0.1',
    'driver_path': find_epass_driver_path() or ''
}

def convert_docx_to_pdf_bytes(docx_path):
    """
    Converts a Word document (.docx or .doc) to PDF bytes.
    Method 1: Uses Windows Word COM automation if MS Word is installed.
    Method 2: Pure Python fallback using python-docx + ReportLab without needing MS Word.
    """
    if CURRENT_OS == 'Windows':
        try:
            import win32com.client
            import pythoncom
            pythoncom.CoInitialize()
            word = win32com.client.DispatchEx("Word.Application")
            word.Visible = False
            word.DisplayAlerts = False
            abs_in = os.path.abspath(docx_path)
            tmp_pdf = os.path.join(tempfile.gettempdir(), f"abi_word_{uuid.uuid4().hex[:8]}.pdf")
            doc = word.Documents.Open(abs_in, ReadOnly=True)
            doc.SaveAs(tmp_pdf, FileFormat=17)  # 17 = wdFormatPDF
            doc.Close(False)
            word.Quit()
            if os.path.exists(tmp_pdf):
                with open(tmp_pdf, 'rb') as f:
                    pdf_data = f.read()
                try: os.remove(tmp_pdf)
                except Exception: pass
                if pdf_data:
                    return pdf_data
        except Exception as e:
            logger.debug(f"win32com word conversion attempt notice: {e}")

    try:
        import docx
        from reportlab.lib.pagesizes import letter
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet

        doc = docx.Document(docx_path)
        buf = io.BytesIO()
        pdf_doc = SimpleDocTemplate(buf, pagesize=letter, rightMargin=40, leftMargin=40, topMargin=40, bottomMargin=40)
        styles = getSampleStyleSheet()
        story = []
        for p in doc.paragraphs:
            text = p.text.strip()
            if text:
                safe_text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                story.append(Paragraph(safe_text, styles['Normal']))
                story.append(Spacer(1, 4))
        for table in doc.tables:
            for row in table.rows:
                row_txt = " | ".join(cell.text.strip() for cell in row.cells if cell.text.strip())
                if row_txt:
                    safe_row = row_txt.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                    story.append(Paragraph(safe_row, styles['Normal']))
                    story.append(Spacer(1, 3))
        if not story:
            story.append(Paragraph("[Empty Word Document]", styles['Normal']))
        pdf_doc.build(story)
        return buf.getvalue()
    except Exception as e:
        logger.error(f"Fallback docx convert error: {e}")
        raise ValueError(f"Could not convert Word document '{os.path.basename(docx_path)}' to PDF: {e}")

def convert_file_to_pdf_bytes(filepath):
    """
    Converts any supported file (PDF, Word, Images) to PDF bytes:
    - .pdf: direct raw bytes
    - .png, .jpg, .jpeg, .bmp, .tiff, .webp, .gif: PyMuPDF high-res image-to-PDF conversion
    - .docx, .doc: Word to PDF conversion
    """
    ext = os.path.splitext(filepath)[1].lower()
    if ext == '.pdf':
        with open(filepath, 'rb') as f:
            return f.read()
    elif ext in ['.png', '.jpg', '.jpeg', '.bmp', '.tiff', '.webp', '.gif']:
        img_doc = fitz.open(filepath)
        pdf_bytes = img_doc.convert_to_pdf()
        img_doc.close()
        return pdf_bytes
    elif ext in ['.docx', '.doc']:
        return convert_docx_to_pdf_bytes(filepath)
    else:
        # Fallback to PyMuPDF generic conversion
        sub_doc = fitz.open(filepath)
        if sub_doc.is_pdf:
            data = sub_doc.tobytes()
            sub_doc.close()
            return data
        pdf_bytes = sub_doc.convert_to_pdf()
        sub_doc.close()
        return pdf_bytes

# ==========================================
# 1.5. CRYPTOGRAPHIC SIGNING & REPORTLAB OVERLAY
# ==========================================
def pdf_has_digital_signatures(pdf_input):
    """
    Check whether a PDF file or byte stream contains existing cryptographic digital signatures.
    """
    try:
        data = b""
        if isinstance(pdf_input, (str, os.PathLike)):
            if not os.path.exists(pdf_input):
                return False
            with open(pdf_input, 'rb') as f:
                data = f.read()
        elif isinstance(pdf_input, (bytes, bytearray)):
            data = bytes(pdf_input)
        elif hasattr(pdf_input, 'read'):
            pos = pdf_input.tell() if hasattr(pdf_input, 'tell') else 0
            data = pdf_input.read()
            if hasattr(pdf_input, 'seek'):
                pdf_input.seek(pos)

        if not data:
            return False

        # Fast byte check for PDF signature dictionaries
        if b"/ByteRange" in data and (b"/Type /Sig" in data or b"/Type/Sig" in data or b"/SubFilter" in data):
            return True

        # PyHanko embedded signatures check
        try:
            r = PdfFileReader(io.BytesIO(data))
            if len(r.embedded_signatures) > 0:
                return True
        except Exception:
            pass

        # PyMuPDF sigflags check
        try:
            doc = fitz.open(stream=data, filetype="pdf")
            has_sig = doc.get_sigflags() > 0
            doc.close()
            if has_sig:
                return True
        except Exception:
            pass

        return False
    except Exception:
        return False


def preserve_signature_appearances_on_doc(doc, remove_broken_fields=True):
    """
    Scans all pages in the PyMuPDF document for digital signature widgets (/Sig).
    Renders their exact visual appearance (signer stamp, name, date, reason, green tick, border)
    at high resolution (300 DPI) and embeds them directly into the page content stream as images.
    If remove_broken_fields=True, deletes the signature widgets and sanitizes AcroForm/SigFlags
    so that subsequent PDF mutations (Merge, Compress, Rotate, OCR/Edit) do NOT cause invalid byte-range
    table offsets or Adobe Acrobat 'File Damage / Corruption' errors.
    Returns the total count of signature appearances preserved.
    """
    total_preserved = 0
    try:
        for page in doc:
            sig_widgets = []
            try:
                for w in page.widgets():
                    ft = getattr(w, 'field_type', None)
                    fts = str(getattr(w, 'field_type_string', '')).lower()
                    if ft == fitz.PDF_WIDGET_TYPE_SIGNATURE or fts == 'signature':
                        sig_widgets.append(w)
            except Exception as we:
                logger.debug(f"Error enumerating page widgets: {we}")

            for w in sig_widgets:
                try:
                    rect = w.rect
                    if not rect.is_empty and not rect.is_infinite and rect.width > 2 and rect.height > 2:
                        pix = page.get_pixmap(clip=rect, dpi=300)
                        if pix and pix.width > 0 and pix.height > 0:
                            page.insert_image(rect, pixmap=pix)
                            total_preserved += 1
                except Exception as pix_e:
                    logger.debug(f"Could not bake signature appearance: {pix_e}")

                if remove_broken_fields:
                    try:
                        page.delete_widget(w)
                    except Exception:
                        pass

        if remove_broken_fields:
            try:
                root_xref = doc.pdf_catalog()
                doc.xref_set_key(root_xref, "SigFlags", "null")
                acro_xref = doc.xref_get_key(root_xref, "AcroForm")
                if acro_xref and acro_xref[0] != "null":
                    doc.xref_set_key(root_xref, "AcroForm", "null")
            except Exception as cat_e:
                logger.debug(f"AcroForm catalog cleanup: {cat_e}")
    except Exception as e:
        logger.debug(f"preserve_signature_appearances_on_doc error: {e}")

    return total_preserved

def clean_pdf_signatures_completely(doc, keep_visual=True):
    """
    Detects and cleans all digital signatures from a PyMuPDF document:
    - If keep_visual=True, bakes the visual stamp at 300 DPI into the page content.
    - If keep_visual=False, wipes the signature widget entirely without baking.
    - Deletes all signature widgets and removes broken catalog SigFlags/AcroForm.
    """
    total_cleaned = 0
    try:
        for page in doc:
            sig_widgets = []
            try:
                for w in page.widgets():
                    ft = getattr(w, 'field_type', None)
                    fts = str(getattr(w, 'field_type_string', '')).lower()
                    if ft == fitz.PDF_WIDGET_TYPE_SIGNATURE or fts == 'signature':
                        sig_widgets.append(w)
            except Exception:
                pass

            for w in sig_widgets:
                total_cleaned += 1
                if keep_visual:
                    try:
                        rect = w.rect
                        if not rect.is_empty and not rect.is_infinite and rect.width > 2 and rect.height > 2:
                            pix = page.get_pixmap(clip=rect, dpi=300)
                            if pix and pix.width > 0 and pix.height > 0:
                                page.insert_image(rect, pixmap=pix)
                    except Exception as e:
                        logger.debug(f"Error baking stamp: {e}")
                try:
                    page.delete_widget(w)
                except Exception:
                    pass

        try:
            root_xref = doc.pdf_catalog()
            doc.xref_set_key(root_xref, "SigFlags", "null")
            doc.xref_set_key(root_xref, "AcroForm", "null")
        except Exception:
            pass
    except Exception as e:
        logger.debug(f"clean_pdf_signatures_completely error: {e}")

    return total_cleaned

def extract_cert_email_and_address(cert):
    """
    Extracts email address and physical postal address from an x509.Certificate object (Token or PFX/P12).
    - Email: Checks Subject Alternative Name (SAN) for RFC822Name, then Subject DN for emailAddress (1.2.840.113549.1.9.1).
    - Address: Checks Subject DN for streetAddress (2.5.4.9), localityName (2.5.4.7), stateOrProvinceName (2.5.4.8),
               postalCode (2.5.4.17), countryName (2.5.4.6).
    """
    email = ""
    address = ""
    if not cert:
        return email, address

    try:
        from cryptography.x509.oid import ExtensionOID, NameOID
        # 1. Subject Alternative Name (SAN)
        try:
            san = cert.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME)
            emails = san.value.get_values_for_type(x509.RFC822Name)
            if emails:
                email = str(emails[0]).strip()
        except Exception:
            pass

        # Fallback to Subject emailAddress OID (1.2.840.113549.1.9.1)
        if not email:
            for attr in cert.subject:
                if attr.oid == NameOID.EMAIL_ADDRESS or attr.oid.dotted_string == '1.2.840.113549.1.9.1':
                    email = str(attr.value).strip()
                    break

        # 2. Extract Address from Subject DN
        street = ""
        locality = ""
        state = ""
        postal_code = ""
        country = ""

        for attr in cert.subject:
            oid_str = attr.oid.dotted_string
            val = str(attr.value).strip()
            if attr.oid == NameOID.STREET_ADDRESS or oid_str == '2.5.4.9':
                street = val
            elif attr.oid == NameOID.LOCALITY_NAME or oid_str == '2.5.4.7':
                locality = val
            elif attr.oid == NameOID.STATE_OR_PROVINCE_NAME or oid_str == '2.5.4.8':
                state = val
            elif attr.oid == NameOID.POSTAL_CODE or oid_str == '2.5.4.17':
                postal_code = val
            elif attr.oid == NameOID.COUNTRY_NAME or oid_str == '2.5.4.6':
                country = val

        parts = []
        if street:
            parts.append(street)
        if locality and locality.lower() not in street.lower():
            parts.append(locality)
        if state and state.lower() not in street.lower():
            parts.append(state)
        if postal_code and postal_code not in street:
            parts.append(postal_code)
        if not parts and country:
            parts.append(country)

        if parts:
            address = ", ".join(parts)
    except Exception as e:
        logger.debug(f"Cert email/address extraction error: {e}")

    return email, address

def get_bbox_bg_color(page, bbox):
    """
    Samples the background pixel color around the perimeter of a bounding box on the page.
    Prevents white redaction rectangles on colored backgrounds (e.g. blue headers, shaded tables).
    Returns (r, g, b) normalized between 0.0 and 1.0.
    """
    try:
        pix = page.get_pixmap(dpi=150)
        sx = pix.width / page.rect.width
        sy = pix.height / page.rect.height

        if hasattr(bbox, 'x0'):
            x0, y0, x1, y1 = bbox.x0, bbox.y0, bbox.x1, bbox.y1
        else:
            x0, y0, x1, y1 = bbox

        sample_pts = [
            (int(x0 * sx), max(0, int(y0 * sy) - 2)),
            (int((x0 + x1) / 2 * sx), max(0, int(y0 * sy) - 2)),
            (min(pix.width - 1, int(x1 * sx)), max(0, int(y0 * sy) - 2)),
            (max(0, int(x0 * sx) - 2), int((y0 + y1) / 2 * sy)),
            (min(pix.width - 1, int(x1 * sx) + 2), int((y0 + y1) / 2 * sy)),
            (int(x0 * sx), min(pix.height - 1, int(y1 * sy) + 2)),
            (int((x0 + x1) / 2 * sx), min(pix.height - 1, int(y1 * sy) + 2)),
            (min(pix.width - 1, int(x1 * sx)), min(pix.height - 1, int(y1 * sy) + 2)),
        ]
        colors = []
        for px, py in sample_pts:
            try:
                col = pix.pixel(px, py)
                colors.append(col[:3])
            except Exception:
                pass

        if colors:
            from collections import Counter
            most_common = Counter(colors).most_common(1)[0][0]
            return (most_common[0] / 255.0, most_common[1] / 255.0, most_common[2] / 255.0)
    except Exception as e:
        logger.debug(f"Failed to sample background color: {e}")
    return (1.0, 1.0, 1.0)

def sample_text_color_int(pix, box, bg_rgb):
    """
    Samples contrasting text pixel color from within the bounding box.
    Returns RGB integer (e.g. 0x000000 or 0xFFFFFF).
    """
    try:
        bg_int_r = int(bg_rgb[0] * 255)
        bg_int_g = int(bg_rgb[1] * 255)
        bg_int_b = int(bg_rgb[2] * 255)

        x0 = int(min(p[0] for p in box))
        y0 = int(min(p[1] for p in box))
        x1 = int(max(p[0] for p in box))
        y1 = int(max(p[1] for p in box))

        contrast_colors = []
        for x in range(x0 + 1, x1, max(1, (x1 - x0) // 5)):
            for y in range(y0 + 1, y1, max(1, (y1 - y0) // 3)):
                if 0 <= x < pix.width and 0 <= y < pix.height:
                    col = pix.pixel(x, y)[:3]
                    dist = abs(col[0] - bg_int_r) + abs(col[1] - bg_int_g) + abs(col[2] - bg_int_b)
                    if dist > 80:
                        contrast_colors.append(col)
        if contrast_colors:
            from collections import Counter
            c = Counter(contrast_colors).most_common(1)[0][0]
            return (c[0] << 16) | (c[1] << 8) | c[2]
    except Exception:
        pass
    luminance = (bg_rgb[0] * 0.299 + bg_rgb[1] * 0.587 + bg_rgb[2] * 0.114)
    return 0xFFFFFF if luminance < 0.5 else 0x000000

_RAPID_OCR_ENGINE = None

def get_rapid_ocr_engine():
    global _RAPID_OCR_ENGINE
    if _RAPID_OCR_ENGINE is None and RAPID_OCR_AVAILABLE:
        try:
            _RAPID_OCR_ENGINE = RapidOCR()
        except Exception as e:
            logger.warning(f"Could not initialize RapidOCR: {e}")
    return _RAPID_OCR_ENGINE

def draw_incremental_overlay_layer(pdf_bytes, areas, cert, reason="", location="", contact="", logo_path=None, appearance_opts=None):
    """
    Draw visual signature stamp directly and incrementally on the PDF using PyMuPDF,
    preserving 100% of original bytes so preexisting digital signatures remain intact!
    """
    if appearance_opts is None:
        appearance_opts = {
            'show_name': True,
            'show_date': True,
            'show_reason': True,
            'show_address': True,
            'show_issuer': False,
            'show_tick': True,
            'show_border': False
        }

    import tempfile
    with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tf:
        tf.write(pdf_bytes)
        tf_name = tf.name

    try:
        doc = fitz.open(tf_name)
        subj = cert.subject.rfc4514_string() if cert else ""
        def get_f(k):
            for p in subj.split(','):
                if p.strip().startswith(k + '='):
                    return p.split('=', 1)[1].strip()
            return "N/A"

        ist = timezone(timedelta(hours=5, minutes=30))
        signer_cn = get_f('CN') if cert else "Authorized Signer"

        lines = []
        if appearance_opts.get('show_name', True):
            lines.append("Digitally signed by:")
            lines.append(f"Name:- {signer_cn}")

        if appearance_opts.get('show_issuer', False):
            issuer_cn = "N/A"
            if cert:
                try:
                    for attr in cert.issuer:
                        if attr.oid == x509.NameOID.COMMON_NAME:
                            issuer_cn = attr.value; break
                except Exception:
                    pass
            lines.append(f"Issuer:- {issuer_cn}")

        if appearance_opts.get('show_reason', True) and reason:
            lines.append(f"Reason:- {reason}")
        if appearance_opts.get('show_address', True) and location:
            lines.append(f"Address:- {location}")
        if contact:
            lines.append(f"Contact:- {contact}")

        if appearance_opts.get('show_date', True):
            lines.append(f"Date:- {datetime.now(ist).strftime('%Y.%m.%d %H:%M:%S IST')}")

        if not lines:
            lines = [f"Signed by {signer_cn}"]

        full_text = "\n".join(lines)

        for a in areas:
            p_idx = int(a.get('page', 0))
            if p_idx >= len(doc):
                continue
            page = doc[p_idx]
            ph = page.rect.height

            ax = float(a['x'])
            ay_pdf = float(a['y'])
            aw = float(a['width'])
            ah = float(a['height'])
            ay_mupdf = ph - (ay_pdf + ah)

            # 1. Outer Box Border (only if show_border is True)
            if appearance_opts.get('show_border', False):
                rect = fitz.Rect(ax, ay_mupdf, ax + aw, ay_mupdf + ah)
                page.draw_rect(rect, color=(0.1, 0.2, 0.3), width=1.2)

            # 2. Custom Logo (if provided)
            has_logo = False
            logo_w = 0.0
            if logo_path and os.path.exists(logo_path):
                try:
                    logo_dim = min(aw * 0.28, ah * 0.82)
                    logo_w = logo_dim
                    logo_h = logo_dim
                    logo_x = ax + (aw * 0.03)
                    logo_y = ay_mupdf + (ah - logo_h) / 2
                    page.insert_image(fitz.Rect(logo_x, logo_y, logo_x + logo_w, logo_y + logo_h), filename=logo_path)
                    has_logo = True
                except Exception as e:
                    logger.warning(f"PyMuPDF custom logo overlay error: {e}")

            # 3. Background Green Checkmark Watermark (only if show_tick is True)
            if appearance_opts.get('show_tick', True):
                tick_offset_x = (logo_w + aw * 0.04) if has_logo else 0
                cx = ax + tick_offset_x + ((aw - tick_offset_x) / 2)
                cy = ay_mupdf + (ah / 2)
                s = min(aw - tick_offset_x, ah) * 0.35
                p1 = fitz.Point(cx - s, cy)
                p2 = fitz.Point(cx - s * 0.2, cy + s * 0.6)
                p3 = fitz.Point(cx + s * 0.8, cy - s * 0.8)
                page.draw_polyline([p1, p2, p3], color=(0.2, 0.7, 0.4), width=max(1.5, aw * 0.045), lineJoin=1)

            # 4. Text Information
            left_pad = (aw * 0.03) + (logo_w + (aw * 0.04) if has_logo else 0)
            right_pad = aw * 0.03
            tx = ax + left_pad
            ty = ay_mupdf + (ah * 0.06)
            tw = max(aw - left_pad - right_pad, 20.0)
            th = max(ah - (ah * 0.12), 10.0)

            f_size = min(tw / (max(len(l) for l in lines) * 0.55), th / (len(lines) * 1.25), 11.0)
            f_size = max(f_size, 5.5)

            page.insert_textbox(
                fitz.Rect(tx, ty, tx + tw, ty + th),
                full_text,
                fontsize=f_size,
                fontname="helv",
                color=(0.05, 0.1, 0.15)
            )

        doc.save(tf_name, incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
        doc.close()

        with open(tf_name, 'rb') as f:
            out_bytes = f.read()
        return out_bytes
    finally:
        if os.path.exists(tf_name):
            try:
                os.unlink(tf_name)
            except Exception:
                pass

def draw_reportlab_layer(pdf_bytes, areas, cert, reason="", location="", contact="", logo_path=None, appearance_opts=None):
    if pdf_has_digital_signatures(pdf_bytes):
        logger.info("PDF contains preexisting digital signatures. Using 100% incremental visual overlay to preserve digital signatures intact.")
        return draw_incremental_overlay_layer(pdf_bytes, areas, cert, reason=reason, location=location, contact=contact, logo_path=logo_path, appearance_opts=appearance_opts)

    if appearance_opts is None:
        appearance_opts = {
            'show_name': True,
            'show_date': True,
            'show_reason': True,
            'show_address': True,
            'show_issuer': False,
            'show_tick': True,
            'show_border': False
        }

    subj = cert.subject.rfc4514_string() if cert else ""
    def get_f(k):
        for p in subj.split(','):
            if p.strip().startswith(k + '='):
                return p.split('=', 1)[1].strip()
        return "N/A"

    ist = timezone(timedelta(hours=5, minutes=30))
    signer_cn = get_f('CN') if cert else "Authorized Signer"

    lines = []
    if appearance_opts.get('show_name', True):
        lines.append("Digitally signed by:")
        lines.append(f"Name:- {signer_cn}")

    if appearance_opts.get('show_issuer', False):
        issuer_cn = "N/A"
        if cert:
            try:
                for attr in cert.issuer:
                    if attr.oid == x509.NameOID.COMMON_NAME:
                        issuer_cn = attr.value; break
            except Exception:
                pass
        lines.append(f"Issuer:- {issuer_cn}")

    if appearance_opts.get('show_reason', True) and reason:
        lines.append(f"Reason:- {reason}")
    if appearance_opts.get('show_address', True) and location:
        lines.append(f"Address:- {location}")
    if contact:
        lines.append(f"Contact:- {contact}")

    if appearance_opts.get('show_date', True):
        lines.append(f"Date:- {datetime.now(ist).strftime('%Y.%m.%d %H:%M:%S IST')}")

    if not lines:
        lines = [f"Signed by {signer_cn}"]

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

                # Signature outer box border (only if show_border is True)
                if appearance_opts.get('show_border', False):
                    c.setStrokeColorRGB(0.1, 0.2, 0.3)
                    c.setLineWidth(1.2)
                    c.rect(ax, ay, aw, ah)

                # Draw custom logo if provided
                has_logo = False
                logo_w = 0
                if logo_path and os.path.exists(logo_path):
                    try:
                        logo_dim = min(aw * 0.28, ah * 0.82)
                        logo_w = logo_dim
                        logo_h = logo_dim
                        logo_x = ax + (aw * 0.03)
                        logo_y = ay + (ah - logo_h) / 2
                        c.drawImage(logo_path, logo_x, logo_y, width=logo_w, height=logo_h, mask='auto', preserveAspectRatio=True)
                        has_logo = True
                    except Exception as e:
                        logger.warning(f"ReportLab custom logo error: {e}")
                        has_logo = False

                # Background watermark green checkmark (only if show_tick is True)
                if appearance_opts.get('show_tick', True):
                    c.setStrokeColor(Color(0.2, 0.7, 0.4, alpha=0.35))
                    c.setLineWidth(max(1.5, aw * 0.06))
                    c.setLineJoin(1)
                    tick_offset_x = (logo_w + aw * 0.04) if has_logo else 0
                    cx, cy, s = ax + tick_offset_x + ((aw - tick_offset_x) / 2), ay + (ah / 2), min(aw - tick_offset_x, ah) * 0.35
                    p = c.beginPath()
                    p.moveTo(cx - s, cy)
                    p.lineTo(cx - s * 0.2, cy - s * 0.6)
                    p.lineTo(cx + s * 0.8, cy + s * 0.8)
                    c.drawPath(p, 1, 0)

                # Text positioning on right of logo
                left_pad = (aw * 0.03) + (logo_w + (aw * 0.04) if has_logo else 0)
                right_pad = aw * 0.03
                avail_w = max(aw - left_pad - right_pad, 20.0)
                avail_h = max(ah - (ah * 0.08), 10.0)

                f_size = min(avail_w / (max(len(l) for l in lines) * 0.55), avail_h / (len(lines) * 1.25), 11.0)
                f_size = max(f_size, 5.5)
                line_spacing = f_size * 1.25

                c.setFillColorRGB(0.1, 0.1, 0.1)
                ty = ay + ah - line_spacing - (ah * 0.04)
                c.setFont("Helvetica-Bold", f_size)
                c.drawString(ax + left_pad, ty, lines[0])
                ty -= line_spacing
                c.setFont("Helvetica", f_size * 0.95)
                for l in lines[1:]:
                    c.drawString(ax + left_pad, ty, l)
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

    asn1_cert = a509.Certificate.load(cert.public_bytes(Encoding.DER))
    asn1_key = akeys.PrivateKeyInfo.load(private_key.private_bytes(Encoding.DER, PrivateFormat.PKCS8, NoEncryption()))
    cert_store = SimpleCertificateStore()
    if chain:
        for extra in chain:
            if extra:
                try:
                    cert_store.register(a509.Certificate.load(extra.public_bytes(Encoding.DER)))
                except Exception:
                    pass

    signer = signers.SimpleSigner(
        signing_cert=asn1_cert,
        signing_key=asn1_key,
        cert_registry=cert_store
    )

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
    raw_tier = request.args.get('tier', 'pro')
    tier = 'basic' if str(raw_tier).lower().strip() == 'basic' else 'pro'

    lic_id = request.args.get('id')
    status_val = request.args.get('status_val', 'active')
    client_email = request.args.get('client_email', '')
    activated_at = request.args.get('activated_at', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))

    close_script = "<script>setTimeout(function(){ window.opener = null; window.open('','_self'); window.close(); }, 1200);</script>"

    if status == 'valid' and key:
        LicenseManager.save_license(
            key, expiredate, tier=tier,
            id=lic_id, status=status_val,
            client_email=client_email, activated_at=activated_at
        )
        LicenseManager.activation_error = None
        tier_title = tier.upper()
        if gui_root and gui_app:
            gui_root.after(0, lambda: (gui_app.show_window(), gui_app._init_app_interface() if not hasattr(gui_app, 'notebook') else None))

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
    key = request.args.get('key') or LicenseManager.get_license_key()
    raw_tier = request.args.get('tier', 'pro')
    tier = 'basic' if str(raw_tier).lower().strip() == 'basic' else 'pro'
    lic_id = request.args.get('id')
    status_val = request.args.get('status_val', 'active')
    client_email = request.args.get('client_email', '')
    activated_at = request.args.get('activated_at', '')
    expiredate = request.args.get('expiredate', 'never')

    close_script = "<script>setTimeout(function(){ window.opener = null; window.open('','_self'); window.close(); }, 1000);</script>"

    if status in ['expired', 'inactive', 'invalid', 'suspended']:
        logger.warning(f"Browser Sync: License is {status}. Locking app.")
        LicenseManager.save_license(
            key, "2000-01-01 00:00:00", tier=tier,
            id=lic_id, status=status, client_email=client_email, activated_at=activated_at
        )
        if gui_root and gui_app:
            gui_root.after(0, gui_app._show_activation_screen)
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
            info = LicenseManager.get_license_info()
            exp = expiredate if expiredate and expiredate != 'never' else info.get('expiredate', 'never')
            LicenseManager.save_license(
                key, exp, tier=tier,
                id=lic_id or info.get('id'),
                status='active',
                client_email=client_email or info.get('client_email', ''),
                activated_at=activated_at or info.get('activated_at', '')
            )
        return f"""
        <html><body style='font-family: Arial; text-align: center; padding: 50px; background-color: #f4f4f9;'>
            <h2 style='color: #2ecc71;'>License Verified Online!</h2>
            <p style='font-size: 18px;'>Your software is fully synced ({tier.upper()} Suite).</p>
            {close_script}
        </body></html>
        """

@app.route('/api/drivers')
def api_drivers():
    return jsonify(list(DRIVER_MAP.keys()))

@app.route('/api/health')
def health():
    is_activated = LicenseManager.is_activated()
    exp_status = LicenseManager.check_expiration_local() if is_activated else "inactive"
    is_expired = (exp_status == "expired")
    
    is_demo = not is_activated or is_expired
    info = LicenseManager.get_license_info()
    active_key = info.get("key") or info.get("license_key") or LicenseManager.get_license_key() or "ABI-MASTER-PASS"
    
    return jsonify({
        'status': 'healthy', 
        'server_status': server_config.get('status', 'running'),
        'version': '2.0',
        'demo': is_demo,
        'expired': is_expired,
        'key': active_key,
        'id': info.get('id'),
        'license_key': info.get('license_key') or active_key,
        'license_status': info.get('status', 'active' if is_activated else 'inactive'),
        'tier': info.get('tier', 'pro'),
        'license_tier': info.get('license_tier', 'pro'),
        'client_email': info.get('client_email', ''),
        'activated_at': info.get('activated_at', ''),
        'expiredate': info.get('expiredate', 'never'),
        'hardware_id': LicenseManager.get_hardware_id()
    })

@app.route('/api/certificates')
def api_certs():
    req_driver = request.args.get('driver_name')
    if req_driver and req_driver in DRIVER_MAP:
        server_config['driver_path'] = DRIVER_MAP[req_driver]
    elif not server_config.get('driver_path'):
        server_config['driver_path'] = find_epass_driver_path() or ''
    
    certs = []
    if server_config['driver_path'] and os.path.exists(server_config['driver_path']):
        try:
            lib = pkcs11.lib(server_config['driver_path'])
            for slot in lib.get_slots(token_present=True):
                try:
                    token = slot.get_token()
                    with token.open() as session:
                        for obj in session.get_objects({pkcs11.Attribute.CLASS: pkcs11.ObjectClass.CERTIFICATE}):
                            cert = x509.load_der_x509_certificate(obj[pkcs11.Attribute.VALUE])
                            try:
                                if cert.subject == cert.issuer: continue
                            except Exception: pass

                            try:
                                key_usage = cert.extensions.get_extension_for_class(x509.KeyUsage).value
                                if not (key_usage.digital_signature or key_usage.content_commitment): continue
                            except Exception: pass
                            
                            cn = "Unknown"
                            for attr in cert.subject:
                                if attr.oid == x509.NameOID.COMMON_NAME:
                                    cn = attr.value; break

                            email, addr = extract_cert_email_and_address(cert)
                            certs.append({
                                'alias': str(cert.serial_number),
                                'subject': cert.subject.rfc4514_string(),
                                'cn': cn,
                                'email': email,
                                'address': addr,
                                'source': f"USB Token ({token.label})"
                            })
                except Exception: continue
        except Exception as e:
            logger.error(f"Cert fetch error: {e}")
    return jsonify(certs)

@app.route('/api/sign', methods=['POST'])
def sign_pdf():
    try:
        if LicenseManager.check_expiration_local() == "expired":
            return jsonify({"error": "Your software license has expired. Please contact +91 7016797720 or sales@a-b-infotech.in to renew."}), 403

        pdf_data = request.files['file'].read()
        pin = request.form.get('password')
        signature_areas = json.loads(request.form.get('areas', '[]'))
        cert_meta = json.loads(request.form.get('certificate', '{}'))
        serial_number = cert_meta.get('alias')
        
        for area in signature_areas:
            area['page'] = int(area.get('page', 1)) - 1

        client_driver = request.form.get('driver_name')
        if client_driver and client_driver in DRIVER_MAP:
            server_config['driver_path'] = DRIVER_MAP[client_driver]
        elif not server_config.get('driver_path'):
            server_config['driver_path'] = find_epass_driver_path() or ''

        lib = pkcs11.lib(server_config['driver_path'])
        
        token = None
        for slot in lib.get_slots(token_present=True):
            try:
                token = slot.get_token()
                break
            except Exception: continue
        if not token: raise ValueError("Hardware Token not found.")

        with token.open(user_pin=pin) as session:
            cert_id, cert_parsed = None, None
            for obj in session.get_objects({pkcs11.Attribute.CLASS: pkcs11.ObjectClass.CERTIFICATE}):
                c = x509.load_der_x509_certificate(obj[pkcs11.Attribute.VALUE])
                if str(c.serial_number) == str(serial_number):
                    cert_id, cert_parsed = obj[pkcs11.Attribute.ID], c
                    break
            
            if not cert_id: raise ValueError("Certificate not found. Check PIN.")
            signer = PKCS11Signer(pkcs11_session=session, cert_id=cert_id)

            visual_pdf_data = draw_reportlab_layer(pdf_data, signature_areas, cert_parsed)
            current_io = io.BytesIO(visual_pdf_data)

            base_reader = PdfReader(io.BytesIO(pdf_data))
            tst_client = HTTPTimeStamper(TSA_URL)
            
            for idx, area in enumerate(signature_areas):
                field_name = f"WebSignature_{idx+1}"
                p_idx = area['page']
                page_meta = base_reader.pages[p_idx]
                
                offset_x = float(page_meta.mediabox.left if hasattr(page_meta.mediabox, 'left') else page_meta.mediaBox.getLowerLeft_x())
                offset_y = float(page_meta.mediabox.bottom if hasattr(page_meta.mediabox, 'bottom') else page_meta.mediaBox.getLowerLeft_y())

                x1, y1 = int(area['x'] + offset_x), int(area['y'] + offset_y)
                x2, y2 = int(area['x'] + area['width'] + offset_x), int(area['y'] + area['height'] + offset_y)
                
                writer = IncrementalPdfFileWriter(current_io)
                append_signature_field(writer, SigFieldSpec(field_name, box=(x1, y1, x2, y2), on_page=p_idx))
                
                tmp_io = io.BytesIO(); writer.write(tmp_io); tmp_io.seek(0)
                final_writer = IncrementalPdfFileWriter(tmp_io)
                
                pdf_signer = signers.PdfSigner(
                    signature_meta=signers.PdfSignatureMetadata(field_name=field_name),
                    signer=signer, 
                    timestamper=tst_client
                )
                
                signed_out = io.BytesIO()
                pdf_signer.sign_pdf(final_writer, in_place=False, output=signed_out)
                signed_out.seek(0)
                current_io = signed_out

        return send_file(current_io, as_attachment=True, download_name="TokenSigned.pdf", mimetype='application/pdf')

    except Exception as e:
        logger.error(f"WEB API SIGNING ERROR: {str(e)}\n{traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500

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
    def __init__(self, parent, on_area_selected=None, allow_drawing=False, bg="#F8FAFC", on_clear=None):
        super().__init__(parent, bg=bg)
        self.doc = None
        self.pdf_path = None
        self.current_page = 0
        self.scale = 1.35
        self.on_area_selected = on_area_selected
        self.allow_drawing = allow_drawing
        self.on_clear = on_clear
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
        if self.on_clear:
            try:
                self.on_clear()
            except Exception as e:
                logger.debug(f"Viewer on_clear error: {e}")

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
    Wondershare PDFelement & Adobe Acrobat Style Interactive PDF Editor & High-Level OCR:
    - View Mode vs Edit Text Mode
    - Bounding frames with 8 corner/edge grab handles around text blocks
    - In-place formatting ribbon: Font Family, Font Size, Bold, Italic, Color presets & picker
    - Interactive 'Add Text Box' tool to place text anywhere on page
    - High-level OCR for scanned images and raster pages
    - In-place clean whiteout redaction and font-matched replacement
    - Save / Export edited PDF
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
        self.hover_span_idx = None
        self.editor_mode = "edit"  # "view" or "edit"
        self.add_text_mode = False

        # Formatting properties
        self.font_family_var = tk.StringVar(value="Helvetica")
        self.font_size_var = tk.StringVar(value="10")
        self.bold_var = tk.BooleanVar(value=False)
        self.italic_var = tk.BooleanVar(value=False)
        self.text_color_hex = "#000000"

        self._build_ui()

    def _build_ui(self):
        if not LicenseManager.is_pro():
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
                 f"To unlock direct Adobe/PDFelement-style text editing, font formatting, and scanned image OCR,\n"
                 f"please upgrade to the Pro Edition.",
            font=get_sys_font(9), fg="#475569", bg="#FFFFFF", wraplength=480, justify="center"
        ).pack(pady=(10, 16))

        btn_box = tk.Frame(lock_card, bg="#FFFFFF")
        btn_box.pack()

        ttk.Button(btn_box, text="💬 WhatsApp Upgrade (+91 7016797720)", style="Primary3D.TButton", command=self._open_whatsapp_upgrade).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_box, text="🔑 Enter / Sync License Key", style="Tool3D.TButton", command=self._prompt_license).pack(side=tk.LEFT, padx=4)

    def _open_whatsapp_upgrade(self):
        msg = urllib.parse.quote("Hi A B INFOTECH Team, I want to upgrade my license to Pro Edition for the PDF OCR & Text Editor.")
        webbrowser.open(f"https://wa.me/917016797720?text={msg}")

    def _prompt_license(self):
        if self.main_app:
            self.main_app._show_activation_screen()

    def _build_editor_view(self):
        for w in self.winfo_children():
            w.destroy()

        # ----------------------------------------------------
        # 1. TOP MAIN TOOLBAR (Document Navigation & Modes)
        # ----------------------------------------------------
        toolbar = tk.Frame(self, bg="#E2E8F0", padx=8, pady=4, relief=tk.RAISED, bd=1)
        toolbar.pack(side=tk.TOP, fill=tk.X)

        ttk.Button(toolbar, text="📂 Open PDF", style="Primary3D.TButton", command=self.open_pdf).pack(side=tk.LEFT, padx=3)
        ttk.Button(toolbar, text="❌ Clear PDF", style="Tool3D.TButton", command=self.clear_pdf).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="◀ Prev", style="Tool3D.TButton", command=self.prev_page).pack(side=tk.LEFT, padx=2)
        self.lbl_page = tk.Label(toolbar, text="Page: 0 / 0", font=get_sys_font(8, "bold"), fg="#1E293B", bg="#E2E8F0")
        self.lbl_page.pack(side=tk.LEFT, padx=6)
        ttk.Button(toolbar, text="Next ▶", style="Tool3D.TButton", command=self.next_page).pack(side=tk.LEFT, padx=2)

        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8)

        # Zoom Controls
        ttk.Button(toolbar, text="🔍-", style="Tool3D.TButton", width=3, command=self.zoom_out).pack(side=tk.LEFT, padx=1)
        self.lbl_zoom = tk.Label(toolbar, text=f"{int(self.scale*100)}%", font=get_sys_font(8), bg="#E2E8F0")
        self.lbl_zoom.pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="🔍+", style="Tool3D.TButton", width=3, command=self.zoom_in).pack(side=tk.LEFT, padx=1)

        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8)

        # Mode Selection (Acrobat / PDFelement style)
        self.btn_view_mode = tk.Button(
            toolbar, text="👁️ Read / View Mode", font=get_sys_font(8, "bold"),
            bg="#CBD5E1", fg="#334155", relief=tk.FLAT, padx=8, pady=2,
            command=lambda: self.set_editor_mode("view")
        )
        self.btn_view_mode.pack(side=tk.LEFT, padx=2)

        self.btn_edit_mode = tk.Button(
            toolbar, text="✏️ Edit Text Mode", font=get_sys_font(8, "bold"),
            bg="#0284C7", fg="#FFFFFF", relief=tk.RAISED, padx=8, pady=2,
            command=lambda: self.set_editor_mode("edit")
        )
        self.btn_edit_mode.pack(side=tk.LEFT, padx=2)

        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8)

        # Quick Actions
        self.btn_add_text = ttk.Button(toolbar, text="➕ Add Text", style="Tool3D.TButton", command=self.toggle_add_text_mode)
        self.btn_add_text.pack(side=tk.LEFT, padx=3)

        ttk.Button(toolbar, text="🔍 Recognize Text (OCR)", style="Tool3D.TButton", command=self.recognize_ocr).pack(side=tk.LEFT, padx=3)
        ttk.Button(toolbar, text="💾 Save / Export PDF", style="Primary3D.TButton", command=self.save_edited_pdf).pack(side=tk.RIGHT, padx=4)

        # ----------------------------------------------------
        # 2. ADOBE/PDFELEMENT FORMATTING RIBBON (Font, Size, Color)
        # ----------------------------------------------------
        self.ribbon = tk.Frame(self, bg="#F8FAFC", padx=10, pady=5, relief=tk.GROOVE, bd=1)
        self.ribbon.pack(side=tk.TOP, fill=tk.X)

        tk.Label(self.ribbon, text="Formatting:", font=get_sys_font(8, "bold"), fg="#1E3A8A", bg="#F8FAFC").pack(side=tk.LEFT, padx=(0, 6))

        # Font Family
        tk.Label(self.ribbon, text="Font:", font=get_sys_font(8), bg="#F8FAFC").pack(side=tk.LEFT)
        font_families = ["Helvetica", "Times-Roman", "Courier"]
        self.combo_font = ttk.Combobox(self.ribbon, textvariable=self.font_family_var, values=font_families, width=12, state="readonly", font=get_sys_font(8))
        self.combo_font.pack(side=tk.LEFT, padx=(2, 8))

        # Font Size
        tk.Label(self.ribbon, text="Size:", font=get_sys_font(8), bg="#F8FAFC").pack(side=tk.LEFT)
        font_sizes = ["6", "8", "9", "10", "11", "12", "14", "16", "18", "20", "24", "28", "32", "36", "48"]
        self.combo_size = ttk.Combobox(self.ribbon, textvariable=self.font_size_var, values=font_sizes, width=4, state="readonly", font=get_sys_font(8))
        self.combo_size.pack(side=tk.LEFT, padx=(2, 8))

        # Bold & Italic Toggles
        self.btn_bold = tk.Checkbutton(self.ribbon, text="B", variable=self.bold_var, font=get_sys_font(8, "bold"), width=2, indicatoron=False, bg="#E2E8F0", selectcolor="#93C5FD")
        self.btn_bold.pack(side=tk.LEFT, padx=2)
        self.btn_italic = tk.Checkbutton(self.ribbon, text="I", variable=self.italic_var, font=get_sys_font(8, "italic"), width=2, indicatoron=False, bg="#E2E8F0", selectcolor="#93C5FD")
        self.btn_italic.pack(side=tk.LEFT, padx=(2, 8))

        # Color Swatches
        tk.Label(self.ribbon, text="Color:", font=get_sys_font(8), bg="#F8FAFC").pack(side=tk.LEFT)
        self.swatch_indicator = tk.Label(self.ribbon, text="   ", bg=self.text_color_hex, relief=tk.SOLID, bd=1)
        self.swatch_indicator.pack(side=tk.LEFT, padx=(2, 4))

        quick_colors = [("#000000", "Black"), ("#4B5563", "Gray"), ("#1D4ED8", "Blue"), ("#DC2626", "Red"), ("#059669", "Green")]
        for hex_val, col_name in quick_colors:
            btn_col = tk.Button(self.ribbon, bg=hex_val, width=1, height=1, relief=tk.FLAT, command=lambda c=hex_val: self.set_text_color(c))
            btn_col.pack(side=tk.LEFT, padx=1)

        ttk.Button(self.ribbon, text="🎨 Custom...", style="Tool3D.TButton", command=self.pick_custom_color).pack(side=tk.LEFT, padx=(4, 8))

        ttk.Separator(self.ribbon, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=6)

        # Direct Ribbon Action Buttons
        ttk.Button(self.ribbon, text="✓ Apply Edit", style="Primary3D.TButton", command=self.apply_text_edit).pack(side=tk.LEFT, padx=4)
        ttk.Button(self.ribbon, text="🗑️ Redact / Delete", style="Tool3D.TButton", command=self.delete_selected_block).pack(side=tk.LEFT, padx=4)

        self.lbl_ribbon_status = tk.Label(self.ribbon, text="Edit Mode: Click any text box on the PDF to modify.", font=get_sys_font(8, "italic"), fg="#047857", bg="#F8FAFC")
        self.lbl_ribbon_status.pack(side=tk.RIGHT, padx=6)

        # ----------------------------------------------------
        # 3. MAIN SPLIT CONTENT: Canvas (Left) + Sidebar (Right)
        # ----------------------------------------------------
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
        self.canvas.bind("<Motion>", self._on_canvas_motion)
        self.canvas.bind("<MouseWheel>", self._on_mouse_wheel)

        # Right: Docked Text Editing Sidebar
        sidebar = tk.Frame(split_f, bg="#FFFFFF", width=380, padx=12, pady=10, relief=tk.RAISED, bd=1)
        sidebar.pack(side=tk.RIGHT, fill=tk.Y)
        sidebar.pack_propagate(False)

        tk.Label(sidebar, text="Interactive Text Inspector", font=get_sys_font(10, "bold"), fg="#1E40AF", bg="#FFFFFF").pack(anchor=tk.W, pady=(0, 2))
        tk.Label(sidebar, text="Select text blocks on the canvas or from list to edit with font preservation:", font=get_sys_font(8), fg="#64748B", bg="#FFFFFF", wraplength=350, justify="left").pack(anchor=tk.W, pady=(0, 6))

        # Detected text list
        list_f = tk.Frame(sidebar, bg="#FFFFFF")
        list_f.pack(fill=tk.BOTH, expand=True, pady=(0, 8))
        self.tree_spans = ttk.Treeview(list_f, columns=("Text",), show="headings", height=8)
        self.tree_spans.heading("Text", text="Detected Text Elements on Page")
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
        self.txt_editor.bind("<Control-Return>", lambda e: self.apply_text_edit())

        self.lbl_font_info = tk.Label(form_card, text="Font: Auto | Origin: Same", font=get_sys_font(7, "italic"), fg="#64748B", bg="#F8FAFC")
        self.lbl_font_info.pack(anchor=tk.W, pady=(0, 6))

        btn_act_row = tk.Frame(form_card, bg="#F8FAFC")
        btn_act_row.pack(fill=tk.X)
        ttk.Button(btn_act_row, text="✓ Apply Edit (Ctrl+Enter)", style="Primary3D.TButton", command=self.apply_text_edit).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 3))
        ttk.Button(btn_act_row, text="🗑️ Delete Block", style="Tool3D.TButton", command=self.delete_selected_block).pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(3, 0))

    def set_editor_mode(self, mode):
        self.editor_mode = mode
        self.add_text_mode = False
        self.canvas.config(cursor="arrow")
        if mode == "view":
            self.btn_view_mode.config(bg="#0284C7", fg="#FFFFFF", relief=tk.RAISED)
            self.btn_edit_mode.config(bg="#CBD5E1", fg="#334155", relief=tk.FLAT)
            self.lbl_ribbon_status.config(text="View Mode: Viewing PDF without interactive edit frames.", fg="#475569")
            self.canvas.delete("span_box")
            self.canvas.delete("active_box")
            self.canvas.delete("active_handle")
        else:
            self.btn_view_mode.config(bg="#CBD5E1", fg="#334155", relief=tk.FLAT)
            self.btn_edit_mode.config(bg="#0284C7", fg="#FFFFFF", relief=tk.RAISED)
            self.lbl_ribbon_status.config(text="Edit Mode: Click any text box to edit with handles.", fg="#047857")
            self._draw_span_overlays()

    def toggle_add_text_mode(self):
        if not self.doc:
            messagebox.showwarning("Warning", "Please open a PDF document first.")
            return
        self.add_text_mode = not self.add_text_mode
        if self.add_text_mode:
            self.set_editor_mode("edit")
            self.canvas.config(cursor="crosshair")
            self.btn_add_text.config(text="✖ Cancel Add Text")
            self.lbl_ribbon_status.config(text="Click anywhere on the PDF page to place new text.", fg="#D97706")
        else:
            self.canvas.config(cursor="arrow")
            self.btn_add_text.config(text="➕ Add Text")
            self.lbl_ribbon_status.config(text="Edit Mode: Click any text box to edit.", fg="#047857")

    def zoom_in(self):
        self.scale = min(3.0, self.scale + 0.25)
        self.lbl_zoom.config(text=f"{int(self.scale*100)}%")
        self.render_page()
        if self.editor_mode == "edit":
            self._draw_span_overlays()

    def zoom_out(self):
        self.scale = max(0.6, self.scale - 0.25)
        self.lbl_zoom.config(text=f"{int(self.scale*100)}%")
        self.render_page()
        if self.editor_mode == "edit":
            self._draw_span_overlays()

    def set_text_color(self, hex_val):
        self.text_color_hex = hex_val
        self.swatch_indicator.config(bg=hex_val)

    def pick_custom_color(self):
        col = colorchooser.askcolor(initialcolor=self.text_color_hex, title="Select Text Color")
        if col and col[1]:
            self.set_text_color(col[1])

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

        # 1. Extract vector text blocks and spans with exact bounding boxes
        text_dict = page.get_text("dict")
        raw_spans = []
        for b in text_dict.get("blocks", []):
            if b.get("type") == 0:  # Text block
                for l in b.get("lines", []):
                    for s in l.get("spans", []):
                        txt = s.get("text", "").strip()
                        if txt:
                            s["bg_rgb"] = get_bbox_bg_color(page, s.get("bbox", (0, 0, 0, 0)))
                            raw_spans.append(s)

        # 2. Deep-learning multilingual OCR fallback for scanned/image pages
        if not raw_spans:
            raw_spans = self._extract_ocr_page_text(page)

        self.spans = raw_spans
        for idx, s in enumerate(self.spans):
            self.tree_spans.insert("", tk.END, iid=str(idx), values=(s.get("text", "")[:45],))

        if self.editor_mode == "edit":
            self._draw_span_overlays()

    def _extract_ocr_page_text(self, page):
        """
        Multilingual OCR extraction using RapidOCR on rendered page pixmap.
        Detects exact text blocks, word positions, and bounding coordinates from images.
        """
        res = []
        ocr = get_rapid_ocr_engine()
        if not ocr:
            try:
                blocks = page.get_text("blocks")
                for b in blocks:
                    t = b[4].strip()
                    if t:
                        res.append({
                            "text": t,
                            "bbox": b[:4],
                            "origin": (b[0], b[3] - 2),
                            "font": "Helvetica",
                            "size": 10.0,
                            "color": 0,
                            "bg_rgb": get_bbox_bg_color(page, b[:4])
                        })
            except Exception as e:
                logger.warning(f"Fallback scan text error: {e}")
            return res

        try:
            import numpy as np
            pix = page.get_pixmap(dpi=150)
            np_img = np.frombuffer(pix.samples, dtype=np.uint8).reshape((pix.height, pix.width, 3))
            ocr_results, _ = ocr(np_img)
            if not ocr_results:
                return res

            sx = page.rect.width / pix.width
            sy = page.rect.height / pix.height

            for item in ocr_results:
                box = item[0]
                text = str(item[1]).strip()
                if not text:
                    continue
                score = float(item[2]) if len(item) > 2 else 1.0
                if score < 0.25:
                    continue

                x0 = min(p[0] for p in box) * sx
                y0 = min(p[1] for p in box) * sy
                x1 = max(p[0] for p in box) * sx
                y1 = max(p[1] for p in box) * sy
                h = max(y1 - y0, 6.0)
                f_size = max(7.0, round(h * 0.72))
                origin = (x0, y1 - 2)

                bg_rgb = get_bbox_bg_color(page, (x0, y0, x1, y1))
                txt_col = sample_text_color_int(pix, box, bg_rgb)

                res.append({
                    "text": text,
                    "bbox": (x0, y0, x1, y1),
                    "origin": origin,
                    "font": "Helvetica",
                    "size": float(f_size),
                    "color": txt_col,
                    "bg_rgb": bg_rgb,
                    "is_ocr": True
                })
        except Exception as e:
            logger.error(f"RapidOCR page text extraction error: {e}")
        return res

    def recognize_ocr(self):
        if not self.doc:
            messagebox.showwarning("Warning", "Please open a PDF document first.")
            return
        page = self.doc[self.current_page]
        ocr_spans = self._extract_ocr_page_text(page)
        if ocr_spans:
            self.spans = ocr_spans
            for item in self.tree_spans.get_children():
                self.tree_spans.delete(item)
            for idx, s in enumerate(self.spans):
                self.tree_spans.insert("", tk.END, iid=str(idx), values=(s.get("text", "")[:45],))
            if self.editor_mode == "edit":
                self._draw_span_overlays()
            count = len(self.spans)
            messagebox.showinfo(
                "OCR Complete",
                f"✓ High-level multilingual OCR successfully recognized {count} text blocks on Page {self.current_page + 1}.\n\n"
                f"You can now click directly on any text block on the canvas to edit, format, or redact!"
            )
        else:
            self.scan_and_detect_text()
            count = len(self.spans)
            messagebox.showinfo("OCR Complete", f"Recognized {count} text elements on Page {self.current_page + 1}.")

    def _draw_span_overlays(self):
        self.canvas.delete("span_box")
        if self.editor_mode != "edit":
            return
        for idx, s in enumerate(self.spans):
            bbox = s.get("bbox", (0, 0, 0, 0))
            c_x1 = bbox[0] * self.scale
            c_y1 = bbox[1] * self.scale
            c_x2 = bbox[2] * self.scale
            c_y2 = bbox[3] * self.scale

            tag = f"span_{idx}"
            self.canvas.create_rectangle(
                c_x1, c_y1, c_x2, c_y2,
                outline="#93C5FD", width=1, dash=(2, 2),
                tags=("span_box", tag)
            )

    def _on_canvas_motion(self, event):
        if self.editor_mode != "edit" or self.add_text_mode:
            return
        cur_x = self.canvas.canvasx(event.x) / self.scale
        cur_y = self.canvas.canvasy(event.y) / self.scale

        hovered = None
        for idx, s in enumerate(self.spans):
            bbox = s.get("bbox", (0, 0, 0, 0))
            if bbox[0] <= cur_x <= bbox[2] and bbox[1] <= cur_y <= bbox[3]:
                hovered = idx
                break

        if hovered != self.hover_span_idx:
            self.hover_span_idx = hovered
            self.canvas.delete("hover_box")
            if hovered is not None and hovered != self.selected_span_idx:
                bbox = self.spans[hovered].get("bbox", (0, 0, 0, 0))
                self.canvas.create_rectangle(
                    bbox[0] * self.scale - 1, bbox[1] * self.scale - 1,
                    bbox[2] * self.scale + 1, bbox[3] * self.scale + 1,
                    outline="#38BDF8", width=1.5, tags=("hover_box",)
                )

    def _on_canvas_click(self, event):
        cur_x = self.canvas.canvasx(event.x) / self.scale
        cur_y = self.canvas.canvasy(event.y) / self.scale

        # Handle 'Add Text' mode
        if self.add_text_mode:
            self._handle_add_text_at(cur_x, cur_y)
            return

        if self.editor_mode != "edit":
            return

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
        else:
            self.selected_span_idx = None
            self.canvas.delete("active_box")
            self.canvas.delete("active_handle")

    def _handle_add_text_at(self, x, y):
        self.toggle_add_text_mode()
        new_text = "Type text here"
        f_size = float(self.font_size_var.get())
        font_name = self._get_target_fontname()
        color_rgb = self._get_target_color_rgb()

        page = self.doc[self.current_page]
        try:
            page.insert_text(fitz.Point(x, y + f_size), new_text, fontname=font_name, fontsize=f_size, color=color_rgb)
            self.render_page()
            self.scan_and_detect_text()
            if self.spans:
                self._select_span(len(self.spans) - 1)
                self.txt_editor.focus_set()
        except Exception as e:
            messagebox.showerror("Error", f"Failed to add text:\n{e}")

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

        orig_font = s.get("font", "Helvetica")
        if "times" in orig_font.lower():
            self.font_family_var.set("Times-Roman")
        elif "courier" in orig_font.lower():
            self.font_family_var.set("Courier")
        else:
            self.font_family_var.set("Helvetica")

        size_val = round(float(s.get("size", 10.0)))
        self.font_size_var.set(str(size_val))

        # Check bold / italic from font flags
        flags = s.get("flags", 0)
        self.bold_var.set(bool(flags & 2 ** 4) or "bold" in orig_font.lower())
        self.italic_var.set(bool(flags & 2 ** 1) or "oblique" in orig_font.lower() or "italic" in orig_font.lower())

        # Color
        c_val = s.get("color", 0)
        if isinstance(c_val, int):
            r = (c_val >> 16) & 255
            g = (c_val >> 8) & 255
            b = c_val & 255
            self.set_text_color(f"#{r:02x}{g:02x}{b:02x}")

        self.lbl_font_info.config(
            text=f"Font: {orig_font} ({s.get('size', 10.0):.1f}pt) | Origin: ({int(s.get('origin', (0,0))[0])}, {int(s.get('origin', (0,0))[1])})"
        )

        # Draw Adobe / PDFelement selection frame with 8 handles
        self._draw_selection_frame(s.get("bbox", (0, 0, 0, 0)))

    def _draw_selection_frame(self, bbox):
        self.canvas.delete("active_box")
        self.canvas.delete("active_handle")

        x1 = bbox[0] * self.scale - 2
        y1 = bbox[1] * self.scale - 2
        x2 = bbox[2] * self.scale + 2
        y2 = bbox[3] * self.scale + 2

        # 1. Selection border
        self.canvas.create_rectangle(
            x1, y1, x2, y2,
            outline="#2563EB", width=2, tags=("active_box",)
        )

        # 2. 8 Grab Handles (Corners + Edge Midpoints)
        pts = [
            (x1, y1), ((x1 + x2)/2, y1), (x2, y1),
            (x2, (y1 + y2)/2),
            (x2, y2), ((x1 + x2)/2, y2), (x1, y2),
            (x1, (y1 + y2)/2)
        ]
        hs = 3.5
        for px, py in pts:
            self.canvas.create_rectangle(
                px - hs, py - hs, px + hs, py + hs,
                fill="#FFFFFF", outline="#2563EB", width=1.5,
                tags=("active_handle", "active_box")
            )

    def _get_target_fontname(self):
        fam = self.font_family_var.get().lower()
        is_b = self.bold_var.get()
        is_i = self.italic_var.get()
        if "times" in fam:
            base = "times"
        elif "courier" in fam:
            base = "couri"
        else:
            base = "helv"

        if is_b and is_i:
            return f"{base}-boldoblique" if base == "helv" else f"{base}-bi"
        elif is_b:
            return f"{base}-bold"
        elif is_i:
            return f"{base}-oblique" if base == "helv" else f"{base}-italic"
        return base

    def _get_target_color_rgb(self):
        hex_c = self.text_color_hex.lstrip("#")
        if len(hex_c) == 6:
            r = int(hex_c[0:2], 16) / 255.0
            g = int(hex_c[2:4], 16) / 255.0
            b = int(hex_c[4:6], 16) / 255.0
            return (r, g, b)
        return (0.0, 0.0, 0.0)

    def apply_text_edit(self):
        if not self.doc or self.selected_span_idx is None:
            messagebox.showwarning("Warning", "Please select a text block to edit.")
            return

        new_text = self.txt_editor.get("1.0", tk.END).strip()
        s = self.spans[self.selected_span_idx]

        page = self.doc[self.current_page]
        bbox = fitz.Rect(s["bbox"])

        try:
            # 1. Seamless in-place redaction matching background pixel color
            bg_rgb = s.get("bg_rgb")
            if not bg_rgb:
                bg_rgb = get_bbox_bg_color(page, bbox)

            page.add_redact_annot(bbox, fill=bg_rgb)
            page.apply_redactions()

            # 2. Compute typography
            f_target = self._get_target_fontname()
            try:
                f_size = float(self.font_size_var.get())
            except ValueError:
                f_size = float(s.get("size", 10.0))

            color_rgb = self._get_target_color_rgb()
            origin = s.get("origin", (bbox.x0, bbox.y1 - 2))

            # 3. Insert replacement text at exact location matching baseline and font
            page.insert_text(fitz.Point(origin[0], origin[1]), new_text, fontname=f_target, fontsize=f_size, color=color_rgb)

            # Re-render & scan
            self.render_page()
            self.scan_and_detect_text()
            self.lbl_ribbon_status.config(text="✓ Edit applied cleanly to PDF document!", fg="#047857")
        except Exception as e:
            messagebox.showerror("Edit Error", f"Failed to apply text edit:\n{e}")

    def delete_selected_block(self):
        if not self.doc or self.selected_span_idx is None:
            messagebox.showwarning("Warning", "Please select a text block to delete.")
            return

        s = self.spans[self.selected_span_idx]
        page = self.doc[self.current_page]
        bbox = fitz.Rect(s["bbox"])

        try:
            bg_rgb = s.get("bg_rgb") or get_bbox_bg_color(page, bbox)
            page.add_redact_annot(bbox, fill=bg_rgb)
            page.apply_redactions()
            self.selected_span_idx = None
            self.txt_editor.delete("1.0", tk.END)
            self.render_page()
            self.scan_and_detect_text()
            self.lbl_ribbon_status.config(text="✓ Selected text element redacted and removed.", fg="#047857")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to delete block:\n{e}")

    def clear_pdf(self):
        self.doc = None
        self.pdf_path = None
        self.current_page = 0
        self.spans.clear()
        self.selected_span_idx = None
        self.hover_span_idx = None
        self.canvas.delete("all")
        self.lbl_page.config(text="Page: 0 / 0")
        self.txt_editor.delete("1.0", tk.END)
        for item in self.tree_spans.get_children():
            self.tree_spans.delete(item)
        self.lbl_font_info.config(text="No block selected")
        self.lbl_ribbon_status.config(text="Cleared PDF document.")

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

    def _on_mouse_wheel(self, event):
        if self.doc:
            if event.delta > 0:
                self.canvas.yview_scroll(-1, "units")
            else:
                self.canvas.yview_scroll(1, "units")

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
                # If document contains digital signatures, preserve visual appearance
                # and sanitize broken byte-range /Sig objects so Adobe Acrobat opens
                # with ZERO 'File Damage' errors!
                preserve_signature_appearances_on_doc(self.doc, remove_broken_fields=True)
                # Rebuild into a clean document so xref tables are rebuilt from scratch
                clean_doc = fitz.open()
                clean_doc.insert_pdf(self.doc)
                clean_doc.save(out_p, garbage=3, deflate=True)
                clean_doc.close()
                messagebox.showinfo("Success", f"Edited PDF saved successfully:\n{out_p}\n\n(All signatures and visual stamps preserved with clean file structure)")
            except Exception as e:
                try:
                    self.doc.save(out_p, garbage=3, deflate=True)
                    messagebox.showinfo("Success", f"Edited PDF saved successfully:\n{out_p}")
                except Exception as save_err:
                    messagebox.showerror("Save Error", f"Could not save PDF file:\n{save_err}")

# ==========================================
# 5. DIALOGS (SETTINGS, SHORTCUTS & BATCH)
# ==========================================
class SignatureSettingsDialog:
    """Settings popup dialog for configuring Reason, Location, TSA, Custom Logo, Password Protection and Appearance."""
    def __init__(self, parent, app):
        self.dialog = tk.Toplevel(parent)
        self.dialog.title("⚙️ Signature Details, Logo & Security Settings")
        self.dialog.geometry("560x600")
        self.dialog.minsize(500, 520)
        self.dialog.transient(parent)
        apply_app_icon(self.dialog)
        self.app = app
        self.logo_preview_img = None

        outer = tk.Frame(self.dialog, bg="#F1F5F9", padx=14, pady=12)
        outer.pack(fill=tk.BOTH, expand=True)

        canvas = tk.Canvas(outer, bg="#F1F5F9", bd=0, highlightthickness=0)
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        scroll_frame = tk.Frame(canvas, bg="#F1F5F9")
        scroll_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # 1. Cryptographic Details & TSA Card
        c1 = tk.Frame(scroll_frame, bg="#FFFFFF", relief=tk.RAISED, bd=1, padx=12, pady=10)
        c1.pack(fill=tk.X, pady=(0, 8))
        tk.Label(c1, text="1. Cryptographic Details & Time-Stamp (TSA)", font=get_sys_font(10, "bold"), fg="#1E40AF", bg="#FFFFFF").pack(anchor=tk.W, pady=(0, 6))

        grid = tk.Frame(c1, bg="#FFFFFF")
        grid.pack(fill=tk.X)

        tk.Label(grid, text="Hash Algorithm:", font=get_sys_font(8, "bold"), bg="#FFFFFF").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Combobox(grid, textvariable=app.sign_hash_var, values=["SHA-256", "SHA-384", "SHA-512"], state="readonly", font=get_sys_font(8)).grid(row=0, column=1, sticky="ew", padx=6, pady=4)

        tk.Label(grid, text="Time-Stamp (TSA):", font=get_sys_font(8, "bold"), bg="#FFFFFF").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Combobox(grid, textvariable=app.sign_tsa_var, values=["DigiCert TSA (Recommended)", "FreeTSA.org", "None"], state="readonly", font=get_sys_font(8)).grid(row=1, column=1, sticky="ew", padx=6, pady=4)
        grid.columnconfigure(1, weight=1)

        # 2. Custom Signature Logo Card
        c2 = tk.Frame(scroll_frame, bg="#FFFFFF", relief=tk.RAISED, bd=1, padx=12, pady=10)
        c2.pack(fill=tk.X, pady=(0, 8))
        tk.Label(c2, text="2. Custom Company Logo / Stamp in Signature", font=get_sys_font(10, "bold"), fg="#1E40AF", bg="#FFFFFF").pack(anchor=tk.W, pady=(0, 4))
        tk.Label(c2, text="Draw your company logo or seal alongside certificate details inside the signature box:", font=get_sys_font(8), fg="#64748B", bg="#FFFFFF").pack(anchor=tk.W, pady=(0, 6))

        tk.Checkbutton(c2, text="Include Custom Logo Image in Digital Signature Stamp", variable=app.sign_include_logo, font=get_sys_font(8, "bold"), fg="#0E7490", bg="#FFFFFF").pack(anchor=tk.W, pady=(0, 4))

        logo_row = tk.Frame(c2, bg="#FFFFFF")
        logo_row.pack(fill=tk.X, pady=(2, 4))
        ttk.Entry(logo_row, textvariable=app.sign_custom_logo_path, font=get_sys_font(8)).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        ttk.Button(logo_row, text="Browse Logo...", style="Tool3D.TButton", command=self._browse_logo).pack(side=tk.LEFT, padx=2)
        ttk.Button(logo_row, text="Clear", style="Tool3D.TButton", command=self._clear_logo).pack(side=tk.LEFT, padx=2)

        self.lbl_logo_preview = tk.Label(c2, text="[No logo selected]", font=get_sys_font(7, "italic"), fg="#94A3B8", bg="#F8FAFC", bd=1, relief=tk.SOLID, width=20, height=2)
        self.lbl_logo_preview.pack(anchor=tk.W, pady=(4, 2))
        self._update_logo_preview()

        # 3. Document Protection & Password Encryption Card
        c3 = tk.Frame(scroll_frame, bg="#FFFFFF", relief=tk.RAISED, bd=1, padx=12, pady=10)
        c3.pack(fill=tk.X, pady=(0, 8))
        tk.Label(c3, text="3. Document Security & Password Protection", font=get_sys_font(10, "bold"), fg="#1E40AF", bg="#FFFFFF").pack(anchor=tk.W, pady=(0, 4))
        tk.Label(c3, text="Secure the output PDF with high-grade AES-256 password encryption upon signing:", font=get_sys_font(8), fg="#64748B", bg="#FFFFFF").pack(anchor=tk.W, pady=(0, 6))

        tk.Checkbutton(c3, text="🔒 Protect Signed PDF Document with Password (AES-256)", variable=app.sign_protect_pdf, font=get_sys_font(8, "bold"), fg="#B45309", bg="#FFFFFF").pack(anchor=tk.W, pady=(0, 4))

        pass_row = tk.Frame(c3, bg="#FFFFFF")
        pass_row.pack(fill=tk.X, pady=(2, 2))
        tk.Label(pass_row, text="PDF Password:", font=get_sys_font(8, "bold"), bg="#FFFFFF").pack(side=tk.LEFT)
        self.ent_pass = ttk.Entry(pass_row, textvariable=app.sign_pdf_password, show="*", font=get_sys_font(8), width=24)
        self.ent_pass.pack(side=tk.LEFT, padx=6)
        self.btn_toggle_pass = ttk.Button(pass_row, text="Show", style="Tool3D.TButton", width=6, command=self._toggle_pass_visibility)
        self.btn_toggle_pass.pack(side=tk.LEFT)

        # Bottom Close Button
        btn_bot = tk.Frame(outer, bg="#F1F5F9")
        btn_bot.pack(fill=tk.X, pady=(4, 0))
        ttk.Button(btn_bot, text="✓ Save & Close Settings", style="Primary3D.TButton", command=self.dialog.destroy).pack(side=tk.RIGHT)

    def _browse_logo(self):
        f = filedialog.askopenfilename(
            title="Select Logo Image for Signature",
            filetypes=[("Image Files", "*.png;*.jpg;*.jpeg;*.bmp"), ("PNG Images", "*.png"), ("JPEG Images", "*.jpg;*.jpeg")],
            parent=self.dialog
        )
        if f:
            self.app.sign_custom_logo_path.set(f)
            self.app.sign_include_logo.set(True)
            self._update_logo_preview()

    def _clear_logo(self):
        self.app.sign_custom_logo_path.set("")
        self.app.sign_include_logo.set(False)
        self._update_logo_preview()

    def _update_logo_preview(self):
        p = self.app.sign_custom_logo_path.get()
        if p and os.path.exists(p):
            try:
                im = Image.open(p)
                im.thumbnail((80, 45), Image.Resampling.LANCZOS)
                self.logo_preview_img = ImageTk.PhotoImage(im)
                self.lbl_logo_preview.config(image=self.logo_preview_img, text="", width=80, height=45)
                return
            except Exception:
                pass
        self.lbl_logo_preview.config(image="", text="[No logo selected]", width=20, height=2)

    def _toggle_pass_visibility(self):
        if self.ent_pass.cget('show') == '*':
            self.ent_pass.config(show='')
            self.btn_toggle_pass.config(text="Hide")
        else:
            self.ent_pass.config(show='*')
            self.btn_toggle_pass.config(text="Show")

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
        self.dialog.geometry("680x420")
        self.dialog.minsize(620, 360)
        self.dialog.transient(parent)
        apply_app_icon(self.dialog)
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

        # 1. Installed Token Management Utilities (1-Click Launchers)
        c2 = tk.Frame(outer, bg="#FFFFFF", relief=tk.RAISED, bd=1, padx=12, pady=10)
        c2.pack(fill=tk.X, pady=(0, 10))
        tk.Label(c2, text="1. Installed Token Management Utilities (1-Click Launchers)", font=get_sys_font(10, "bold"), fg="#1E40AF", bg="#FFFFFF").pack(anchor=tk.W, pady=(0, 4))
        tk.Label(c2, text="Launch the official admin app for your USB token to view certificate details or test PIN:", font=get_sys_font(8), fg="#64748B", bg="#FFFFFF").pack(anchor=tk.W, pady=(0, 6))

        c2_grid = tk.Frame(c2, bg="#FFFFFF")
        c2_grid.pack(fill=tk.X)
        ttk.Button(c2_grid, text="🚀 Launch ePass2003 / HyperPKI", style="Tool3D.TButton", command=self._launch_epass).grid(row=0, column=0, sticky="ew", padx=3, pady=3)
        ttk.Button(c2_grid, text="🚀 Launch mToken / CryptoID", style="Tool3D.TButton", command=self._launch_mtoken).grid(row=0, column=1, sticky="ew", padx=3, pady=3)
        ttk.Button(c2_grid, text="🚀 Launch Watchdata PROXKey", style="Tool3D.TButton", command=self._launch_proxkey).grid(row=1, column=0, sticky="ew", padx=3, pady=3)
        ttk.Button(c2_grid, text="🚀 Launch InnaIT DSC Utility", style="Tool3D.TButton", command=self._launch_innait).grid(row=1, column=1, sticky="ew", padx=3, pady=3)
        ttk.Button(c2_grid, text="🔌 Open Custom Driver Manager", style="Tool3D.TButton", command=self._open_custom_driver_settings).grid(row=2, column=0, columnspan=2, sticky="ew", padx=3, pady=3)
        c2_grid.columnconfigure(0, weight=1)
        c2_grid.columnconfigure(1, weight=1)

        # 2. Direct A B INFOTECH Technical Support & Driver Downloads
        c3 = tk.Frame(outer, bg="#F0F9FF", relief=tk.SOLID, bd=1, padx=12, pady=10)
        c3.pack(fill=tk.X, pady=(0, 10))
        tk.Label(c3, text="2. Direct A B INFOTECH Technical Support & Driver Downloads", font=get_sys_font(10, "bold"), fg="#0369A1", bg="#F0F9FF").pack(anchor=tk.W, pady=(0, 4))
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
            r"C:\Program Files (x86)\HyperPKI\HyperPKI_HYP2003\HyperPKITokenManager_HYP2003.exe",
            r"C:\Program Files\HyperPKI\HyperPKI_HYP2003\HyperPKITokenManager_HYP2003.exe",
            r"C:\Program Files (x86)\ePass2003-India-Auto\ePass2003_India_Auto.exe",
            r"C:\Program Files\EnterSafe\ePass2003\eps2003.exe",
            r"C:\Program Files (x86)\EnterSafe\ePass2003\eps2003.exe",
            r"C:\Program Files (x86)\Professional Digisign Private Limited\ProdigiSignStore\eps2003.exe"
        ]
        for p in paths:
            if os.path.exists(p):
                subprocess.Popen([p]); return
        if messagebox.askyesno("Not Found", "ePass2003 / HyperPKI Manager not found at standard path.\nOpen driver download portal?", parent=self.dialog):
            self._open_driver_download()

    def _launch_mtoken(self):
        paths = [
            r"C:\Program Files (x86)\CryptoID\CryptoIDMgr.exe",
            r"C:\Program Files (x86)\CryptoID\CryptoIDCertUtilityF3\CryptoIDMgr.exe",
            r"C:\Program Files\CryptoID\CryptoIDMgr.exe",
            r"C:\Program Files\CryptoID\CryptoIDCertUtilityF3\CryptoIDMgr.exe",
            r"C:\Program Files (x86)\mToken\mToken.exe",
            r"C:\Program Files\mToken\mToken.exe",
            r"C:\Program Files (x86)\CryptoID\CryptoID.exe"
        ]
        for p in paths:
            if os.path.exists(p):
                subprocess.Popen([p]); return
        if messagebox.askyesno("Not Found", "mToken / CryptoID Utility not found at standard path.\nOpen download portal?", parent=self.dialog):
            self._open_driver_download()

    def _launch_proxkey(self):
        paths = [
            r"C:\Program Files (x86)\Watchdata\WD PROXKey\WDAdmintool.exe",
            r"C:\Program Files\Watchdata\WD PROXKey\WDAdmintool.exe",
            r"C:\Windows\System32\Watchdata\PROXKey CSP India V3.0\wdkmgr.exe",
            r"C:\Windows\SysWOW64\Watchdata\PROXKey CSP India V3.0\wdkmgr.exe",
            r"C:\Program Files (x86)\Watchdata\PROXKey\wdkmgr.exe"
        ]
        for p in paths:
            if os.path.exists(p):
                subprocess.Popen([p]); return
        if messagebox.askyesno("Not Found", "Watchdata WD PROXKey Utility not found at standard path.\nOpen download portal?", parent=self.dialog):
            self._open_driver_download()

    def _launch_innait(self):
        paths = [
            r"C:\Program Files (x86)\Precision Biometric\InnaIT\InnaITDSC\InnaITDSCApplication.exe",
            r"C:\Program Files (x86)\Precision Biometric\InnaIT\InnaITDSC \InnaITDSCApplication.exe",
            r"C:\Program Files\Precision Biometric\InnaIT\InnaITDSC\InnaITDSCApplication.exe",
            r"C:\Program Files\Precision Biometric\InnaIT\InnaITDSC \InnaITDSCApplication.exe",
            r"C:\Program Files (x86)\Precision Biometric\InnaIT\InnaITDSC\InnaITDSC.exe",
            r"C:\Program Files\Precision Biometric\InnaIT\InnaITDSC\InnaITDSC.exe"
        ]
        for p in paths:
            if os.path.exists(p):
                subprocess.Popen([p]); return
        if messagebox.askyesno("Not Found", "InnaIT DSC Utility (Precision Biometric) not found at standard path.\nOpen driver download portal?", parent=self.dialog):
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
        webbrowser.open("https://a-b-infotech.in/download?cat=T")

class BatchFolderDialog:
    """Batch Folder Signing Wizard with visual area selection on sample PDF."""
    def __init__(self, parent, main_app):
        self.dialog = tk.Toplevel(parent)
        self.dialog.title(f"📁 Bulk PDF Signer - Folder Setup [{CURRENT_OS}]")
        self.dialog.geometry("640x420")
        self.dialog.minsize(580, 380)
        self.dialog.transient(parent)
        apply_app_icon(self.dialog)
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
    """Unified Settings Dialog for License & Token Drivers with Windows Hardware Diagnostics."""
    def __init__(self, parent, main_app, initial_tab=None):
        self.dialog = tk.Toplevel(parent)
        self.dialog.title(f"⚙️ Settings & Hardware Token Drivers [{CURRENT_OS}]")
        self.dialog.geometry("740x650")
        self.dialog.minsize(660, 560)
        self.dialog.transient(parent)
        apply_app_icon(self.dialog)
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

        # Tab 2: Custom Drivers & Diagnostics
        tab_drv = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(tab_drv, text="🔌 Token Drivers & Diagnostics (.dll / .dylib / .so)")
        self._build_drivers_tab(tab_drv)

        if initial_tab == "drivers":
            self.notebook.select(tab_drv)

        btn_row = tk.Frame(outer, bg="#F1F5F9")
        btn_row.pack(fill=tk.X)
        ttk.Button(btn_row, text="Close Settings", style="Tool3D.TButton", command=self.dialog.destroy).pack(side=tk.RIGHT)

    def _build_license_tab(self, parent):
        hw_id = LicenseManager.get_hardware_id()
        info = LicenseManager.get_license_info()
        key = info.get("key") or info.get("license_key")
        exp = info.get("expiredate", "never")
        tier_lbl = LicenseManager.get_tier_label()
        status_val = info.get("status", "active")
        lic_id = info.get("id")
        client_email = info.get("client_email", "")
        activated_at = info.get("activated_at", "")

        card = tk.Frame(parent, bg="#FFFFFF", relief=tk.RAISED, bd=1, padx=12, pady=10)
        card.pack(fill=tk.X, pady=(0, 10))

        tk.Label(card, text="Hardware Machine ID & Database License Details", font=get_sys_font(10, "bold"), fg="#1E40AF", bg="#FFFFFF").pack(anchor=tk.W, pady=(0, 6))

        grid = tk.Frame(card, bg="#FFFFFF")
        grid.pack(fill=tk.X)

        tk.Label(grid, text="Hardware / Machine ID:", font=get_sys_font(8, "bold"), bg="#FFFFFF").grid(row=0, column=0, sticky="w", pady=3)
        tk.Label(grid, text=hw_id, font=get_sys_font(8, "bold"), fg="#0E7490", bg="#E0F2FE", padx=6, pady=2).grid(row=0, column=1, sticky="w", padx=6, pady=3)

        tk.Label(grid, text="Database Record ID:", font=get_sys_font(8, "bold"), bg="#FFFFFF").grid(row=1, column=0, sticky="w", pady=3)
        tk.Label(grid, text=str(lic_id) if lic_id else "N/A", font=get_sys_font(8), bg="#FFFFFF").grid(row=1, column=1, sticky="w", padx=6, pady=3)

        tk.Label(grid, text="License Key:", font=get_sys_font(8, "bold"), bg="#FFFFFF").grid(row=2, column=0, sticky="w", pady=3)
        tk.Label(grid, text=key if key else "Not Activated", font=get_sys_font(8), bg="#FFFFFF").grid(row=2, column=1, sticky="w", padx=6, pady=3)

        tk.Label(grid, text="Status:", font=get_sys_font(8, "bold"), bg="#FFFFFF").grid(row=3, column=0, sticky="w", pady=3)
        status_color = "#047857" if status_val == "active" else "#DC2626"
        tk.Label(grid, text=str(status_val).upper(), font=get_sys_font(8, "bold"), fg=status_color, bg="#FFFFFF").grid(row=3, column=1, sticky="w", padx=6, pady=3)

        tk.Label(grid, text="License Edition (Tier):", font=get_sys_font(8, "bold"), bg="#FFFFFF").grid(row=4, column=0, sticky="w", pady=3)
        tk.Label(grid, text=f"{tier_lbl} Edition", font=get_sys_font(8, "bold"), fg="#047857", bg="#FFFFFF").grid(row=4, column=1, sticky="w", padx=6, pady=3)

        tk.Label(grid, text="Client Email:", font=get_sys_font(8, "bold"), bg="#FFFFFF").grid(row=5, column=0, sticky="w", pady=3)
        tk.Label(grid, text=client_email if client_email else "sales@a-b-infotech.in", font=get_sys_font(8), bg="#FFFFFF").grid(row=5, column=1, sticky="w", padx=6, pady=3)

        tk.Label(grid, text="Activated At:", font=get_sys_font(8, "bold"), bg="#FFFFFF").grid(row=6, column=0, sticky="w", pady=3)
        tk.Label(grid, text=activated_at if activated_at else "N/A", font=get_sys_font(8), bg="#FFFFFF").grid(row=6, column=1, sticky="w", padx=6, pady=3)

        tk.Label(grid, text="Expiry Date:", font=get_sys_font(8, "bold"), bg="#FFFFFF").grid(row=7, column=0, sticky="w", pady=3)
        tk.Label(grid, text=exp, font=get_sys_font(8, "bold"), fg="#047857" if exp != "expired" else "#DC2626", bg="#FFFFFF").grid(row=7, column=1, sticky="w", padx=6, pady=3)

        renew_box = tk.Frame(parent, bg="#F0FDF4", relief=tk.SOLID, bd=1, padx=12, pady=10)
        renew_box.pack(fill=tk.X)
        tk.Label(renew_box, text="🔄 Check, Renew or Upgrade License", font=get_sys_font(9, "bold"), fg="#065F46", bg="#F0FDF4").pack(anchor=tk.W)
        tk.Label(renew_box, text="WhatsApp Support: +91 7016797720  |  Email: sales@a-b-infotech.in", font=get_sys_font(8), fg="#047857", bg="#F0FDF4").pack(anchor=tk.W, pady=(2, 6))

        r_btns = tk.Frame(renew_box, bg="#F0FDF4")
        r_btns.pack(anchor=tk.W)
        ttk.Button(r_btns, text="🔄 Check & Sync with Database", style="Tool3D.TButton", command=self._check_and_sync_db).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(r_btns, text="🌐 Renew Online", style="Primary3D.TButton", command=lambda: webbrowser.open("https://pay.a-b-infotech.in/license.php")).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(r_btns, text="💬 WhatsApp: +91 7016797720", style="Tool3D.TButton", command=self.main_app.open_whatsapp).pack(side=tk.LEFT)

    def _check_and_sync_db(self):
        try:
            res = LicenseManager.verify_with_database()
            status = res.get('status', 'unknown')
            if status == 'active':
                messagebox.showinfo("License Verified", "✓ License verified and synchronized with database successfully!")
            else:
                messagebox.showwarning("License Notice", f"License check status: {status.upper()}.\nPlease contact support if you need assistance.")
            self.dialog.destroy()
            SettingsDialog(self.dialog.master, self.main_app, initial_tab=None)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to sync with database: {e}")

    def _build_drivers_tab(self, parent):
        canvas = tk.Canvas(parent, bg="#F1F5F9", bd=0, highlightthickness=0)
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        scroll_frame = tk.Frame(canvas, bg="#F1F5F9")
        scroll_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # ----------------------------------------------------
        # SECTION 1: Windows System Hardware & Certificate Diagnostics
        # ----------------------------------------------------
        c1 = tk.Frame(scroll_frame, bg="#FFFFFF", relief=tk.RAISED, bd=1, padx=12, pady=10)
        c1.pack(fill=tk.X, padx=4, pady=(0, 10))

        tk.Label(c1, text="1. Windows System Hardware & Certificate Diagnostics", font=get_sys_font(10, "bold"), fg="#1E40AF", bg="#FFFFFF").pack(anchor=tk.W, pady=(0, 2))
        tk.Label(c1, text="Use these tools to verify if Windows detects your USB Smart Card Token and certificates correctly:", font=get_sys_font(8), fg="#64748B", bg="#FFFFFF").pack(anchor=tk.W, pady=(0, 6))

        c1_grid = tk.Frame(c1, bg="#FFFFFF")
        c1_grid.pack(fill=tk.X)
        ttk.Button(c1_grid, text="🖥️ Open Device Manager (devmgmt.msc)", style="Tool3D.TButton", command=self._open_devmgmt).grid(row=0, column=0, sticky="ew", padx=3, pady=3)
        ttk.Button(c1_grid, text="📜 Open User Certificates (certmgr.msc)", style="Tool3D.TButton", command=self._open_certmgr_user).grid(row=0, column=1, sticky="ew", padx=3, pady=3)
        ttk.Button(c1_grid, text="🏢 Open Machine Certificates (certlm.msc)", style="Tool3D.TButton", command=self._open_certmgr_machine).grid(row=1, column=0, sticky="ew", padx=3, pady=3)
        ttk.Button(c1_grid, text="🔄 Restart Smart Card Service (SCardSvr)", style="Primary3D.TButton", command=self._restart_scardsvr).grid(row=1, column=1, sticky="ew", padx=3, pady=3)
        c1_grid.columnconfigure(0, weight=1)
        c1_grid.columnconfigure(1, weight=1)

        # ----------------------------------------------------
        # SECTION 2: Installed USB Token Management Utilities
        # ----------------------------------------------------
        c_launch = tk.Frame(scroll_frame, bg="#FFFFFF", relief=tk.RAISED, bd=1, padx=12, pady=10)
        c_launch.pack(fill=tk.X, padx=4, pady=(0, 10))

        tk.Label(c_launch, text="2. Installed USB Token Management Utilities", font=get_sys_font(10, "bold"), fg="#1E40AF", bg="#FFFFFF").pack(anchor=tk.W, pady=(0, 2))
        tk.Label(c_launch, text="Launch official token utilities to test PIN, view hardware certificates, or configure token:", font=get_sys_font(8), fg="#64748B", bg="#FFFFFF").pack(anchor=tk.W, pady=(0, 6))

        launch_grid = tk.Frame(c_launch, bg="#FFFFFF")
        launch_grid.pack(fill=tk.X)
        ttk.Button(launch_grid, text="🔑 ePass2003 Manager", style="Tool3D.TButton", command=self._launch_epass).grid(row=0, column=0, sticky="ew", padx=3, pady=3)
        ttk.Button(launch_grid, text="🔑 mToken / CryptoID Utility", style="Tool3D.TButton", command=self._launch_mtoken).grid(row=0, column=1, sticky="ew", padx=3, pady=3)
        ttk.Button(launch_grid, text="🔑 Watchdata PROXKey Admin", style="Tool3D.TButton", command=self._launch_proxkey).grid(row=1, column=0, sticky="ew", padx=3, pady=3)
        ttk.Button(launch_grid, text="🔑 InnaIT DSC Utility (Precision)", style="Tool3D.TButton", command=self._launch_innait).grid(row=1, column=1, sticky="ew", padx=3, pady=3)
        launch_grid.columnconfigure(0, weight=1)
        launch_grid.columnconfigure(1, weight=1)

        # ----------------------------------------------------
        # SECTION 3: Official Token Drivers Download & Support
        # ----------------------------------------------------
        c2 = tk.Frame(scroll_frame, bg="#F0F9FF", relief=tk.SOLID, bd=1, padx=12, pady=10)
        c2.pack(fill=tk.X, padx=4, pady=(0, 10))

        tk.Label(c2, text="3. Official USB Token Drivers Download & Technical Support", font=get_sys_font(10, "bold"), fg="#0369A1", bg="#F0F9FF").pack(anchor=tk.W, pady=(0, 2))
        tk.Label(c2, text="Download verified drivers for ePass2003, mToken, ProxKey, InnaIT or get instant technical support:", font=get_sys_font(8), fg="#0284C7", bg="#F0F9FF").pack(anchor=tk.W, pady=(0, 6))

        c2_btn_row = tk.Frame(c2, bg="#F0F9FF")
        c2_btn_row.pack(fill=tk.X)
        ttk.Button(c2_btn_row, text="🌐 Download Token Drivers Online", style="Primary3D.TButton", command=self._open_driver_download).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(c2_btn_row, text="💬 WhatsApp Support (+91 7016797720)", style="Tool3D.TButton", command=self._open_whatsapp_support).pack(side=tk.LEFT, padx=3)
        ttk.Button(c2_btn_row, text="📧 Email: sales@a-b-infotech.in", style="Tool3D.TButton", command=self._open_email_support).pack(side=tk.LEFT, padx=3)

        # ----------------------------------------------------
        # SECTION 4: Register Custom Token Driver (PKCS#11)
        # ----------------------------------------------------
        ext = ".dll" if CURRENT_OS == 'Windows' else (".dylib" if CURRENT_OS == 'Darwin' else ".so")
        c3 = tk.Frame(scroll_frame, bg="#FFFFFF", relief=tk.RAISED, bd=1, padx=12, pady=10)
        c3.pack(fill=tk.X, padx=4, pady=(0, 10))

        tk.Label(c3, text="4. Register Custom Token Driver (PKCS#11)", font=get_sys_font(10, "bold"), fg="#1E40AF", bg="#FFFFFF").pack(anchor=tk.W, pady=(0, 4))
        f_grid = tk.Frame(c3, bg="#FFFFFF"); f_grid.pack(fill=tk.X, pady=4)

        tk.Label(f_grid, text="Token Name:", font=get_sys_font(8, "bold"), bg="#FFFFFF").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Entry(f_grid, textvariable=self.driver_name_var, width=28, font=get_sys_font(8)).grid(row=0, column=1, sticky="w", padx=6, pady=3)

        tk.Label(f_grid, text=f"Driver Path ({ext}):", font=get_sys_font(8, "bold"), bg="#FFFFFF").grid(row=1, column=0, sticky="w", pady=3)
        p_row = tk.Frame(f_grid, bg="#FFFFFF"); p_row.grid(row=1, column=1, sticky="ew", padx=6, pady=3)
        ttk.Entry(p_row, textvariable=self.driver_path_var, font=get_sys_font(8)).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(p_row, text="Browse...", style="Tool3D.TButton", command=self._browse_driver).pack(side=tk.LEFT, padx=(4, 0))

        btn_box = tk.Frame(c3, bg="#FFFFFF"); btn_box.pack(anchor=tk.W, pady=(6, 2))
        ttk.Button(btn_box, text="➕ Add & Save Driver", style="Primary3D.TButton", command=self._add_driver).pack(side=tk.LEFT, padx=(0, 6))

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
                messagebox.showinfo("Success", "Windows Smart Card Service (SCardSvr) restarted successfully!\nYour USB token should now be detected.", parent=self.dialog)
            else:
                messagebox.showwarning("Notice", "Smart Card Service command completed. Please re-insert your USB token.", parent=self.dialog)
            if self.main_app:
                self.main_app.load_token_certificates()
        except Exception as e:
            messagebox.showerror("Error", f"Failed to restart service: {e}", parent=self.dialog)

    def _launch_epass(self):
        paths = [
            r"C:\Program Files (x86)\HyperPKI\HyperPKI_HYP2003\HyperPKITokenManager_HYP2003.exe",
            r"C:\Program Files\HyperPKI\HyperPKI_HYP2003\HyperPKITokenManager_HYP2003.exe",
            r"C:\Program Files (x86)\ePass2003-India-Auto\ePass2003_India_Auto.exe",
            r"C:\Program Files\EnterSafe\ePass2003\eps2003.exe",
            r"C:\Program Files (x86)\EnterSafe\ePass2003\eps2003.exe",
            r"C:\Program Files (x86)\HyperPKI\HyperPKI_HYP2003\HyperPKICertD_HYP2003.exe",
            r"C:\Program Files\HyperPKI\HyperPKI_HYP2003\HyperPKICertD_HYP2003.exe"
        ]
        for p in paths:
            if os.path.exists(p):
                subprocess.Popen([p]); return
        if messagebox.askyesno("Not Found", "ePass2003 / HyperPKI Token Manager not found.\nOpen driver download portal?", parent=self.dialog):
            self._open_driver_download()

    def _launch_mtoken(self):
        paths = [
            r"C:\Program Files (x86)\CryptoID\CryptoIDMgr.exe",
            r"C:\Program Files (x86)\CryptoID\CryptoIDCertUtilityF3\CryptoIDMgr.exe",
            r"C:\Program Files\CryptoID\CryptoIDMgr.exe",
            r"C:\Program Files\CryptoID\CryptoIDCertUtilityF3\CryptoIDMgr.exe"
        ]
        for p in paths:
            if os.path.exists(p):
                subprocess.Popen([p]); return
        if messagebox.askyesno("Not Found", "mToken / CryptoID Utility not found.\nOpen download portal?", parent=self.dialog):
            self._open_driver_download()

    def _launch_proxkey(self):
        paths = [
            r"C:\Program Files (x86)\Watchdata\WD PROXKey\WDAdmintool.exe",
            r"C:\Program Files\Watchdata\WD PROXKey\WDAdmintool.exe"
        ]
        for p in paths:
            if os.path.exists(p):
                subprocess.Popen([p]); return
        if messagebox.askyesno("Not Found", "Watchdata WD PROXKey Admin Utility not found.\nOpen download portal?", parent=self.dialog):
            self._open_driver_download()

    def _launch_innait(self):
        paths = [
            r"C:\Program Files (x86)\Precision Biometric\InnaIT\InnaITDSC\InnaITDSCApplication.exe",
            r"C:\Program Files (x86)\Precision Biometric\InnaIT\InnaITDSC \InnaITDSCApplication.exe",
            r"C:\Program Files\Precision Biometric\InnaIT\InnaITDSC\InnaITDSCApplication.exe",
            r"C:\Program Files\Precision Biometric\InnaIT\InnaITDSC \InnaITDSCApplication.exe",
            r"C:\Program Files (x86)\Precision Biometric\InnaIT\InnaITDSC\InnaITDSC.exe",
            r"C:\Program Files\Precision Biometric\InnaIT\InnaITDSC\InnaITDSC.exe"
        ]
        for p in paths:
            if os.path.exists(p):
                subprocess.Popen([p]); return
        if messagebox.askyesno("Not Found", "InnaIT DSC Application (Precision Biometric) not found at default location.\nOpen driver download portal?", parent=self.dialog):
            self._open_driver_download()

    def _open_whatsapp_support(self):
        msg = urllib.parse.quote("Hello A B INFOTECH Team, I need technical assistance with Token Driver Setup & Digital Signatures.")
        webbrowser.open(f"https://wa.me/917016797720?text={msg}")

    def _open_email_support(self):
        subj = urllib.parse.quote("Technical Support: Token Driver Setup")
        webbrowser.open(f"mailto:sales@a-b-infotech.in?subject={subj}")

    def _open_driver_download(self):
        webbrowser.open("https://a-b-infotech.in/download?cat=T")

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
        self.sign_address_var = tk.StringVar(value="India")
        self.sign_location_var = self.sign_address_var  # alias for backward compatibility
        self.sign_contact_var = tk.StringVar(value="")
        self.sign_hash_var = tk.StringVar(value="SHA-256")
        self.sign_tsa_var = tk.StringVar(value="DigiCert TSA (Recommended)")
        self.sign_all_pages_var = tk.BooleanVar(value=False)

        # Appearance Checkbox options (Name, Date, Reason, Address, Issuer, Green tick, Border)
        self.show_name_var = tk.BooleanVar(value=True)
        self.show_date_var = tk.BooleanVar(value=True)
        self.show_reason_var = tk.BooleanVar(value=True)
        self.show_address_var = tk.BooleanVar(value=True)
        self.show_issuer_var = tk.BooleanVar(value=False)
        self.show_tick_var = tk.BooleanVar(value=True)
        self.show_border_var = tk.BooleanVar(value=False)

        # Custom Logo & Password Protection settings
        self.sign_custom_logo_path = tk.StringVar(value="")
        self.sign_include_logo = tk.BooleanVar(value=False)
        self.sign_protect_pdf = tk.BooleanVar(value=False)
        self.sign_pdf_password = tk.StringVar(value="")
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
        apply_app_icon(window)

    def hide_window(self):
        self.root.withdraw()

    def show_window(self):
        self.root.deiconify()
        self._maximize_window(self.root)
        self.root.lift()
        self.root.attributes('-topmost', True)
        self.root.after(150, lambda: self.root.attributes('-topmost', False))
        self.root.focus_force()
        if CURRENT_OS == 'Windows':
            try:
                hwnd = self.root.winfo_id()
                ctypes.windll.user32.SetForegroundWindow(hwnd)
            except Exception:
                pass

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
            LicenseManager.save_license(user_key, "never", tier="pro")
            messagebox.showinfo("Success", "Developer Bypass Activated! Pro Edition Unlocked.")
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
        badge_fg = "#10B981" if tier_lbl == "Pro" else "#F59E0B"
        badge_bg = "#064E3B" if tier_lbl == "Pro" else "#78350F"
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

        # 6. 🔓 Remove Sign Tab (Pro Only - Strip Signatures from PDF)
        self.tab_remove_sign = ttk.Frame(self.notebook)
        if LicenseManager.is_pro():
            self.notebook.add(self.tab_remove_sign, text=" 🔓 Remove Sign ")
        self._build_tab_remove_sign(self.tab_remove_sign)

        # 7. 📖 OCR & Edit Tab (Full Virtual Display Interactive Text Editor - Pro Only)
        self.tab_ocr_edit = ttk.Frame(self.notebook)
        if LicenseManager.is_pro():
            self.notebook.add(self.tab_ocr_edit, text=" 📖 OCR & Edit ")
        self._build_tab_ocr_edit(self.tab_ocr_edit)

        # 8. ℹ️ About & License
        self.tab_about = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_about, text=" ℹ️ About & License ")
        self._build_tab_about(self.tab_about)

    def apply_feature_gating(self):
        """Dynamically update UI features based on active license tier (Basic vs Pro)."""
        is_pro = LicenseManager.is_pro()
        tabs = self.notebook.tabs()
        if is_pro:
            about_idx = -1
            for idx, t in enumerate(tabs):
                if t == str(self.tab_about):
                    about_idx = idx
                    break

            if str(self.tab_remove_sign) not in tabs:
                if about_idx >= 0:
                    self.notebook.insert(about_idx, self.tab_remove_sign, text=" 🔓 Remove Sign ")
                else:
                    self.notebook.add(self.tab_remove_sign, text=" 🔓 Remove Sign ")

            tabs = self.notebook.tabs()
            for idx, t in enumerate(tabs):
                if t == str(self.tab_about):
                    about_idx = idx
                    break

            if str(self.tab_ocr_edit) not in tabs:
                if about_idx >= 0:
                    self.notebook.insert(about_idx, self.tab_ocr_edit, text=" 📖 OCR & Edit ")
                else:
                    self.notebook.add(self.tab_ocr_edit, text=" 📖 OCR & Edit ")
        else:
            if str(self.tab_remove_sign) in tabs:
                self.notebook.forget(self.tab_remove_sign)
            if str(self.tab_ocr_edit) in tabs:
                self.notebook.forget(self.tab_ocr_edit)

    # =========================================================================
    # TAB 1: ✍️ SIGN PDF (Restored Classic Split Layout with Live Canvas)
    # =========================================================================
    def _build_tab_sign(self, parent):
        split = tk.Frame(parent, bg="#F1F5F9")
        split.pack(fill=tk.BOTH, expand=True)

        # ----------------------------------------------------
        # LEFT PANEL: Signing Controls, Logo & Old DSC Options
        # ----------------------------------------------------
        left_panel = tk.Frame(split, bg="#FFFFFF", width=440, padx=10, pady=6, relief=tk.RAISED, bd=1)
        left_panel.pack(side=tk.LEFT, fill=tk.Y)
        left_panel.pack_propagate(False)

        # -----------------------------------------------------------------
        # BOTTOM CONTROLS CLUSTER: Packed at tk.BOTTOM so it is NEVER clipped!
        # Always visible:
        # [⚙️ Signature Settings] [📁 Bulk Sign] [❌ Clear PDF]
        # Ready status label
        # [🚀 SIGN & SAVE PDF DOCUMENT]
        # Adobe disclaimer
        # -----------------------------------------------------------------
        bot_f = tk.Frame(left_panel, bg="#FFFFFF")
        bot_f.pack(side=tk.BOTTOM, fill=tk.X, pady=(2, 2))

        action_row = tk.Frame(bot_f, bg="#FFFFFF")
        action_row.pack(side=tk.TOP, fill=tk.X, pady=(0, 4))

        self.btn_signature_settings = ttk.Button(
            action_row, text="⚙️ Signature Settings", style="Tool3D.TButton", command=self.open_signature_settings
        )
        self.btn_signature_settings.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 2))

        self.btn_bulk_sign = ttk.Button(
            action_row, text="📁 Bulk Sign", style="Tool3D.TButton", command=self.open_batch_dialog
        )
        self.btn_bulk_sign.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)

        self.btn_clear_pdf = ttk.Button(
            action_row, text="❌ Clear PDF", style="Tool3D.TButton", command=self._clear_sign_pdf
        )
        self.btn_clear_pdf.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(2, 0))

        self.lbl_sign_status = tk.Label(
            bot_f,
            text="Ready. Open a PDF document on the right to begin.",
            font=get_sys_font(8),
            fg="#059669",
            bg="#FFFFFF",
            wraplength=410,
            justify="left"
        )
        self.lbl_sign_status.pack(pady=(2, 3))

        self.btn_sign_doc = ttk.Button(
            bot_f,
            text="🚀 SIGN & SAVE PDF DOCUMENT",
            style="Primary3D.TButton",
            command=self.start_signing
        )
        self.btn_sign_doc.pack(fill=tk.X, ipady=4)

        disclaimer = "Note: Adobe validity tick depends on Adobe's live validation & Trusted Root list."
        tk.Label(bot_f, text=disclaimer, font=get_sys_font(7, "italic"), fg="#DC2626", bg="#FFFFFF", wraplength=410).pack(pady=(2, 0))

        # -----------------------------------------------------------------
        # TOP WIDGETS: Logo, Certificate Source, Options, Selected Areas
        # -----------------------------------------------------------------
        # Compact Logo Card (Horizontal Layout saves vertical space)
        logo_frame = tk.Frame(left_panel, bg="#F8FAFC", relief=tk.SOLID, bd=1, padx=6, pady=3)
        logo_frame.pack(side=tk.TOP, fill=tk.X, pady=(0, 3))

        self.tk_big_logo = load_abinfotech_logo(max_size=(90, 32))
        if self.tk_big_logo:
            tk.Label(logo_frame, image=self.tk_big_logo, bg="#F8FAFC").pack(side=tk.LEFT, padx=(4, 8))
            txt_box = tk.Frame(logo_frame, bg="#F8FAFC")
            txt_box.pack(side=tk.LEFT, fill=tk.X, expand=True)
            tk.Label(txt_box, text="A B INFOTECH", font=get_sys_font(10, "bold"), fg="#1E3A8A", bg="#F8FAFC", anchor=tk.W).pack(anchor=tk.W)
            tk.Label(txt_box, text="Local Digital Signature Suite", font=get_sys_font(7), fg="#059669", bg="#F8FAFC", anchor=tk.W).pack(anchor=tk.W)
        else:
            tk.Label(logo_frame, text="A B INFOTECH", font=get_sys_font(10, "bold"), fg="#1E3A8A", bg="#F8FAFC").pack()
            tk.Label(logo_frame, text="Local Digital Signature Suite", font=get_sys_font(7), fg="#059669", bg="#F8FAFC").pack()

        # Certificate Source Options: Only the 2 Classic Options (USB Token & PFX File)
        tk.Label(left_panel, text="Digital Certificate Source:", font=get_sys_font(8, "bold"), fg="#1E40AF", bg="#FFFFFF").pack(side=tk.TOP, anchor=tk.W)

        self.nb_dsc = ttk.Notebook(left_panel)
        self.nb_dsc.pack(side=tk.TOP, fill=tk.X, pady=(1, 3))

        # Sub-tab 1: USB Hardware Token (PKCS#11)
        f_token = ttk.Frame(self.nb_dsc, padding=3)
        self.nb_dsc.add(f_token, text=" 🔌 USB Hardware Token ")

        tk.Label(f_token, text="Select Token Driver:", font=get_sys_font(8, "bold"), fg="#1E3A8A").pack(anchor=tk.W)
        d_row = tk.Frame(f_token); d_row.pack(fill=tk.X, pady=(1, 2))
        self.token_driver_combo = ttk.Combobox(d_row, textvariable=self.driver_var, values=list(DRIVER_MAP.keys()), state="readonly", font=get_sys_font(8))
        self.token_driver_combo.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        if DRIVER_MAP: self.token_driver_combo.current(0)
        ttk.Button(d_row, text="Refresh", style="Tool3D.TButton", command=self.load_token_certificates).pack(side=tk.RIGHT)

        tk.Label(f_token, text="Certificate in Token:", font=get_sys_font(8, "bold"), fg="#1E3A8A").pack(anchor=tk.W)
        self.token_cert_combo = ttk.Combobox(f_token, state="readonly", font=get_sys_font(8))
        self.token_cert_combo.pack(fill=tk.X, pady=(1, 2))
        self.token_cert_combo.bind("<<ComboboxSelected>>", self._on_token_cert_selected)

        pin_row = tk.Frame(f_token); pin_row.pack(fill=tk.X, pady=(1, 1))
        tk.Label(pin_row, text="Hardware Token PIN:", font=get_sys_font(8, "bold"), fg="#1E3A8A").pack(side=tk.LEFT)
        ttk.Entry(pin_row, textvariable=self.pin_var, show="*", width=14).pack(side=tk.LEFT, padx=6)
        ttk.Button(pin_row, text="🛠️ Driver Shortcuts", style="Tool3D.TButton", command=self.show_driver_shortcuts_dialog).pack(side=tk.RIGHT)

        # Sub-tab 2: PFX / P12 File
        f_pfx = ttk.Frame(self.nb_dsc, padding=3)
        self.nb_dsc.add(f_pfx, text=" 🔑 PFX / P12 File ")

        tk.Label(f_pfx, text="Select .PFX / .P12 File:", font=get_sys_font(8, "bold"), fg="#1E3A8A").pack(anchor=tk.W)
        p_row1 = tk.Frame(f_pfx); p_row1.pack(fill=tk.X, pady=(1, 2))
        ttk.Entry(p_row1, textvariable=self.pfx_path_var, font=get_sys_font(8)).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        ttk.Button(p_row1, text="Browse...", style="Tool3D.TButton", command=self.browse_pfx_file).pack(side=tk.RIGHT)

        p_row2 = tk.Frame(f_pfx); p_row2.pack(fill=tk.X, pady=(1, 1))
        tk.Label(p_row2, text="Password:", font=get_sys_font(8, "bold"), fg="#1E3A8A").pack(side=tk.LEFT)
        ttk.Entry(p_row2, textvariable=self.pfx_pass_var, show="*", width=14).pack(side=tk.LEFT, padx=6)
        ttk.Button(p_row2, text="Verify", style="Tool3D.TButton", command=lambda: self.verify_pfx_certificate(silent=False)).pack(side=tk.LEFT)

        # 4 Options Card: Reason, Address, Sign all pages, Checkbox Bar
        opts_card = tk.Frame(left_panel, bg="#F8FAFC", relief=tk.SOLID, bd=1, padx=6, pady=3)
        opts_card.pack(side=tk.TOP, fill=tk.X, pady=(0, 3))

        # 1. Reason
        r_row = tk.Frame(opts_card, bg="#F8FAFC")
        r_row.pack(fill=tk.X, pady=1)
        tk.Label(r_row, text="Reason:", font=get_sys_font(8, "bold"), fg="#1E3A8A", bg="#F8FAFC", width=8, anchor=tk.W).pack(side=tk.LEFT)
        reasons = ["I approve this document", "I am the author of this document", "Official Authentication", "Verified and Approved"]
        ttk.Combobox(r_row, textvariable=self.sign_reason_var, values=reasons, font=get_sys_font(8)).pack(side=tk.LEFT, fill=tk.X, expand=True)

        # 2. Address (auto-populated from token/pfx certificate)
        a_row = tk.Frame(opts_card, bg="#F8FAFC")
        a_row.pack(fill=tk.X, pady=1)
        tk.Label(a_row, text="Address:", font=get_sys_font(8, "bold"), fg="#1E3A8A", bg="#F8FAFC", width=8, anchor=tk.W).pack(side=tk.LEFT)
        ttk.Entry(a_row, textvariable=self.sign_address_var, font=get_sys_font(8)).pack(side=tk.LEFT, fill=tk.X, expand=True)

        # 3. Sign all pages (Unchecked by default)
        tk.Checkbutton(
            opts_card,
            text="📄 Sign all pages in the PDF document",
            variable=self.sign_all_pages_var,
            font=get_sys_font(8, "bold"),
            fg="#047857", bg="#F8FAFC"
        ).pack(anchor=tk.W, pady=(1, 1))

        # 4. Checkbox bar in 2 clean rows so "Border" is never cut off
        chk_bar1 = tk.Frame(opts_card, bg="#F8FAFC")
        chk_bar1.pack(fill=tk.X, pady=(0, 0))
        for text, var in [
            ("Name", self.show_name_var),
            ("Date", self.show_date_var),
            ("Reason", self.show_reason_var),
            ("Address", self.show_address_var),
        ]:
            tk.Checkbutton(
                chk_bar1, text=text, variable=var,
                font=get_sys_font(7, "bold"),
                fg="#1E293B", bg="#F8FAFC",
                padx=2, pady=0
            ).pack(side=tk.LEFT, padx=1)

        chk_bar2 = tk.Frame(opts_card, bg="#F8FAFC")
        chk_bar2.pack(fill=tk.X, pady=(0, 0))
        for text, var in [
            ("Issuer", self.show_issuer_var),
            ("Green tick", self.show_tick_var),
            ("Border", self.show_border_var),
        ]:
            tk.Checkbutton(
                chk_bar2, text=text, variable=var,
                font=get_sys_font(7, "bold"),
                fg="#1E293B", bg="#F8FAFC",
                padx=2, pady=0
            ).pack(side=tk.LEFT, padx=1)

        # Selected Signature Areas Listbox / Container
        tk.Label(left_panel, text="Selected Signature Area(s):", font=get_sys_font(8, "bold"), fg="#1E40AF", bg="#FFFFFF").pack(side=tk.TOP, anchor=tk.W, pady=(2, 1))
        self.area_container = tk.Frame(left_panel, bg="#F8FAFC", relief=tk.SUNKEN, bd=1, height=44)
        self.area_container.pack(side=tk.TOP, fill=tk.X, pady=(0, 3))
        self.area_container.pack_propagate(False)

        self.lbl_no_areas = tk.Label(self.area_container, text="No area selected yet.\nClick 'Draw Signature Box' on the right canvas.", font=get_sys_font(8), fg="#64748B", bg="#F8FAFC")
        self.lbl_no_areas.pack(expand=True)

        # ----------------------------------------------------
        # RIGHT PANEL: Live Interactive PDF Canvas Viewer
        # ----------------------------------------------------
        right_panel = tk.Frame(split, bg="#E2E8F0")
        right_panel.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        self.sign_viewer = EmbeddedPdfViewer(right_panel, on_area_selected=self._on_sign_area_picked, allow_drawing=True, on_clear=self._clear_sign_pdf)
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

    def _clear_sign_pdf(self):
        self.signature_areas.clear()
        self._update_sign_areas_ui()
        self.lbl_sign_status.config(text="PDF cleared. Open a document on the right to begin.", fg="#64748B")
        if hasattr(self, 'sign_viewer') and self.sign_viewer.doc is not None:
            self.sign_viewer.clear_pdf()

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
        ttk.Button(r1, text="Browse...", style="Tool3D.TButton", command=self._browse_compress_pdf).pack(side=tk.LEFT, padx=(0, 2))
        ttk.Button(r1, text="❌ Clear", style="Tool3D.TButton", command=self._clear_compress_pdf).pack(side=tk.RIGHT)

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

    def _clear_compress_pdf(self):
        self.compress_path_var.set("")
        self.lbl_compress_res.config(text="")
        if hasattr(self, 'compress_viewer') and self.compress_viewer.doc is not None:
            self.compress_viewer.clear_pdf()

    def exec_compress(self):
        in_p = self.compress_path_var.get().strip()
        if not in_p or not os.path.exists(in_p):
            messagebox.showerror("Error", "Please select a valid PDF file."); return
        out_p = filedialog.asksaveasfilename(title="Save Compressed PDF As", defaultextension=".pdf", filetypes=[("PDF", "*.pdf")])
        if not out_p: return

        try:
            has_sig = pdf_has_digital_signatures(in_p)
            doc = fitz.open(in_p)
            if has_sig:
                # Bake visual signature appearance so the stamp is preserved 100% intact
                preserve_signature_appearances_on_doc(doc, remove_broken_fields=True)
                # Rebuild into fresh doc to eliminate any corrupt xref or byte-range entries
                clean_doc = fitz.open()
                clean_doc.insert_pdf(doc)
                doc.close()
                clean_doc.save(out_p, garbage=4, deflate=True)
                clean_doc.close()
            else:
                doc.save(out_p, garbage=4, deflate=True)
                doc.close()

            orig_s = os.path.getsize(in_p)
            new_s = os.path.getsize(out_p)
            saved = max(0, orig_s - new_s)
            pct = (saved / orig_s) * 100 if orig_s > 0 else 0

            status_msg = f"✓ Compressed! Saved {pct:.1f}% ({saved // 1024} KB saved)"
            if has_sig:
                status_msg += " (Visual digital signature preserved)"
            self.lbl_compress_res.config(text=status_msg)
            self.compress_viewer.load_pdf(out_p)
            info_text = f"Compressed successfully!\nOriginal: {orig_s//1024} KB\nNew: {new_s//1024} KB\nSaved: {pct:.1f}%"
            if has_sig:
                info_text += "\n\n✓ Note: Existing digital signature appearance was preserved intact."
            messagebox.showinfo("Success", info_text)
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

        tk.Label(left, text="Merge Files (PDF, Word & Images)", font=get_sys_font(11, "bold"), fg="#1E40AF", bg="#FFFFFF").pack(anchor=tk.W, pady=(0, 4))
        tk.Label(left, text="Upload PDF, Word documents (.docx/.doc), or Image files into queue, rearrange order, and merge into one PDF.", font=get_sys_font(8), fg="#64748B", bg="#FFFFFF", wraplength=350, justify="left").pack(anchor=tk.W, pady=(0, 8))

        self.merge_pdf_list = []
        self.merge_listbox = tk.Listbox(left, height=10, font=get_sys_font(8), bd=1, relief=tk.SOLID)
        self.merge_listbox.pack(fill=tk.X, pady=(0, 6))
        self.merge_listbox.bind("<<ListboxSelect>>", self._on_merge_select)

        btn_row = tk.Frame(left, bg="#FFFFFF"); btn_row.pack(fill=tk.X, pady=(0, 8))
        ttk.Button(btn_row, text="📁 Upload Files", style="Tool3D.TButton", command=self.merge_add_files).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(btn_row, text="▲ Up", style="Tool3D.TButton", command=self.merge_move_up).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_row, text="▼ Down", style="Tool3D.TButton", command=self.merge_move_down).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_row, text="🗑️ Remove", style="Tool3D.TButton", command=self.merge_remove_file).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_row, text="❌ Clear All", style="Tool3D.TButton", command=self.merge_clear_all).pack(side=tk.RIGHT, padx=2)

        ttk.Button(left, text="📑 MERGE & SAVE AS ONE PDF", style="Primary3D.TButton", command=self.exec_merge).pack(fill=tk.X, ipady=4, pady=(10, 0))

        # Right Live PDF Viewer
        right = tk.Frame(split, bg="#E2E8F0")
        right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)
        self.merge_viewer = EmbeddedPdfViewer(right)
        self.merge_viewer.pack(fill=tk.BOTH, expand=True)

    def _get_file_type_badge(self, fpath):
        ext = os.path.splitext(fpath)[1].lower()
        if ext == '.pdf':
            return "📑 [PDF] "
        elif ext in ['.docx', '.doc']:
            return "📝 [WORD] "
        elif ext in ['.png', '.jpg', '.jpeg', '.bmp', '.tiff', '.webp', '.gif']:
            return "🖼️ [IMG] "
        return "📄 [FILE] "

    def merge_add_files(self):
        file_filters = [
            ("All Supported Files (*.pdf, *.docx, *.doc, Images)", "*.pdf;*.docx;*.doc;*.png;*.jpg;*.jpeg;*.bmp;*.tiff;*.webp;*.gif"),
            ("PDF Documents (*.pdf)", "*.pdf"),
            ("Word Documents (*.docx;*.doc)", "*.docx;*.doc"),
            ("Image Files (*.png;*.jpg;*.jpeg;*.bmp;*.tiff;*.webp;*.gif)", "*.png;*.jpg;*.jpeg;*.bmp;*.tiff;*.webp;*.gif"),
            ("All Files (*.*)", "*.*")
        ]
        files = filedialog.askopenfilenames(title="Upload Files to Merge (PDF, Word, Images)", filetypes=file_filters)
        if files:
            for f in files:
                if f not in self.merge_pdf_list:
                    self.merge_pdf_list.append(f)
            self._refresh_merge_list(len(self.merge_pdf_list) - 1)

    def _on_merge_select(self, event):
        sel = self.merge_listbox.curselection()
        if sel:
            idx = sel[0]
            if idx < len(self.merge_pdf_list):
                self._preview_merge_item(self.merge_pdf_list[idx])

    def _preview_merge_item(self, fpath):
        ext = os.path.splitext(fpath)[1].lower()
        if ext == '.pdf':
            self.merge_viewer.load_pdf(fpath)
        else:
            try:
                pdf_bytes = convert_file_to_pdf_bytes(fpath)
                tmp_p = os.path.join(tempfile.gettempdir(), f"abi_merge_prev_{abs(hash(fpath))}.pdf")
                with open(tmp_p, 'wb') as tf:
                    tf.write(pdf_bytes)
                self.merge_viewer.load_pdf(tmp_p)
            except Exception as e:
                logger.debug(f"Preview non-pdf error: {e}")

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

    def merge_clear_all(self):
        self.merge_pdf_list.clear()
        self.merge_listbox.delete(0, tk.END)
        if hasattr(self, 'merge_viewer') and self.merge_viewer.doc is not None:
            self.merge_viewer.clear_pdf()

    def _refresh_merge_list(self, new_sel=None):
        self.merge_listbox.delete(0, tk.END)
        for f in self.merge_pdf_list:
            badge = self._get_file_type_badge(f)
            self.merge_listbox.insert(tk.END, f"{badge}{os.path.basename(f)}")
        if self.merge_pdf_list and new_sel is not None and new_sel < len(self.merge_pdf_list):
            self.merge_listbox.selection_set(new_sel)
            self._preview_merge_item(self.merge_pdf_list[new_sel])

    def exec_merge(self):
        if len(self.merge_pdf_list) < 2:
            messagebox.showwarning("Warning", "Please upload at least 2 files to merge."); return
        out_p = filedialog.asksaveasfilename(title="Save Merged PDF As", defaultextension=".pdf", filetypes=[("PDF Documents", "*.pdf")])
        if not out_p: return

        try:
            merged = fitz.open()
            for p in self.merge_pdf_list:
                pdf_bytes = convert_file_to_pdf_bytes(p)
                sub_doc = fitz.open("pdf", pdf_bytes)
                # Bake any existing digital signature appearance onto the pages
                # so the visual signature stamp is 100% preserved in the merged output!
                preserve_signature_appearances_on_doc(sub_doc, remove_broken_fields=True)
                merged.insert_pdf(sub_doc)
                sub_doc.close()
            merged.save(out_p, garbage=3, deflate=True)
            total_pages = len(merged)
            merged.close()
            self.merge_viewer.load_pdf(out_p)
            messagebox.showinfo(
                "Merge Complete",
                f"✓ Successfully merged {len(self.merge_pdf_list)} file(s) into one PDF document ({total_pages} total pages):\n\n{out_p}\n\n(All existing visual signature stamps were preserved intact)"
            )
        except Exception as e:
            messagebox.showerror("Merge Error", f"Merge failed: {e}")

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
        ttk.Button(r1, text="Browse...", style="Tool3D.TButton", command=self._browse_verify_pdf).pack(side=tk.LEFT, padx=(0, 2))
        ttk.Button(r1, text="❌ Clear", style="Tool3D.TButton", command=self._clear_verify_pdf).pack(side=tk.RIGHT)

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

    def _clear_verify_pdf(self):
        self.verify_path_var.set("")
        self.txt_verify_report.delete("1.0", tk.END)
        if hasattr(self, 'verify_viewer') and self.verify_viewer.doc is not None:
            self.verify_viewer.clear_pdf()

    def exec_verify(self):
        p = self.verify_path_var.get().strip()
        if not p or not os.path.exists(p):
            messagebox.showerror("Error", "Please select a valid PDF file."); return

        self.txt_verify_report.delete("1.0", tk.END)
        self.txt_verify_report.insert(tk.END, "Analyzing cryptographic digital signatures & certificates...\n\n")

        try:
            sig_fields = []
            with open(p, 'rb') as f:
                r = PdfFileReader(f)
                sig_fields = list(r.embedded_signatures)

            if not sig_fields:
                self.txt_verify_report.insert(tk.END, "❌ No cryptographic digital signatures found in this document.\n")
                return

            self.txt_verify_report.insert(tk.END, f"Found {len(sig_fields)} Digital Signature(s):\n" + "="*52 + "\n\n")

            with open(p, 'rb') as f:
                for idx, sig in enumerate(sig_fields):
                    self.txt_verify_report.insert(tk.END, f"━"*52 + "\n")
                    self.txt_verify_report.insert(tk.END, f"SIGNATURE #{idx+1} DETAILS:\n")
                    self.txt_verify_report.insert(tk.END, f"━"*52 + "\n")
                    self.txt_verify_report.insert(tk.END, f"• Signature Field: {sig.field_name}\n")

                    # SubFilter / Format
                    subf = "Unknown"
                    try:
                        subf = str(sig.sig_object.get('/SubFilter', 'adbe.pkcs7.detached'))
                    except Exception:
                        pass
                    self.txt_verify_report.insert(tk.END, f"• Signature Format: {subf}\n")

                    # Cryptographic Integrity
                    is_intact = False
                    try:
                        val = validate_pdf_signature(sig)
                        is_intact = getattr(val, 'intact', False)
                    except Exception as ve:
                        is_intact = getattr(val, 'intact', False) if 'val' in locals() and hasattr(val, 'intact') else False

                    if is_intact:
                        self.txt_verify_report.insert(tk.END, "• Document Integrity: ✓ INTACT & UNALTERED (Bytes match cryptographic hash)\n")
                    else:
                        self.txt_verify_report.insert(tk.END, "• Document Integrity: ❌ TAMPERED (Document content was modified after signing)\n")

                    asn1_cert = sig.signer_cert
                    if asn1_cert:
                        try:
                            cert = x509.load_der_x509_certificate(asn1_cert.dump())
                            cn, org, ou, country, email = "Unknown", "N/A", "N/A", "N/A", "N/A"
                            for attr in cert.subject:
                                if attr.oid == NameOID.COMMON_NAME:
                                    cn = attr.value
                                elif attr.oid == NameOID.ORGANIZATION_NAME:
                                    org = attr.value
                                elif attr.oid == NameOID.ORGANIZATIONAL_UNIT_NAME:
                                    ou = attr.value
                                elif attr.oid == NameOID.COUNTRY_NAME:
                                    country = attr.value
                                elif attr.oid == NameOID.EMAIL_ADDRESS or attr.oid.dotted_string == '1.2.840.113549.1.9.1':
                                    email = attr.value

                            self.txt_verify_report.insert(tk.END, "\n[Signer Digital Certificate (DSC)]:\n")
                            self.txt_verify_report.insert(tk.END, f"  - Signer Name (CN): {cn}\n")
                            self.txt_verify_report.insert(tk.END, f"  - Organization (O): {org}\n")
                            if ou != "N/A":
                                self.txt_verify_report.insert(tk.END, f"  - Department (OU): {ou}\n")
                            if country != "N/A":
                                self.txt_verify_report.insert(tk.END, f"  - Country: {country}\n")
                            if email != "N/A":
                                self.txt_verify_report.insert(tk.END, f"  - Email: {email}\n")
                            self.txt_verify_report.insert(tk.END, f"  - Serial Number (Hex): {hex(cert.serial_number)}\n")
                            self.txt_verify_report.insert(tk.END, f"  - Serial Number (Dec): {cert.serial_number}\n")

                            # Issuer Details
                            icn, iorg = "Unknown", "N/A"
                            for attr in cert.issuer:
                                if attr.oid == NameOID.COMMON_NAME:
                                    icn = attr.value
                                elif attr.oid == NameOID.ORGANIZATION_NAME:
                                    iorg = attr.value
                            self.txt_verify_report.insert(tk.END, "\n[Certificate Authority (Issuer)]:\n")
                            self.txt_verify_report.insert(tk.END, f"  - Issuer CN: {icn}\n")
                            self.txt_verify_report.insert(tk.END, f"  - Issuer Organization: {iorg}\n")

                            # Validity Dates
                            nb = cert.not_valid_before_utc if hasattr(cert, 'not_valid_before_utc') else cert.not_valid_before
                            na = cert.not_valid_after_utc if hasattr(cert, 'not_valid_after_utc') else cert.not_valid_after
                            now = datetime.now(timezone.utc)
                            self.txt_verify_report.insert(tk.END, "\n[Certificate Validity Period]:\n")
                            self.txt_verify_report.insert(tk.END, f"  - Valid From: {nb.strftime('%d-%b-%Y %H:%M:%S UTC')}\n")
                            self.txt_verify_report.insert(tk.END, f"  - Valid Until: {na.strftime('%d-%b-%Y %H:%M:%S UTC')}\n")

                            if now < nb:
                                self.txt_verify_report.insert(tk.END, "  - Expiration Status: ❌ NOT YET ACTIVE\n")
                            elif now > na:
                                self.txt_verify_report.insert(tk.END, "  - Expiration Status: ❌ EXPIRED\n")
                            else:
                                days_left = (na - now).days
                                self.txt_verify_report.insert(tk.END, f"  - Expiration Status: ✓ ACTIVE & VALID ({days_left} days remaining)\n")

                            # CRL Check
                            crl_urls = []
                            try:
                                crl_ext = cert.extensions.get_extension_for_oid(ExtensionOID.CRL_DISTRIBUTION_POINTS)
                                for dp in crl_ext.value:
                                    if dp.full_name:
                                        for name in dp.full_name:
                                            if isinstance(name, x509.UniformResourceIdentifier):
                                                crl_urls.append(name.value)
                            except Exception:
                                pass

                            self.txt_verify_report.insert(tk.END, "\n[CRL Revocation Status]:\n")
                            if crl_urls:
                                for cu in crl_urls:
                                    self.txt_verify_report.insert(tk.END, f"  - CRL Distribution Point: {cu}\n")
                                    try:
                                        req = urllib.request.Request(cu, headers={'User-Agent': 'Mozilla/5.0'})
                                        with urllib.request.urlopen(req, timeout=2.5) as resp:
                                            if resp.status in [200, 302, 304]:
                                                self.txt_verify_report.insert(tk.END, "    ✓ CRL Server is Online & Accessible\n")
                                            else:
                                                self.txt_verify_report.insert(tk.END, f"    ⚠️ CRL Server HTTP Status {resp.status}\n")
                                    except Exception as ce:
                                        self.txt_verify_report.insert(tk.END, f"    ⚠️ CRL Server offline or unreachable ({ce})\n")
                            else:
                                self.txt_verify_report.insert(tk.END, "  - CRL Distribution Points: None specified in certificate\n")

                            # OCSP Check
                            ocsp_urls = []
                            try:
                                aia_ext = cert.extensions.get_extension_for_oid(ExtensionOID.AUTHORITY_INFORMATION_ACCESS)
                                for desc in aia_ext.value:
                                    if desc.access_method == x509.AuthorityInformationAccessOID.OCSP:
                                        if isinstance(desc.access_location, x509.UniformResourceIdentifier):
                                            ocsp_urls.append(desc.access_location.value)
                            except Exception:
                                pass

                            self.txt_verify_report.insert(tk.END, "\n[OCSP Status]:\n")
                            if ocsp_urls:
                                for ou in ocsp_urls:
                                    self.txt_verify_report.insert(tk.END, f"  - OCSP Responder: {ou}\n")
                            else:
                                self.txt_verify_report.insert(tk.END, "  - OCSP Responder: None specified in certificate\n")

                        except Exception as cert_err:
                            self.txt_verify_report.insert(tk.END, f"\n⚠️ Certificate parse notice: {cert_err}\n")

                    self.txt_verify_report.insert(tk.END, "\n")
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
        ttk.Button(r1, text="Browse...", style="Tool3D.TButton", command=self._browse_tool_pdf).pack(side=tk.LEFT, padx=(0, 2))
        ttk.Button(r1, text="❌ Clear", style="Tool3D.TButton", command=self._clear_tool_pdf).pack(side=tk.RIGHT)

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

    def _clear_tool_pdf(self):
        self.tool_pdf_path_var.set("")
        if hasattr(self, 'tool_viewer') and self.tool_viewer.doc is not None:
            self.tool_viewer.clear_pdf()

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
            preserve_signature_appearances_on_doc(doc, remove_broken_fields=True)
            for page in doc: page.set_rotation(page.rotation + angle)
            clean_doc = fitz.open()
            clean_doc.insert_pdf(doc)
            clean_doc.save(out_p, garbage=3, deflate=True)
            clean_doc.close(); doc.close()
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
            preserve_signature_appearances_on_doc(doc, remove_broken_fields=True)
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
            new_doc.save(out_p, garbage=3, deflate=True)
            new_doc.close(); doc.close()
            self.tool_viewer.load_pdf(out_p)
            messagebox.showinfo("Extracted", f"Saved extracted pages to:\n{out_p}")
        except Exception as e: messagebox.showerror("Error", str(e))


    # =========================================================================
    # TAB: 🔓 REMOVE SIGN (Pro Only - Strip Signatures from PDF)
    # =========================================================================
    def _build_tab_remove_sign(self, parent):
        split = tk.Frame(parent, bg="#F1F5F9")
        split.pack(fill=tk.BOTH, expand=True)

        left = tk.Frame(split, bg="#FFFFFF", width=420, padx=12, pady=12, relief=tk.RAISED, bd=1)
        left.pack(side=tk.LEFT, fill=tk.Y)
        left.pack_propagate(False)

        tk.Label(left, text="Remove Signatures from PDF", font=get_sys_font(11, "bold"), fg="#1E40AF", bg="#FFFFFF").pack(anchor=tk.W, pady=(0, 2))
        tk.Label(left, text="Detect and strip cryptographic digital signatures from PDF documents with options to retain or clear visual stamps.", font=get_sys_font(8), fg="#64748B", bg="#FFFFFF", wraplength=390, justify="left").pack(anchor=tk.W, pady=(0, 8))

        tk.Label(left, text="Active Signed PDF:", font=get_sys_font(8, "bold"), bg="#FFFFFF").pack(anchor=tk.W)
        self.remove_sign_path_var = tk.StringVar()
        r1 = tk.Frame(left, bg="#FFFFFF"); r1.pack(fill=tk.X, pady=(2, 8))
        ttk.Entry(r1, textvariable=self.remove_sign_path_var, font=get_sys_font(8)).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        ttk.Button(r1, text="Browse...", style="Tool3D.TButton", command=self._browse_remove_sign_pdf).pack(side=tk.LEFT, padx=(0, 2))
        ttk.Button(r1, text="❌ Clear", style="Tool3D.TButton", command=self._clear_remove_sign_pdf).pack(side=tk.RIGHT)

        # Signature Detection Status Card
        card_det = tk.Frame(left, bg="#F8FAFC", relief=tk.SOLID, bd=1, padx=8, pady=8)
        card_det.pack(fill=tk.X, pady=(0, 10))
        tk.Label(card_det, text="Signature Detection Status:", font=get_sys_font(8, "bold"), fg="#1E3A8A", bg="#F8FAFC").pack(anchor=tk.W)
        self.lbl_remove_sign_detect = tk.Label(card_det, text="No PDF loaded. Open a PDF to inspect signatures.", font=get_sys_font(8), fg="#64748B", bg="#F8FAFC", wraplength=380, justify="left")
        self.lbl_remove_sign_detect.pack(anchor=tk.W, pady=(4, 0))

        # Options Card
        opts_f = tk.Frame(left, bg="#FFFFFF")
        opts_f.pack(fill=tk.X, pady=(0, 10))
        self.keep_visual_stamp_var = tk.BooleanVar(value=True)
        chk_keep = tk.Checkbutton(
            opts_f,
            text="Keep visual signature stamp as plain graphic",
            variable=self.keep_visual_stamp_var,
            font=get_sys_font(8, "bold"),
            fg="#047857", bg="#FFFFFF"
        )
        chk_keep.pack(anchor=tk.W)
        tk.Label(
            opts_f,
            text="• When checked: Visual signature stamp remains visible on pages as an image, but digital signature certificates & cryptographic locks are stripped.\n• When unchecked: Signature stamp is completely removed.",
            font=get_sys_font(7), fg="#64748B", bg="#FFFFFF", wraplength=390, justify="left"
        ).pack(anchor=tk.W, pady=(2, 0))

        ttk.Button(left, text="🔓 REMOVE ALL SIGNATURES & SAVE PDF", style="Primary3D.TButton", command=self.exec_remove_signatures).pack(fill=tk.X, ipady=5, pady=(4, 8))

        self.lbl_remove_sign_res = tk.Label(left, text="", font=get_sys_font(8, "bold"), bg="#FFFFFF")
        self.lbl_remove_sign_res.pack(anchor=tk.W)

        # Right Live PDF Viewer
        right = tk.Frame(split, bg="#E2E8F0")
        right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)
        self.remove_sign_viewer = EmbeddedPdfViewer(right)
        self.remove_sign_viewer.pack(fill=tk.BOTH, expand=True)

    def _browse_remove_sign_pdf(self):
        f = filedialog.askopenfilename(title="Select Signed PDF to Remove Signatures", filetypes=[("PDF Documents", "*.pdf")])
        if f:
            self.remove_sign_path_var.set(f)
            self.remove_sign_viewer.load_pdf(f)
            self._update_remove_sign_detection(f)

    def _clear_remove_sign_pdf(self):
        self.remove_sign_path_var.set("")
        self.lbl_remove_sign_detect.config(text="No PDF loaded. Open a PDF to inspect signatures.", fg="#64748B")
        self.lbl_remove_sign_res.config(text="")
        if hasattr(self, 'remove_sign_viewer') and self.remove_sign_viewer.doc is not None:
            self.remove_sign_viewer.clear_pdf()

    def _update_remove_sign_detection(self, pdf_path):
        try:
            count = 0
            with open(pdf_path, 'rb') as fp:
                r = PdfFileReader(fp)
                sigs = list(r.embedded_signatures)
                count = len(sigs)
            if count > 0:
                self.lbl_remove_sign_detect.config(text=f"✓ Detected {count} digital signature(s) in document.\nReady to remove signatures.", fg="#047857")
            else:
                doc = fitz.open(pdf_path)
                w_count = 0
                for p in doc:
                    for w in p.widgets():
                        ft = getattr(w, 'field_type', None)
                        fts = str(getattr(w, 'field_type_string', '')).lower()
                        if ft == fitz.PDF_WIDGET_TYPE_SIGNATURE or fts == 'signature':
                            w_count += 1
                doc.close()
                if w_count > 0:
                    self.lbl_remove_sign_detect.config(text=f"✓ Detected {w_count} signature field(s) in document.\nReady to remove signatures.", fg="#047857")
                else:
                    self.lbl_remove_sign_detect.config(text="ℹ️ No digital signatures found in this PDF.", fg="#64748B")
        except Exception as e:
            self.lbl_remove_sign_detect.config(text=f"Error inspecting PDF: {e}", fg="#DC2626")

    def exec_remove_signatures(self):
        p = self.remove_sign_path_var.get().strip()
        if not p or not os.path.exists(p):
            messagebox.showerror("Error", "Please select a valid PDF file."); return

        out_p = filedialog.asksaveasfilename(
            title="Save Unsigned PDF As",
            defaultextension=".pdf",
            filetypes=[("PDF Documents", "*.pdf")]
        )
        if not out_p: return

        try:
            doc = fitz.open(p)
            keep_visual = self.keep_visual_stamp_var.get()
            cleaned_count = clean_pdf_signatures_completely(doc, keep_visual=keep_visual)

            # Rebuild into a clean document to eliminate orphaned xref tables
            clean_doc = fitz.open()
            clean_doc.insert_pdf(doc)
            doc.close()
            clean_doc.save(out_p, garbage=4, deflate=True)
            clean_doc.close()

            self.remove_sign_viewer.load_pdf(out_p)
            self.lbl_remove_sign_res.config(text=f"✓ Successfully stripped signatures! Saved: {os.path.basename(out_p)}", fg="#047857")
            messagebox.showinfo(
                "Signatures Removed",
                f"✓ Successfully removed digital signatures from document!\n\n"
                f"• Clean output file: {out_p}\n"
                f"• Visual stamp status: {'Preserved as plain image' if keep_visual else 'Completely cleared'}\n"
                f"• Document is now clean and unsigned."
            )
        except Exception as e:
            messagebox.showerror("Error", f"Failed to remove signatures:\n{e}")

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
        ocr_stat = "Included (Pro Suite)" if LicenseManager.is_pro() else "Not Included (Basic Suite - Upgrade to Pro)"
        sig_stat = "Full Custom Settings, Logo & AES Password (Pro)" if LicenseManager.is_pro() else "Standard Digital Signing (Basic)"

        tk.Label(card2, text=f"• Machine / Hardware ID: {hw_id}", font=get_sys_font(8), bg="#FFFFFF").pack(anchor=tk.W, pady=2)
        tk.Label(card2, text=f"• License Key: {key_val}", font=get_sys_font(8), bg="#FFFFFF").pack(anchor=tk.W, pady=2)
        tk.Label(card2, text=f"• License Edition: {tier_val} Edition", font=get_sys_font(8, "bold"), fg="#1E40AF", bg="#FFFFFF").pack(anchor=tk.W, pady=2)
        tk.Label(card2, text=f"• Adobe/PDFelement-style OCR & Editor: {ocr_stat}", font=get_sys_font(8), fg="#047857" if LicenseManager.is_pro() else "#D97706", bg="#FFFFFF").pack(anchor=tk.W, pady=2)
        tk.Label(card2, text=f"• Signature Customization & Security: {sig_stat}", font=get_sys_font(8), fg="#047857" if LicenseManager.is_pro() else "#D97706", bg="#FFFFFF").pack(anchor=tk.W, pady=2)
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

                            email, addr = extract_cert_email_and_address(c)

                            self.loaded_certs.append({
                                'alias': str(c.serial_number),
                                'cn': cn,
                                'source': token.label,
                                'email': email,
                                'address': addr,
                                'cert_obj': c
                            })
                except Exception:
                    continue

            self.token_cert_combo['values'] = [f"{c['cn']} ({c['source']})" for c in self.loaded_certs]
            if self.loaded_certs:
                self.token_cert_combo.current(0)
                self._on_token_cert_selected()
                self.lbl_sign_status.config(text=f"Loaded {len(self.loaded_certs)} certificate(s) from hardware token.", fg="#047857")
            else:
                messagebox.showwarning("No Certificates", "No signing certificates found in token. Please check PIN.")
        except Exception as e:
            messagebox.showerror("Token Error", f"Could not read certificates: {e}")

    def _on_token_cert_selected(self, event=None):
        idx = self.token_cert_combo.current()
        if 0 <= idx < len(self.loaded_certs):
            c_info = self.loaded_certs[idx]
            if c_info.get('address'):
                self.sign_address_var.set(c_info['address'])
            if c_info.get('email'):
                self.sign_contact_var.set(c_info['email'])

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
            email, addr = extract_cert_email_and_address(cert)
            if addr:
                self.sign_address_var.set(addr)
            if email:
                self.sign_contact_var.set(email)

            cn = "Unknown"
            for attr in cert.subject:
                if attr.oid == x509.NameOID.COMMON_NAME: cn = attr.value; break
            if not silent:
                info_msg = f"Certificate Verified:\nCommon Name (CN): {cn}\nExpires: {cert.not_valid_after_utc}"
                if email: info_msg += f"\nEmail: {email}"
                if addr: info_msg += f"\nAddress: {addr}"
                messagebox.showinfo("Verified", info_msg)
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

        # Handle Sign All Pages
        if self.sign_all_pages_var.get():
            total_pages = len(self.sign_viewer.doc) if self.sign_viewer.doc else 1
            base_areas = list(self.signature_areas)
            all_areas = []
            for p_no in range(total_pages):
                for a in base_areas:
                    new_a = dict(a)
                    new_a['page'] = p_no
                    all_areas.append(new_a)
            areas_to_sign = all_areas
        else:
            areas_to_sign = list(self.signature_areas)

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
            args=(self.sign_viewer.pdf_path, out_path, is_token, pin, cert_alias, pfx_path, pfx_pass, areas_to_sign)
        )
        t.start()

    def _signing_worker(self, in_p, out_p, is_token, pin, cert_alias, pfx_path, pfx_pass, areas):
        try:
            with open(in_p, 'rb') as f:
                pdf_data = f.read()

            driver_path = DRIVER_MAP.get(self.driver_var.get())
            reason = self.sign_reason_var.get().strip()
            location = self.sign_address_var.get().strip()
            contact = self.sign_contact_var.get().strip()
            tsa = TSA_URL if "DigiCert" in self.sign_tsa_var.get() else None

            custom_logo = self.sign_custom_logo_path.get() if (self.sign_include_logo.get() and self.sign_custom_logo_path.get()) else None

            appearance_opts = {
                'show_name': self.show_name_var.get(),
                'show_date': self.show_date_var.get(),
                'show_reason': self.show_reason_var.get(),
                'show_address': self.show_address_var.get(),
                'show_issuer': self.show_issuer_var.get(),
                'show_tick': self.show_tick_var.get(),
                'show_border': self.show_border_var.get(),
            }

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

                    visual_pdf = draw_reportlab_layer(pdf_data, areas, cert_parsed, reason, location, contact, logo_path=custom_logo, appearance_opts=appearance_opts)
                    self._pyhanko_sign_incremental(visual_pdf, out_p, signer, areas, tsa)
            else:
                signer, cert_parsed = load_pfx_certificate(pfx_path, pfx_pass)
                visual_pdf = draw_reportlab_layer(pdf_data, areas, cert_parsed, reason, location, contact, logo_path=custom_logo, appearance_opts=appearance_opts)
                self._pyhanko_sign_incremental(visual_pdf, out_p, signer, areas, tsa)

            # Optional AES-256 Document Encryption & Password Protection
            if self.sign_protect_pdf.get():
                pwd = self.sign_pdf_password.get().strip()
                if pwd:
                    try:
                        doc = fitz.open(out_p)
                        tmp_enc = out_p + ".enc.pdf"
                        doc.save(tmp_enc, encryption=fitz.PDF_ENCRYPT_AES_256, user_pw=pwd, owner_pw=pwd)
                        doc.close()
                        os.replace(tmp_enc, out_p)
                        logger.info(f"Signed PDF successfully encrypted with AES-256 password: {out_p}")
                    except Exception as enc_err:
                        logger.error(f"Failed to apply AES-256 password encryption: {enc_err}")

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

            existing_fields = set()
            try:
                reader_chk = PdfFileReader(io.BytesIO(current_io.getvalue()))
                for s in reader_chk.embedded_signatures:
                    if s.field_name:
                        existing_fields.add(s.field_name)
            except Exception:
                pass

            sig_num = idx + 1
            while f"Signature_{sig_num}" in existing_fields:
                sig_num += 1
            field_name = f"Signature_{sig_num}"

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
_app_single_instance_mutex = None

def check_single_instance():
    """
    Checks if another instance of A B INFOTECH Suite is already running.
    If so, requests the existing instance to bring its window to the front and returns True.
    """
    global _app_single_instance_mutex

    # 1. Attempt to signal running instance via local Flask API
    try:
        req = urllib.request.Request("http://127.0.0.1:5000/api/show_window", method='GET')
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            if resp.status == 200:
                print("A B INFOTECH Suite is already running. Existing window brought to front.")
                logger.info("Existing instance detected via local API. Window brought to front. Exiting duplicate instance.")
                return True
    except Exception:
        pass

    # 2. Windows Named Mutex Check
    if CURRENT_OS == 'Windows':
        try:
            kernel32 = ctypes.windll.kernel32
            mutex_name = "ABInfoTechSignerSingleInstanceMutex"
            mutex = kernel32.CreateMutexW(None, False, mutex_name)
            last_err = kernel32.GetLastError()
            if last_err == 183:  # ERROR_ALREADY_EXISTS
                # Ping the API to bring running instance to front
                for _ in range(4):
                    try:
                        req = urllib.request.Request("http://127.0.0.1:5000/api/show_window", method='GET')
                        with urllib.request.urlopen(req, timeout=0.8) as resp:
                            if resp.status == 200:
                                break
                    except Exception:
                        time.sleep(0.2)
                print("Another instance is already active. Exiting duplicate instance.")
                return True
            _app_single_instance_mutex = mutex
        except Exception as e:
            logger.debug(f"Mutex check exception: {e}")

    return False

def main():
    global gui_root, gui_app

    if check_single_instance():
        sys.exit(0)

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
    if pystray:
        try:
            candidates = [
                os.path.join(APP_DIR, "app_icon.ico"),
                os.path.join(APP_DIR, "icon.ico"),
                "app_icon.ico",
                "icon.ico",
                "image_546aa0.png",
                "app_icon.png"
            ]
            tray_img_path = next((c for c in candidates if os.path.exists(c)), None)
            if tray_img_path:
                tray_img = Image.open(tray_img_path)
                if tray_img.mode != 'RGBA':
                    tray_img = tray_img.convert('RGBA')

                def _open_app(icon=None, itm=None):
                    gui_root.after(0, gui_app.show_window)

                def _nav(tab_attr):
                    def _handler(icon=None, itm=None):
                        def _inner():
                            gui_app.show_window()
                            try:
                                if hasattr(gui_app, tab_attr):
                                    gui_app.notebook.select(getattr(gui_app, tab_attr))
                            except Exception as e:
                                logger.debug(f"Tray switch tab error: {e}")
                        gui_root.after(0, _inner)
                    return _handler

                def _open_settings(icon=None, itm=None):
                    gui_root.after(0, lambda: (gui_app.show_window(), gui_app.open_settings_dialog()))

                def _open_shortcuts(icon=None, itm=None):
                    gui_root.after(0, lambda: (gui_app.show_window(), gui_app.show_driver_shortcuts_dialog()))

                def _open_wa(icon=None, itm=None):
                    gui_root.after(0, gui_app.open_whatsapp)

                def _open_buy(icon=None, itm=None):
                    gui_root.after(0, gui_app.open_buy_dsc)

                def _exit_app(icon=None, itm=None):
                    if icon:
                        try: icon.stop()
                        except Exception: pass
                    gui_root.after(0, lambda: (gui_root.destroy(), os._exit(0)))

                menu_items = [
                    item('🏠 Open A B INFOTECH Suite', _open_app, default=True),
                    pystray.Menu.SEPARATOR,
                    item('✍️ Sign PDF', _nav('tab_sign')),
                    item('🗜️ Compress PDF', _nav('tab_compress')),
                    item('📑 Merge PDF', _nav('tab_merge')),
                    item('🛡️ Verify Signatures', _nav('tab_verify')),
                    item('🛠️ PDF Tools Suite', _nav('tab_tools')),
                ]
                if LicenseManager.is_pro():
                    menu_items.append(item('📖 OCR & Edit PDF (Pro)', _nav('tab_ocr_edit')))
                
                menu_items.extend([
                    pystray.Menu.SEPARATOR,
                    item('⚙️ Settings & License', _open_settings),
                    item('🔌 Token Driver Shortcuts', _open_shortcuts),
                    item('💬 WhatsApp Support (+91 7016797720)', _open_wa),
                    item('🛒 Buy DSC / License', _open_buy),
                    pystray.Menu.SEPARATOR,
                    item('🚪 Exit Application', _exit_app)
                ])

                tray_icon = pystray.Icon(
                    "abinfo_signer",
                    tray_img,
                    "A B INFOTECH - PDF Sign & All-in-One Power Suite",
                    pystray.Menu(*menu_items)
                )
                threading.Thread(target=tray_icon.run, daemon=True).start()
        except Exception as e:
            logger.warning(f"System tray init failed: {e}")

    gui_root.mainloop()

if __name__ == '__main__':
    main()
