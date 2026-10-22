#!/usr/bin/env python3
"""
A B INFOTECH - Cross-Platform Unified Digital Signer (server2.py)
Supported Platforms: Windows, macOS (Darwin), Linux
Features:
- Local Desktop GUI (Tkinter with dynamic OS-native typography & 3D styling)
- Dual Signing Engine: Hardware Token (PKCS#11) & Software Certificate (.P12 / .PFX)
- Cross-Platform Hardware Token Driver Mapping (.dll for Windows, .dylib for macOS, .so for Linux)
- Custom Driver Browser (select any PKCS#11 library on any OS)
- Batch Folder Signing with Multi-Page & Single-Page selection
- Visual Signature Stamp Overlay (ReportLab with dynamic scaling)
- Web API Bridge (Flask daemon with CORS & system tray integration)
- ASN.1 Tag Mismatch Fix (DirectoryString for telephone_number)
- Offline & Steganography License Verification
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
# Prevent PyMuPDF 1.25+ experimental layout/LLM modules from causing crashes in frozen builds
sys.modules['pymupdf4llm'] = None
sys.modules['pymupdf.layout'] = None

try:
    import fitz  # PyMuPDF
    from PIL import Image, ImageTk
except ImportError:
    print("Please install PyMuPDF and Pillow: pip install PyMuPDF Pillow")
    sys.exit(1)

# System Tray Components
try:
    import pystray
    from pystray import MenuItem as item
except ImportError:
    print("Please install pystray: pip install pystray")
    sys.exit(1)

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
    from pyhanko.sign.signers import SimpleSigner
    from pyhanko.sign.fields import SigFieldSpec, append_signature_field
    from pyhanko.sign.pkcs11 import PKCS11Signer
    from pyhanko.stamp import TextStampStyle
    from pyhanko.sign.timestamps import HTTPTimeStamper
    from pyhanko.sign.validation import validate_pdf_signature
    from pyhanko.pdf_utils.reader import PdfFileReader
    from reportlab.pdfgen import canvas as rl_canvas
    from reportlab.lib.colors import Color, HexColor
except ImportError:
    print("Missing cryptography libraries: pip install PyPDF2 cryptography python-pkcs11 pyhanko[pkcs11] reportlab requests")
    sys.exit(1)

# Fix ASN.1 parsing for Indian DSCs (OID 2.5.4.20 telephone_number as UTF8String tag 12 vs PrintableString tag 19)
try:
    import asn1crypto.x509
    asn1crypto.x509.NameTypeAndValue._oid_specs['telephone_number'] = asn1crypto.x509.DirectoryString
except Exception:
    pass

# Safe monkey-patch for pyhanko Signer.subject_name so it never crashes on subject extraction
try:
    from pyhanko.sign.signers.pdf_cms import Signer as PyHankoSigner
    def _safe_subject_name(self):
        if getattr(self, '_custom_name', None):
            return self._custom_name
        if self.signing_cert is None:
            return "Digital Signer"
        try:
            name = self.signing_cert.subject
            if hasattr(name, 'native') and isinstance(name.native, dict):
                val = name.native.get('common_name') or name.native.get('organization_name')
                if val:
                    return str(val)
        except Exception:
            pass
        return "Digital Signer"
    PyHankoSigner.subject_name = property(_safe_subject_name)
except Exception:
    pass

# ==========================================
# 0. SETUP DIRECTORY AND LOGGING (CROSS-PLATFORM)
# ==========================================
USER_HOME = os.path.expanduser('~')
APP_DIR = os.path.join(USER_HOME, '.abinfotech_signer')

if not os.path.exists(APP_DIR):
    os.makedirs(APP_DIR, exist_ok=True)
    if CURRENT_OS == 'Windows':
        try:
            ctypes.windll.kernel32.SetFileAttributesW(APP_DIR, 0x02)  # Hidden attribute
        except Exception:
            pass
    # On macOS and Linux, folders starting with '.' are automatically hidden by the OS

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

# Cross-Platform Native Fonts
def get_sys_font(size=9, weight="normal"):
    if CURRENT_OS == 'Darwin':
        family = "Helvetica Neue"
    elif CURRENT_OS == 'Linux':
        family = "DejaVu Sans"
    else:
        family = "Segoe UI"
    return (family, size, weight)

# ==========================================
# 0.5. LICENSE MANAGER (CROSS-PLATFORM)
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
    def save_license(key, expiredate="never"):
        data_to_encode = f"{key}|||{expiredate}"
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
    def get_license_data():
        if not LicenseManager.is_activated():
            return None, None
        try:
            with open(LicenseManager.LICENSE_FILE, "rb") as f:
                data = f.read()
            if LicenseManager.MAGIC_DELIMITER in data:
                parts = data.split(LicenseManager.MAGIC_DELIMITER)
                if len(parts) > 1:
                    encoded_data = parts[-1]
                    decoded_str = base64.b64decode(encoded_data).decode('utf-8')
                    if "|||" in decoded_str:
                        k, exp = decoded_str.split("|||", 1)
                        return k, exp
                    else:
                        return decoded_str, "never"
        except Exception as e:
            logger.error(f"Failed to read license data: {e}")
        return None, None

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

# ==========================================
# 1. CROSS-PLATFORM DRIVER MAPPING
# ==========================================
def get_platform_drivers():
    """Returns detected PKCS#11 drivers based on current Operating System."""
    drivers = {}
    if CURRENT_OS == 'Windows':
        is_64bit = sys.maxsize > 2**32
        p64 = r"C:\WINDOWS\system32\cryptoida_pkcs11.dll"
        p32 = r"C:\WINDOWS\sysWOW64\cryptoida_pkcs11.dll"
        mtoken = p64 if (is_64bit and os.path.exists(p64)) else p32

        candidates = {
            "ProxKey India": r"C:\Windows\System32\Watchdata\PROXKey CSP India V3.0\wdpkcs.dll",
            "mToken / CryptoID": mtoken,
            "ePass2003 Token": r"C:\Windows\System32\eps2003csp11v2.dll",
            "InnaIT Token": r"C:\Windows\System32\InnaITPKCS11Driver.dll",
            "Generic OpenSC": r"C:\Program Files\OpenSC Project\OpenSC\pkcs11\opensc-pkcs11.dll"
        }
        for name, path in candidates.items():
            if os.path.exists(path):
                drivers[name] = path
            elif not drivers:
                # Keep at least candidates as reference
                drivers[name] = path
    elif CURRENT_OS == 'Darwin':  # macOS
        candidates = {
            "OpenSC (macOS Homebrew)": "/opt/homebrew/lib/opensc-pkcs11.so",
            "OpenSC (macOS Intel)": "/usr/local/lib/opensc-pkcs11.so",
            "OpenSC (Library)": "/Library/OpenSC/lib/opensc-pkcs11.so",
            "mToken / CryptoID (macOS)": "/Library/mToken/libcryptoid_pkcs11.dylib",
            "ePass2003 Token (macOS)": "/Library/EnterSafe/libcastle.dylib",
            "ProxKey India (macOS)": "/Library/ProxKey/libWD_PKCS11.dylib"
        }
        for name, path in candidates.items():
            if os.path.exists(path):
                drivers[name] = path
        if not drivers:
            drivers = {
                "OpenSC (macOS)": "/Library/OpenSC/lib/opensc-pkcs11.so",
                "mToken (macOS)": "/Library/mToken/libcryptoid_pkcs11.dylib",
                "ePass2003 (macOS)": "/Library/EnterSafe/libcastle.dylib",
                "ProxKey (macOS)": "/Library/ProxKey/libWD_PKCS11.dylib"
            }
    else:  # Linux
        candidates = {
            "OpenSC (Ubuntu/Debian 64-bit)": "/usr/lib/x86_64-linux-gnu/opensc-pkcs11.so",
            "OpenSC (RHEL/CentOS 64-bit)": "/usr/lib64/opensc-pkcs11.so",
            "OpenSC (Generic Linux)": "/usr/lib/opensc-pkcs11.so",
            "ProxKey (Watchdata Linux)": "/usr/lib/libWatchdataPKCS11.so",
            "ePass2003 (Linux)": "/usr/lib/libcastle.so.1.0.0",
            "mToken / CryptoID (Linux)": "/usr/lib/libcryptoid_pkcs11.so"
        }
        for name, path in candidates.items():
            if os.path.exists(path):
                drivers[name] = path
        if not drivers:
            drivers = {
                "OpenSC (Linux)": "/usr/lib/x86_64-linux-gnu/opensc-pkcs11.so",
                "ProxKey (Linux)": "/usr/lib/libWatchdataPKCS11.so",
                "ePass2003 (Linux)": "/usr/lib/libcastle.so.1.0.0",
                "mToken (Linux)": "/usr/lib/libcryptoid_pkcs11.so"
            }

    drivers["[Browse Custom Driver Library...]"] = "__CUSTOM__"
    return drivers

DRIVER_MAP = get_platform_drivers()

server_config = {'status': 'stopped', 'port': 5000, 'host': '127.0.0.1', 'driver_path': ''}
TSA_URL = "http://timestamp.digicert.com"

# ==========================================
# 1.5. PKCS#12 (.P12 / .PFX) SIGNER LOADER
# ==========================================
def load_pfx_signer_and_cert(pfx_path_or_bytes, password):
    """
    Loads a PKCS#12 (.p12 / .pfx) file and returns (signer, cert_parsed).
    Works reliably across Windows, macOS, and Linux.
    """
    pass_bytes = password.encode('utf-8') if (isinstance(password, str) and password) else (password or None)

    if isinstance(pfx_path_or_bytes, (str, Path)):
        if not os.path.exists(pfx_path_or_bytes):
            raise FileNotFoundError(f"Certificate file not found: {pfx_path_or_bytes}")
        with open(pfx_path_or_bytes, 'rb') as f:
            pfx_data = f.read()
    else:
        pfx_data = pfx_path_or_bytes

    try:
        priv_key, cert_parsed, ca_certs = pkcs12.load_key_and_certificates(pfx_data, pass_bytes)
    except Exception as e:
        err_str = str(e).lower()
        if "mac" in err_str or "password" in err_str or "pkcs12" in err_str:
            raise ValueError("Incorrect password for .p12/.pfx file.")
        raise ValueError(f"Could not open .p12/.pfx certificate: {e}")

    if not cert_parsed:
        raise ValueError("No certificate found inside the .p12/.pfx file.")
    if not priv_key:
        raise ValueError("No private signing key found inside the .p12/.pfx file.")

    signer = None
    try:
        if isinstance(pfx_path_or_bytes, (str, Path)):
            signer = SimpleSigner.load_pkcs12(pfx_file=str(pfx_path_or_bytes), passphrase=pass_bytes)
        else:
            import tempfile
            with tempfile.NamedTemporaryFile(delete=False, suffix=".p12") as tmp:
                tmp.write(pfx_data)
                tmp_path = tmp.name
            try:
                signer = SimpleSigner.load_pkcs12(pfx_file=tmp_path, passphrase=pass_bytes)
            finally:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
    except Exception as e_p12:
        logger.warning(f"SimpleSigner.load_pkcs12 fallback: {e_p12}")
        try:
            signer = SimpleSigner(
                signing_cert=cert_parsed,
                signing_key=priv_key,
                cert_registry=ca_certs or []
            )
        except Exception:
            pass

    signer_name = None
    try:
        cn_attrs = cert_parsed.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)
        if cn_attrs:
            signer_name = str(cn_attrs[0].value)
    except Exception:
        pass
    if not signer_name:
        try:
            o_attrs = cert_parsed.subject.get_attributes_for_oid(x509.NameOID.ORGANIZATION_NAME)
            if o_attrs:
                signer_name = str(o_attrs[0].value)
        except Exception:
            pass
    if not signer_name:
        signer_name = "Digital Signer"

    if signer:
        signer._custom_name = signer_name

    return signer, cert_parsed

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

    close_script = "<script>setTimeout(function(){ window.opener = null; window.open('','_self'); window.close(); }, 1000);</script>"

    if status == 'valid' and key:
        LicenseManager.save_license(key, expiredate)
        LicenseManager.activation_error = None
        return f"""
        <html><body style='font-family: Arial; text-align: center; padding: 50px; background-color: #f4f4f9;'>
            <h1 style='color: #2ecc71;'>Activation Successful!</h1>
            <p style='font-size: 18px;'>Your software is now unlocked.</p>
            {close_script}
        </body></html>
        """
    else:
        LicenseManager.activation_error = message
        return f"""
        <html><body style='font-family: Arial; text-align: center; padding: 50px; background-color: #f4f4f9;'>
            <h1 style='color: #e74c3c;'>Activation Failed</h1>
            <p style='font-size: 18px; color: #333;'>{message}</p>
            {close_script}
        </body></html>
        """

@app.route('/api/sync_callback')
def sync_callback():
    status = request.args.get('status')
    key = LicenseManager.get_license_key()
    close_script = "<script>setTimeout(function(){ window.opener = null; window.open('','_self'); window.close(); }, 1000);</script>"

    if status in ['expired', 'inactive', 'invalid', 'suspended']:
        logger.warning(f"Browser Sync: License is {status}. Locking app.")
        LicenseManager.save_license(key, "2000-01-01 00:00:00")
        return f"""
        <html><body style='font-family: Arial; text-align: center; padding: 50px; background-color: #f4f4f9;'>
            <h2 style='color: #e74c3c;'>License Suspended or Expired</h2>
            <p style='font-size: 18px;'>Your software has been locked.</p>
            {close_script}
        </body></html>
        """
    else:
        logger.info("Browser Sync: License is active!")
        return f"""
        <html><body style='font-family: Arial; text-align: center; padding: 50px; background-color: #f4f4f9;'>
            <h2 style='color: #2ecc71;'>License Verified Online!</h2>
            <p style='font-size: 18px;'>Your software is fully synced.</p>
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
    active_key = LicenseManager.get_license_key()

    return jsonify({
        'status': 'healthy',
        'server_status': server_config['status'],
        'version': '1.1-crossplatform',
        'os': CURRENT_OS,
        'demo': is_demo,
        'expired': is_expired,
        'key': active_key
    })

@app.route('/api/certificates')
def api_certs():
    req_driver = request.args.get('driver_name')
    if req_driver and req_driver in DRIVER_MAP:
        server_config['driver_path'] = DRIVER_MAP[req_driver]

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
                            except x509.ExtensionNotFound: pass

                            cn = "Unknown"
                            for attr in cert.subject:
                                if attr.oid == x509.NameOID.COMMON_NAME:
                                    cn = attr.value; break

                            certs.append({
                                'alias': str(cert.serial_number),
                                'cn': cn,
                                'source': token.label
                            })
                except Exception: continue
        except Exception as e:
            logger.error(f"Error reading token: {e}")

    return jsonify(certs)

@app.route('/api/sign', methods=['POST'])
def sign_pdf():
    try:
        pdf_file = request.files.get('pdf')
        if not pdf_file: return jsonify({"error": "No PDF provided"}), 400
        pdf_data = pdf_file.read()

        driver_name = request.form.get('driver_name')
        driver_path = DRIVER_MAP.get(driver_name) if driver_name else server_config['driver_path']

        pin = request.form.get('pin')
        serial_number = request.form.get('serial_number')
        areas_raw = request.form.get('areas')

        if not driver_path or not os.path.exists(driver_path):
            return jsonify({"error": "Invalid driver selected or driver library missing."}), 400
        if not pin or not serial_number:
            return jsonify({"error": "Token PIN and Certificate Serial Number required."}), 400

        signature_areas = json.loads(areas_raw) if areas_raw else []
        if not signature_areas:
            return jsonify({"error": "No signature area coordinates provided."}), 400

        lib = pkcs11.lib(driver_path)
        token = None
        for slot in lib.get_slots(token_present=True):
            try:
                token = slot.get_token()
                break
            except Exception: continue

        if not token: return jsonify({"error": "Hardware Token not found. Ensure USB token is plugged in."}), 400

        with token.open(user_pin=pin) as session:
            cert_id, cert_parsed = None, None
            for obj in session.get_objects({pkcs11.Attribute.CLASS: pkcs11.ObjectClass.CERTIFICATE}):
                c = x509.load_der_x509_certificate(obj[pkcs11.Attribute.VALUE])
                if str(c.serial_number) == str(serial_number):
                    cert_id, cert_parsed = obj[pkcs11.Attribute.ID], c
                    break

            if not cert_id: return jsonify({"error": "Certificate not found on token. Check PIN."}), 400
            signer = PKCS11Signer(pkcs11_session=session, cert_id=cert_id)

            signer_name = None
            if cert_parsed:
                try:
                    cn_attrs = cert_parsed.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)
                    if cn_attrs: signer_name = str(cn_attrs[0].value)
                except Exception: pass
                if not signer_name:
                    try:
                        o_attrs = cert_parsed.subject.get_attributes_for_oid(x509.NameOID.ORGANIZATION_NAME)
                        if o_attrs: signer_name = str(o_attrs[0].value)
                    except Exception: pass
            if not signer_name: signer_name = "Digital Signer"
            if hasattr(signer, '_custom_name'): signer._custom_name = signer_name

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
                    signature_meta=signers.PdfSignatureMetadata(field_name=field_name, name=signer_name),
                    signer=signer,
                    stamp_style=TextStampStyle(stamp_text="", border_width=0),
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

# ==========================================
# 3. REPORTLAB VISUAL LAYER (3D ENHANCED)
# ==========================================
def draw_reportlab_layer(pdf_bytes, areas, cert):
    if not areas: return pdf_bytes

    subj = cert.subject.rfc4514_string()
    def get_f(k):
        for p in subj.split(','):
            if p.strip().startswith(k+'='): return p.split('=', 1)[1].strip()
        return "N/A"

    ist = timezone(timedelta(hours=5, minutes=30))
    now_ist = datetime.now(ist).strftime('%Y.%m.%d %H:%M:%S IST')

    cn_name = get_f('CN')
    org_name = get_f('O')
    unit_name = get_f('OU')
    country = get_f('C')
    state = get_f('ST')

    reader = PdfReader(io.BytesIO(pdf_bytes))
    writer = PdfWriter()
    n_pages = len(reader.pages) if hasattr(reader, 'pages') else reader.getNumPages()

    for p_idx in range(n_pages):
        page = reader.pages[p_idx] if hasattr(reader, 'pages') else reader.getPage(p_idx)
        p_areas = [a for a in areas if int(a.get('page', 0)) == p_idx]

        if p_areas:
            pw = float(page.mediabox.width if hasattr(page.mediabox, 'width') else page.mediaBox.getWidth())
            ph = float(page.mediabox.height if hasattr(page.mediabox, 'height') else page.mediaBox.getHeight())

            packet = io.BytesIO()
            c = rl_canvas.Canvas(packet, pagesize=(pw, ph))

            for area in p_areas:
                ax = float(area['x'])
                ay = float(area['y'])
                aw = float(area['width'])
                ah = float(area['height'])

                # 3D Drop Shadow
                c.setFillColor(HexColor('#0F172A'), alpha=0.10)
                c.roundRect(ax + 2, ay - 2, aw, ah, 5, stroke=0, fill=1)

                # Signature Box Background & Border
                c.setFillColor(HexColor('#F8FAFC'), alpha=0.96)
                c.setStrokeColor(HexColor('#0F766E'))
                c.setLineWidth(1.2)
                c.roundRect(ax, ay, aw, ah, 5, stroke=1, fill=1)

                # Accent Header Stripe
                stripe_h = max(min(ah * 0.18, 14.0), 5.0)
                c.setFillColor(HexColor('#0F766E'))
                c.roundRect(ax + 0.6, ay + ah - stripe_h, aw - 1.2, stripe_h, 3, stroke=0, fill=1)

                # Watermark Icon/Text
                c.setFillColor(HexColor('#0F766E'), alpha=0.08)
                c.setFont('Helvetica-Bold', max(min(ah * 0.45, 26.0), 8.0))
                c.drawRightString(ax + aw - 8, ay + 6, "DIGITALLY SIGNED")

                # Text Content Lines
                lines = [
                    ('HEADER', "Digitally signed by:"),
                    ('NAME', cn_name),
                    ('ORG', f"Organization: {org_name}"),
                    ('UNIT', f"Unit: {unit_name}"),
                    ('DATE', f"Date: {now_ist}"),
                    ('LOC', f"Location: {state}, {country}")
                ]

                pad_x = max(aw * 0.04, 6.0)
                pad_top = stripe_h + 3.0
                pad_bot = 4.0
                avail_h = max(ah - pad_top - pad_bot, 10.0)
                avail_w = max(aw - (pad_x * 2.0), 20.0)

                n_lines = len(lines)
                line_step = avail_h / float(n_lines)

                fs_h = line_step * 0.78
                max_str_len = max(len(t) for _, t in lines)
                fs_w = avail_w / (max_str_len * 0.54)
                fs = min(fs_h, fs_w, 11.0)
                fs = max(fs, 3.8)

                line_step = max(fs * 1.16, avail_h / float(n_lines))
                total_text_h = (n_lines - 1) * line_step + fs
                start_y = ay + ah - pad_top - fs - max(0.0, (avail_h - total_text_h) / 2.0)

                cur_y = start_y
                for ltype, text in lines:
                    if ltype == 'HEADER':
                        c.setFont('Helvetica-Bold', min(fs * 1.05, 11.0))
                        c.setFillColor(HexColor('#FFFFFF'))  # White text inside the stripe
                        c.drawString(ax + pad_x, ay + ah - stripe_h + (stripe_h - min(fs * 1.05, 11.0)) / 2.0, text)
                        continue
                    elif ltype == 'NAME':
                        c.setFont('Helvetica-Bold', fs)
                        c.setFillColor(HexColor('#0F172A'))
                    elif ltype == 'DATE':
                        c.setFont('Helvetica-Bold', max(fs * 0.95, 3.8))
                        c.setFillColor(HexColor('#1E3A8A'))
                    else:
                        c.setFont('Helvetica', max(fs * 0.95, 3.8))
                        c.setFillColor(HexColor('#334155'))

                    c.drawString(ax + pad_x, cur_y, text)
                    cur_y -= line_step

            c.save(); packet.seek(0)
            overlay_page = PdfReader(packet).pages[0] if hasattr(PdfReader(packet), 'pages') else PdfReader(packet).getPage(0)

            if hasattr(page, 'merge_page'): page.merge_page(overlay_page)
            else: page.mergePage(overlay_page)

        if hasattr(writer, 'add_page'): writer.add_page(page)
        else: writer.addPage(page)

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()

def sign_pdf_stream(pdf_bytes, areas, signer, cert_parsed, tst_client=None):
    if not areas: return pdf_bytes

    visual_pdf_data = draw_reportlab_layer(pdf_bytes, areas, cert_parsed)
    current_io = io.BytesIO(visual_pdf_data)
    base_reader = PdfReader(io.BytesIO(pdf_bytes))

    signer_name = None
    if cert_parsed:
        try:
            cn_attrs = cert_parsed.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)
            if cn_attrs: signer_name = str(cn_attrs[0].value)
        except Exception: pass
        if not signer_name:
            try:
                o_attrs = cert_parsed.subject.get_attributes_for_oid(x509.NameOID.ORGANIZATION_NAME)
                if o_attrs: signer_name = str(o_attrs[0].value)
            except Exception: pass
    if not signer_name and hasattr(signer, '_custom_name'):
        signer_name = signer._custom_name
    if not signer_name:
        signer_name = "Digital Signer"

    if hasattr(signer, '_custom_name'):
        signer._custom_name = signer_name

    for idx, area in enumerate(areas):
        field_name = f"Signature_Page{int(area.get('page', 0))+1}_{idx+1}_{int(time.time()*1000)%100000}"
        p_idx = int(area.get('page', 0))
        if p_idx >= len(base_reader.pages): continue
        page_meta = base_reader.pages[p_idx]

        offset_x = float(page_meta.mediabox.left if hasattr(page_meta.mediabox, 'left') else page_meta.mediaBox.getLowerLeft_x())
        offset_y = float(page_meta.mediabox.bottom if hasattr(page_meta.mediabox, 'bottom') else page_meta.mediaBox.getLowerLeft_y())

        x1 = int(float(area['x']) + offset_x)
        y1 = int(float(area['y']) + offset_y)
        x2 = int(float(area['x']) + float(area['width']) + offset_x)
        y2 = int(float(area['y']) + float(area['height']) + offset_y)

        if x2 <= x1: x2 = x1 + 10
        if y2 <= y1: y2 = y1 + 10

        writer = IncrementalPdfFileWriter(current_io)
        append_signature_field(writer, SigFieldSpec(field_name, box=(x1, y1, x2, y2), on_page=p_idx))

        tmp_io = io.BytesIO(); writer.write(tmp_io); tmp_io.seek(0)
        final_writer = IncrementalPdfFileWriter(tmp_io)

        pdf_signer = signers.PdfSigner(
            signature_meta=signers.PdfSignatureMetadata(field_name=field_name, name=signer_name),
            signer=signer,
            stamp_style=TextStampStyle(stamp_text="", border_width=0),
            timestamper=tst_client
        )

        signed_out = io.BytesIO()
        try:
            pdf_signer.sign_pdf(final_writer, in_place=False, output=signed_out)
        except Exception as sign_err:
            logger.warning(f"Signing with TSA failed ({sign_err}). Retrying without timestamper...")
            if tst_client:
                tmp_io.seek(0)
                fallback_writer = IncrementalPdfFileWriter(tmp_io)
                pdf_signer_no_tsa = signers.PdfSigner(
                    signature_meta=signers.PdfSignatureMetadata(field_name=field_name, name=signer_name),
                    signer=signer,
                    stamp_style=TextStampStyle(stamp_text="", border_width=0),
                    timestamper=None
                )
                signed_out = io.BytesIO()
                pdf_signer_no_tsa.sign_pdf(fallback_writer, in_place=False, output=signed_out)
            else:
                raise sign_err

        signed_out.seek(0)
        current_io = signed_out

    return current_io.getvalue()

# ==========================================
# 4. CROSS-PLATFORM DESKTOP APP (Tkinter GUI)
# ==========================================
class VisualPdfSignerApp:
    def __init__(self, root):
        self.root = root
        self.root.title(f"A B INFOTECH - Local Digital Signer v1.1 [{CURRENT_OS}]")
        self.root.geometry("1300x850")
        self.root.minsize(1024, 700)
        self.root.protocol('WM_DELETE_WINDOW', self.hide_window)

        self._configure_styles()
        self._apply_window_icon(self.root)

        self.pdf_doc = None
        self.input_pdf_path = None
        self.current_page = 0
        self.scale = 1.35
        self.drawing_mode = False
        self.start_x = 0; self.start_y = 0; self.temp_rect = None
        self.signature_areas = []
        self.loaded_certs = []
        self.just_activated = False

        self.cert_source_var = tk.StringVar(value="pfx" if not DRIVER_MAP or list(DRIVER_MAP.keys())[0] == "[Browse Custom Driver Library...]" else "token")
        self.pfx_path_var = tk.StringVar()
        self.pfx_pass_var = tk.StringVar()
        self.all_pages_var = tk.BooleanVar(value=True)

        if not LicenseManager.is_activated():
            self._show_activation_screen()
        else:
            status = LicenseManager.check_expiration_local()
            if status == "expired":
                messagebox.showwarning("License Expired", "Your software license has expired!\n\nPlease contact 7016797720 or sales@a-b-infotech.in to renew.")
                self._show_activation_screen()
            elif status in ["inactive", "invalid"]:
                messagebox.showerror("License Invalid", "Your license is invalid or inactive.")
                self._show_activation_screen()
            else:
                self._init_app_interface()

    def _configure_styles(self):
        style = ttk.Style(self.root)
        available = style.theme_names()
        if 'clam' in available:
            style.theme_use('clam')

        f_normal = get_sys_font(9)
        f_bold = get_sys_font(9, "bold")

        style.configure("TLabel", font=f_normal)
        style.configure("TButton", font=f_normal)
        style.configure("Header.TLabel", font=get_sys_font(14, "bold"), foreground="#1E40AF")
        style.configure("Toolbar.TFrame", background="#E2E8F0")

        # 3D Push Button Styles
        style.configure("Primary3D.TButton", font=get_sys_font(10, "bold"), background="#059669", foreground="#FFFFFF")
        style.map("Primary3D.TButton",
            background=[("pressed", "#047857"), ("active", "#10B981")],
            foreground=[("pressed", "#FFFFFF"), ("active", "#FFFFFF")]
        )

        style.configure("Tool3D.TButton", font=f_bold, background="#F1F5F9", foreground="#1E293B")
        style.map("Tool3D.TButton",
            background=[("pressed", "#CBD5E1"), ("active", "#E2E8F0")]
        )

        style.configure("Batch.TButton", font=f_bold, background="#2563EB", foreground="#FFFFFF")
        style.map("Batch.TButton",
            background=[("pressed", "#1D4ED8"), ("active", "#3B82F6")],
            foreground=[("pressed", "#FFFFFF"), ("active", "#FFFFFF")]
        )

        style.configure("Draw3D.TButton", font=f_bold, background="#D97706", foreground="#FFFFFF")
        style.map("Draw3D.TButton",
            background=[("pressed", "#B45309"), ("active", "#F59E0B")],
            foreground=[("pressed", "#FFFFFF"), ("active", "#FFFFFF")]
        )

    def _create_3d_card(self, parent, bg="#FFFFFF", shadow_color="#93C5FD", pady=(0, 4)):
        shadow = tk.Frame(parent, bg=shadow_color, bd=0)
        shadow.pack(fill=tk.X, pady=pady)
        card = tk.Frame(shadow, bg=bg, relief=tk.RAISED, bd=1, padx=8, pady=5)
        card.pack(fill=tk.BOTH, expand=True, padx=(0, 2), pady=(0, 2))
        card.shadow = shadow
        return card

    def _maximize_window(self, window):
        try:
            if CURRENT_OS == 'Windows':
                window.state('zoomed')
            elif CURRENT_OS == 'Darwin':
                window.attributes('-zoomed', True)
            else:
                window.attributes('-zoomed', True)
        except Exception:
            pass

    def _apply_window_icon(self, window):
        try:
            icon_file = LicenseManager.LICENSE_FILE
            if not os.path.exists(icon_file):
                icon_file = "app_icon.ico" if os.path.exists("app_icon.ico") else "icon.ico"

            if os.path.exists(icon_file):
                if CURRENT_OS == 'Windows':
                    try:
                        window.iconbitmap(icon_file)
                        return
                    except Exception:
                        pass
                # Cross-platform iconphoto via PIL
                pil_icon = Image.open(icon_file)
                self.window_icon = ImageTk.PhotoImage(pil_icon)
                window.iconphoto(True, self.window_icon)
            else:
                img = Image.new('RGB', (64, 64), color=(0, 120, 215))
                self.window_icon = ImageTk.PhotoImage(img)
                window.iconphoto(True, self.window_icon)
        except Exception as e:
            logger.warning(f"Could not load window icon: {e}")

    def hide_window(self):
        self.root.withdraw()

    def show_window(self):
        self.root.deiconify()
        self._maximize_window(self.root)
        self.root.lift()
        self.root.focus_force()

    def _show_activation_screen(self):
        self.act_win = tk.Toplevel(self.root)
        self.act_win.title("Activation Required - A B INFOTECH")
        self._apply_window_icon(self.act_win)
        self.act_win.geometry("420x260")
        self.act_win.resizable(False, False)

        tk.Label(self.act_win, text="Enter License Key to Activate:", font=get_sys_font(11, "bold"), fg="#1E3A8A").pack(pady=(20, 10))
        self.key_entry = ttk.Entry(self.act_win, width=38, show="*", font=get_sys_font(10))
        self.key_entry.pack(pady=5)
        self.key_entry.focus()

        self.btn_activate = ttk.Button(self.act_win, text="Activate Software via Browser", style="Primary3D.TButton", command=self._verify)
        self.btn_activate.pack(pady=20, ipady=4)

    def _open_url_in_app_mode(self, url, hide_window=False):
        """Cross-platform helper to launch browser cleanly."""
        if CURRENT_OS == 'Windows':
            browser_paths = [
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
            ]
            for b_path in browser_paths:
                if os.path.exists(b_path):
                    args = [b_path, f"--app={url}"]
                    if hide_window:
                        args.append("--window-position=-2000,-2000")
                    subprocess.Popen(args)
                    return
        elif CURRENT_OS == 'Darwin':
            chrome_mac = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
            if os.path.exists(chrome_mac):
                args = [chrome_mac, f"--app={url}"]
                subprocess.Popen(args)
                return
        elif CURRENT_OS == 'Linux':
            for b in ["google-chrome", "chromium-browser", "chromium"]:
                try:
                    subprocess.Popen([b, f"--app={url}"])
                    return
                except FileNotFoundError:
                    continue

        webbrowser.open(url)

    def _verify(self):
        user_key = self.key_entry.get().strip()
        if not user_key:
            messagebox.showerror("Error", "Please enter a valid License Key.")
            return

        if user_key == "ABI-MASTER-PASS":
            LicenseManager.save_license(user_key, "never")
            messagebox.showinfo("Success", "Developer Bypass Activated!")
            self.act_win.destroy()
            self.just_activated = True
            self._init_app_interface()
            return

        self.btn_activate.config(state=tk.DISABLED, text="Waiting for Browser...")
        safe_key = urllib.parse.quote(user_key)
        url = f"{LicenseManager.WEBSITE_URL}?key={safe_key}"
        self._open_url_in_app_mode(url, hide_window=False)
        self._wait_for_callback()

    def _wait_for_callback(self):
        if LicenseManager.is_activated():
            status = LicenseManager.check_expiration_local()
            if status == "expired":
                messagebox.showwarning("License Expired", "This key is expired! Please contact 7016797720 to renew.")
                self.btn_activate.config(state=tk.NORMAL, text="Activate Software")
                return
            elif status in ["inactive", "invalid"]:
                messagebox.showerror("License Invalid", "This key is inactive or invalid.")
                self.btn_activate.config(state=tk.NORMAL, text="Activate Software")
                return

            messagebox.showinfo("Success", "License Activated Successfully!")
            self.act_win.destroy()
            self.just_activated = True
            self._init_app_interface()
        elif LicenseManager.activation_error:
            error_msg = LicenseManager.activation_error
            LicenseManager.activation_error = None
            messagebox.showerror("Activation Failed", error_msg)
            if self.act_win.winfo_exists():
                self.btn_activate.config(state=tk.NORMAL, text="Activate Software")
        else:
            if self.act_win.winfo_exists():
                self.act_win.after(1000, self._wait_for_callback)

    def _run_startup_sync(self):
        key = LicenseManager.get_license_key()
        if not key or key == "ABI-MASTER-PASS": return
        try:
            safe_key = urllib.parse.quote(key)
            url = f"{LicenseManager.WEBSITE_URL}?action=browser_sync&key={safe_key}"
            self._open_url_in_app_mode(url, hide_window=True)
            self.root.after(0, lambda: self.lbl_status.config(text="Verifying license online...", foreground="blue"))
            self._monitor_sync_status()
        except Exception as e:
            logger.warning(f"Startup Sync fallback: {e}")

    def _monitor_sync_status(self):
        if LicenseManager.check_expiration_local() == "expired":
            self._force_lock_ui()
        else:
            self.root.after(2000, self._check_lock_again)

    def _check_lock_again(self):
        if LicenseManager.check_expiration_local() == "expired":
            self._force_lock_ui()
        else:
            self.lbl_status.config(text=f"Ready [{CURRENT_OS}]", foreground="green")

    def _force_lock_ui(self):
        messagebox.showwarning("License Expired", "Your software license was checked online and has been suspended or expired!\n\nPlease contact 7016797720 to renew.")
        self.root.withdraw()
        self._show_activation_screen()

    def _init_app_interface(self):
        self.root.deiconify()
        self._maximize_window(self.root)
        self._build_ui()
        if self.just_activated: self.just_activated = False
        else: self._run_startup_sync()

    def _build_ui(self):
        # Left Control Panel
        left_panel = tk.Frame(self.root, width=390, bg="#EBF1F6", relief=tk.GROOVE, bd=1)
        left_panel.pack(side=tk.LEFT, fill=tk.Y)
        left_panel.pack_propagate(False)

        # Bottom Frame (Sign Buttons & Status)
        bottom_frame = tk.Frame(left_panel, bg="#EBF1F6", padx=12, pady=8)
        bottom_frame.pack(side=tk.BOTTOM, fill=tk.X)

        self.lbl_status = tk.Label(
            bottom_frame, text=f"Ready [{CURRENT_OS}]", foreground="#059669", bg="#EBF1F6",
            wraplength=360, font=get_sys_font(9, "bold")
        )
        self.lbl_status.pack(pady=(0, 4))

        sign_shadow = tk.Frame(bottom_frame, bg="#065F46", bd=0)
        sign_shadow.pack(fill=tk.X, pady=(0, 4))
        self.btn_sign = ttk.Button(
            sign_shadow, text="✍️ SIGN & SAVE LOCAL DOCUMENT",
            command=self.start_signing, style="Primary3D.TButton"
        )
        self.btn_sign.pack(fill=tk.X, padx=(0, 2), pady=(0, 2), ipady=5)

        batch_shadow = tk.Frame(bottom_frame, bg="#1E3A8A", bd=0)
        batch_shadow.pack(fill=tk.X, pady=(0, 4))
        self.btn_batch = ttk.Button(
            batch_shadow, text="📁 BATCH SIGN FOLDER (ALL PDFs)",
            command=self.open_batch_dialog, style="Batch.TButton"
        )
        self.btn_batch.pack(fill=tk.X, padx=(0, 2), pady=(0, 2))

        disclaimer_text = "Note: Adobe validation status depends on your certificate chain and system trusted roots."
        self.lbl_disclaimer = tk.Label(
            bottom_frame, text=disclaimer_text, foreground="#EF4444", bg="#EBF1F6",
            wraplength=360, font=get_sys_font(7, "italic"), justify="left"
        )
        self.lbl_disclaimer.pack(pady=(4, 0))

        # Top Frame (Cards)
        top_frame = tk.Frame(left_panel, bg="#EBF1F6")
        top_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=12, pady=(8, 0))

        # Brand Header Card
        brand_shadow = tk.Frame(top_frame, bg="#1E3A8A", bd=0)
        brand_shadow.pack(fill=tk.X, pady=(0, 6))
        brand_card = tk.Frame(brand_shadow, bg="#1E40AF", relief=tk.RAISED, bd=2, padx=8, pady=6)
        brand_card.pack(fill=tk.BOTH, expand=True, padx=(0, 2), pady=(0, 2))

        try:
            target_logo = LicenseManager.LICENSE_FILE if os.path.exists(LicenseManager.LICENSE_FILE) else "app_icon.ico"
            if os.path.exists(target_logo):
                logo_pil = Image.open(target_logo)
            else:
                logo_pil = Image.new('RGB', (80, 80), color=(0, 120, 215))
            logo_pil.thumbnail((55, 55), Image.Resampling.LANCZOS)
            self.tk_logo = ImageTk.PhotoImage(logo_pil)
            tk.Label(brand_card, image=self.tk_logo, bg="#1E40AF").pack(side=tk.LEFT, padx=(0, 8))
        except Exception:
            tk.Label(brand_card, text="[ABI]", fg="white", bg="#1E40AF", font=get_sys_font(12, "bold")).pack(side=tk.LEFT, padx=(0, 8))

        brand_text_box = tk.Frame(brand_card, bg="#1E40AF")
        brand_text_box.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tk.Label(brand_text_box, text="A B INFOTECH", font=get_sys_font(14, "bold"), fg="#FFFFFF", bg="#1E40AF").pack(anchor=tk.W)
        tk.Label(brand_text_box, text=f"OFFLINE DIGITAL SIGNER v1.1 [{CURRENT_OS}]", font=get_sys_font(7, "bold"), fg="#93C5FD", bg="#1E40AF").pack(anchor=tk.W)
        tk.Label(brand_text_box, text="🛡️ Token & .P12/.PFX Certificate Engine", font=get_sys_font(7, "bold"), fg="#6EE7B7", bg="#1E40AF").pack(anchor=tk.W)

        # Mode Selector Card
        card_mode = self._create_3d_card(top_frame, shadow_color="#93C5FD", pady=(0, 4))
        tk.Label(card_mode, text="Certificate Source Mode:", font=get_sys_font(9, "bold"), fg="#1E3A8A", bg="#FFFFFF").pack(anchor=tk.W)
        mode_row = tk.Frame(card_mode, bg="#FFFFFF")
        mode_row.pack(fill=tk.X, pady=(2, 2))

        rb_tok = tk.Radiobutton(
            mode_row, text="🔒 USB Token", variable=self.cert_source_var,
            value="token", command=self._on_cert_source_changed,
            bg="#FFFFFF", fg="#1E3A8A", font=get_sys_font(9, "bold"), selectcolor="#DBEAFE", bd=0
        )
        rb_tok.pack(side=tk.LEFT, padx=(0, 10))

        rb_pfx = tk.Radiobutton(
            mode_row, text="📁 .P12 / .PFX File", variable=self.cert_source_var,
            value="pfx", command=self._on_cert_source_changed,
            bg="#FFFFFF", fg="#1E3A8A", font=get_sys_font(9, "bold"), selectcolor="#DBEAFE", bd=0
        )
        rb_pfx.pack(side=tk.LEFT)

        # Token Cards Container
        self.token_panel = tk.Frame(top_frame, bg="#EBF1F6")
        self.token_panel.pack(fill=tk.X)

        card1 = self._create_3d_card(self.token_panel, shadow_color="#93C5FD", pady=(0, 4))
        tk.Label(card1, text="1. Select Token Driver:", font=get_sys_font(9, "bold"), fg="#1E3A8A", bg="#FFFFFF").pack(anchor=tk.W)
        driver_row = tk.Frame(card1, bg="#FFFFFF")
        driver_row.pack(fill=tk.X, pady=(2, 2))
        self.driver_var = tk.StringVar()
        self.driver_combo = ttk.Combobox(driver_row, textvariable=self.driver_var, values=list(DRIVER_MAP.keys()), state="readonly", font=get_sys_font(8))
        self.driver_combo.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        self.driver_combo.bind("<<ComboboxSelected>>", self._on_driver_selected)
        if DRIVER_MAP: self.driver_combo.current(0)
        ttk.Button(driver_row, text="Load Token", style="Tool3D.TButton", command=self.load_certificates).pack(side=tk.RIGHT)

        card2 = self._create_3d_card(self.token_panel, shadow_color="#93C5FD", pady=(0, 4))
        tk.Label(card2, text="2. Select Token Certificate:", font=get_sys_font(9, "bold"), fg="#1E3A8A", bg="#FFFFFF").pack(anchor=tk.W)
        self.cert_combo = ttk.Combobox(card2, state="readonly", font=get_sys_font(8))
        self.cert_combo.pack(fill=tk.X, pady=(2, 2))

        card3 = self._create_3d_card(self.token_panel, shadow_color="#93C5FD", pady=(0, 4))
        tk.Label(card3, text="3. Hardware Token PIN:", font=get_sys_font(9, "bold"), fg="#1E3A8A", bg="#FFFFFF").pack(anchor=tk.W)
        self.pin_var = tk.StringVar()
        ttk.Entry(card3, textvariable=self.pin_var, show="*").pack(fill=tk.X, pady=(2, 2))

        # PFX Cards Container
        self.pfx_panel = tk.Frame(top_frame, bg="#EBF1F6")

        card_p1 = self._create_3d_card(self.pfx_panel, shadow_color="#93C5FD", pady=(0, 4))
        tk.Label(card_p1, text="1. Select .P12 / .PFX File:", font=get_sys_font(9, "bold"), fg="#1E3A8A", bg="#FFFFFF").pack(anchor=tk.W)
        pfx_file_row = tk.Frame(card_p1, bg="#FFFFFF")
        pfx_file_row.pack(fill=tk.X, pady=(2, 2))
        self.pfx_path_entry = ttk.Entry(pfx_file_row, textvariable=self.pfx_path_var, font=get_sys_font(8))
        self.pfx_path_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        ttk.Button(pfx_file_row, text="Browse...", style="Tool3D.TButton", command=self.browse_pfx_file).pack(side=tk.RIGHT)

        card_p2 = self._create_3d_card(self.pfx_panel, shadow_color="#93C5FD", pady=(0, 4))
        tk.Label(card_p2, text="2. Certificate Password:", font=get_sys_font(9, "bold"), fg="#1E3A8A", bg="#FFFFFF").pack(anchor=tk.W)
        pfx_pass_row = tk.Frame(card_p2, bg="#FFFFFF")
        pfx_pass_row.pack(fill=tk.X, pady=(2, 2))
        self.pfx_pass_entry = ttk.Entry(pfx_pass_row, textvariable=self.pfx_pass_var, show="*")
        self.pfx_pass_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        ttk.Button(pfx_pass_row, text="Verify", style="Tool3D.TButton", command=lambda: self.verify_pfx_certificate(silent=False)).pack(side=tk.RIGHT)

        card_p3 = self._create_3d_card(self.pfx_panel, shadow_color="#93C5FD", pady=(0, 4))
        tk.Label(card_p3, text="3. Certificate Status:", font=get_sys_font(9, "bold"), fg="#1E3A8A", bg="#FFFFFF").pack(anchor=tk.W)
        self.lbl_pfx_details = tk.Label(
            card_p3, text="Select .p12/.pfx file & enter password to unlock.",
            font=get_sys_font(8), fg="#64748B", bg="#F8FAFC",
            relief=tk.SUNKEN, bd=1, padx=6, pady=3, justify="left", anchor=tk.W
        )
        self.lbl_pfx_details.pack(fill=tk.X, pady=(2, 2))

        # Card 4: Signature Area & Checkbox (Prominent in Side Panel)
        card4 = self._create_3d_card(top_frame, shadow_color="#93C5FD", pady=(0, 4))
        self.card4_shadow = card4.shadow

        card4_header = tk.Frame(card4, bg="#FFFFFF")
        card4_header.pack(fill=tk.X)
        tk.Label(card4_header, text="4. Selected Signature Area:", font=get_sys_font(9, "bold"), fg="#1E3A8A", bg="#FFFFFF").pack(side=tk.LEFT)

        # Prominent Checkbox in Side Panel
        self.chk_all_pages = tk.Checkbutton(
            card4, text="☑️ Apply area to ALL pages (Sign all pages)",
            variable=self.all_pages_var, command=self.on_all_pages_toggle,
            bg="#FFFFFF", fg="#047857", activebackground="#FFFFFF", activeforeground="#065F46",
            selectcolor="#D1FAE5", font=get_sys_font(8, "bold"), cursor="hand2", bd=0
        )
        self.chk_all_pages.pack(anchor=tk.W, pady=(2, 3))

        self.area_container = tk.Frame(card4, bg="#F8FAFC", relief=tk.SUNKEN, bd=1, height=44)
        self.area_container.pack(fill=tk.X, pady=(1, 2))
        self.area_container.pack_propagate(False)

        self.lbl_no_areas = tk.Label(
            self.area_container, text="No area selected yet.\nClick '✏️ Draw Signature Box' or open a PDF.",
            bg="#F8FAFC", fg="#64748B", font=get_sys_font(8), justify="center"
        )
        self.lbl_no_areas.pack(expand=True)

        # Set initial visibility based on default mode
        self._on_cert_source_changed()

        # Right Panel (PDF Viewport)
        right_panel = tk.Frame(self.root, bg="#CBD5E1")
        right_panel.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        toolbar = ttk.Frame(right_panel, padding="6", style="Toolbar.TFrame")
        toolbar.pack(fill=tk.X)

        ttk.Button(toolbar, text="📂 Open PDF", style="Tool3D.TButton", command=self.open_pdf).pack(side=tk.LEFT, padx=3)
        self.btn_clear_pdf = ttk.Button(toolbar, text="🗑️ Clear PDF", style="Tool3D.TButton", command=self.clear_pdf, state=tk.DISABLED)
        self.btn_clear_pdf.pack(side=tk.LEFT, padx=3)

        self.btn_prev = ttk.Button(toolbar, text="◀ Prev", style="Tool3D.TButton", command=self.prev_page, state=tk.DISABLED)
        self.btn_prev.pack(side=tk.LEFT, padx=3)
        self.lbl_page = tk.Label(toolbar, text="Page 0 / 0", font=get_sys_font(9, "bold"), bg="#F1F5F9", fg="#1E3A8A", relief=tk.SUNKEN, bd=1, padx=8, pady=3)
        self.lbl_page.pack(side=tk.LEFT, padx=6)
        self.btn_next = ttk.Button(toolbar, text="Next ▶", style="Tool3D.TButton", command=self.next_page, state=tk.DISABLED)
        self.btn_next.pack(side=tk.LEFT, padx=3)

        self.btn_batch_tool = ttk.Button(toolbar, text="📁 Batch Sign Folder", style="Batch.TButton", command=self.open_batch_dialog)
        self.btn_batch_tool.pack(side=tk.LEFT, padx=8)

        ttk.Button(toolbar, text="🧹 Clear Boxes", style="Tool3D.TButton", command=self.clear_boxes).pack(side=tk.RIGHT, padx=3)
        self.btn_draw = ttk.Button(toolbar, text="✏️ Draw Signature Box", style="Draw3D.TButton", command=self.toggle_draw_mode, state=tk.DISABLED)
        self.btn_draw.pack(side=tk.RIGHT, padx=3)

        canvas_frame = tk.Frame(right_panel, bg="#CBD5E1", relief=tk.SUNKEN, bd=2)
        canvas_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        self.canvas_vscroll = ttk.Scrollbar(canvas_frame, orient=tk.VERTICAL)
        self.canvas_vscroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas_hscroll = ttk.Scrollbar(canvas_frame, orient=tk.HORIZONTAL)
        self.canvas_hscroll.pack(side=tk.BOTTOM, fill=tk.X)

        self.canvas = tk.Canvas(canvas_frame, bg="#94A3B8", xscrollcommand=self.canvas_hscroll.set, yscrollcommand=self.canvas_vscroll.set, highlightthickness=0)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.canvas_vscroll.config(command=self.canvas.yview)
        self.canvas_hscroll.config(command=self.canvas.xview)

        self.canvas.bind("<ButtonPress-1>", self.on_mouse_down)
        self.canvas.bind("<B1-Motion>", self.on_mouse_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_mouse_up)

    def _on_cert_source_changed(self):
        mode = self.cert_source_var.get()
        if mode == "token":
            self.pfx_panel.pack_forget()
            self.token_panel.pack(fill=tk.X, before=self.card4_shadow)
            self.lbl_status.config(text=f"Switched to Hardware Token Mode [{CURRENT_OS}].", foreground="#1E3A8A")
        else:
            self.token_panel.pack_forget()
            self.pfx_panel.pack(fill=tk.X, before=self.card4_shadow)
            self.lbl_status.config(text="Switched to .P12 / .PFX Certificate File Mode.", foreground="#0F766E")

    def _on_driver_selected(self, event=None):
        selected = self.driver_var.get()
        if selected == "[Browse Custom Driver Library...]":
            if CURRENT_OS == 'Windows':
                ftypes = [("PKCS#11 Library (*.dll)", "*.dll"), ("All Files", "*.*")]
            elif CURRENT_OS == 'Darwin':
                ftypes = [("PKCS#11 Dynamic Library (*.dylib, *.so)", "*.dylib;*.so"), ("All Files", "*.*")]
            else:
                ftypes = [("PKCS#11 Shared Object (*.so)", "*.so*"), ("All Files", "*.*")]

            custom_path = filedialog.askopenfilename(title=f"Select Token Driver Library ({CURRENT_OS})", filetypes=ftypes)
            if custom_path:
                label_name = f"Custom: {os.path.basename(custom_path)}"
                DRIVER_MAP[label_name] = custom_path
                all_keys = list(DRIVER_MAP.keys())
                self.driver_combo['values'] = all_keys
                self.driver_var.set(label_name)
                self.lbl_status.config(text=f"Selected custom driver: {os.path.basename(custom_path)}", foreground="green")

    def browse_pfx_file(self):
        ftypes = [("PKCS#12 Certificates (*.p12;*.pfx)", "*.p12;*.pfx;*.P12;*.PFX"), ("All Files", "*.*")]
        path = filedialog.askopenfilename(title="Select .P12 / .PFX Certificate File", filetypes=ftypes)
        if path:
            self.pfx_path_var.set(path)
            self.verify_pfx_certificate(silent=False)

    def verify_pfx_certificate(self, silent=False):
        path = self.pfx_path_var.get().strip()
        pwd = self.pfx_pass_var.get()
        if not path:
            if not silent: messagebox.showerror("Error", "Please select a .p12/.pfx file first.")
            return False

        try:
            signer, cert_parsed = load_pfx_signer_and_cert(path, pwd)
            cn_name = "Unknown"
            try:
                cn_attrs = cert_parsed.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)
                if cn_attrs: cn_name = str(cn_attrs[0].value)
            except Exception: pass

            exp_str = cert_parsed.not_valid_after_utc.strftime('%d-%b-%Y')
            info_text = f"✓ Ready: {cn_name}\nValid Until: {exp_str}"
            self.lbl_pfx_details.config(text=info_text, fg="#059669")
            self.lbl_status.config(text=f"PFX Unlocked: {cn_name}", foreground="#059669")
            return True
        except Exception as e:
            err = str(e)
            self.lbl_pfx_details.config(text=f"❌ Verification failed:\n{err}", fg="#EF4444")
            if not silent: messagebox.showerror("PFX Error", err)
            return False

    def load_certificates(self):
        driver = DRIVER_MAP.get(self.driver_var.get())
        if not driver or driver == "__CUSTOM__":
            self._on_driver_selected()
            driver = DRIVER_MAP.get(self.driver_var.get())
            if not driver or driver == "__CUSTOM__": return

        if not os.path.exists(driver):
            messagebox.showerror("Driver Missing", f"Could not find driver library at:\n\n{driver}\n\nPlease install your Token middleware or browse for the correct library.")
            return

        try:
            self.loaded_certs = []
            lib = pkcs11.lib(driver)
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
                            except x509.ExtensionNotFound: pass

                            cn = "Unknown"
                            for attr in cert.subject:
                                if attr.oid == x509.NameOID.COMMON_NAME:
                                    cn = attr.value; break

                            self.loaded_certs.append({
                                'alias': str(cert.serial_number),
                                'cn': cn,
                                'source': token.label
                            })
                except Exception: continue

            self.cert_combo['values'] = [f"{c['cn']} ({c['source']})" for c in self.loaded_certs]
            if self.loaded_certs: self.cert_combo.current(0)
            self.lbl_status.config(text="Hardware Token certificates loaded.", foreground="green")

        except Exception as e:
            err_msg = str(e)
            if not err_msg.strip(): err_msg = "Please ensure USB Token is connected."
            ask = messagebox.askyesno("Hardware Token Not Found", f"{err_msg}\n\nWould you like to use a .p12 / .pfx file to sign instead?", parent=self.root)
            if ask:
                self.cert_source_var.set("pfx")
                self._on_cert_source_changed()

    def update_area_list_ui(self):
        for widget in self.area_container.winfo_children():
            widget.destroy()

        if not self.signature_areas:
            self.lbl_no_areas = tk.Label(
                self.area_container, text="No area selected yet.\nClick '✏️ Draw Signature Box' or open a PDF.",
                bg="#F8FAFC", fg="#64748B", font=get_sys_font(8), justify="center"
            )
            self.lbl_no_areas.pack(expand=True)
            return

        if self.all_pages_var.get() and len(self.signature_areas) > 0:
            first = self.signature_areas[0]
            desc = f"W: {int(first['width'])} x H: {int(first['height'])}"
            tk.Label(
                self.area_container, text=f"✓ All Pages ({len(self.signature_areas)} pages) — {desc}",
                bg="#F8FAFC", fg="#059669", font=get_sys_font(8, "bold"), justify="left"
            ).pack(anchor=tk.W, padx=6, pady=2)
        else:
            for idx, area in enumerate(self.signature_areas):
                txt = f"[{idx+1}] Page {area['page']+1} — W: {int(area['width'])} x H: {int(area['height'])}"
                tk.Label(self.area_container, text=txt, bg="#F8FAFC", fg="#1E3A8A", font=get_sys_font(8)).pack(anchor=tk.W, padx=6, pady=1)

    def on_all_pages_toggle(self):
        if self.all_pages_var.get():
            if self.pdf_doc and self.signature_areas:
                self._apply_area_to_all_pages_memory()
                self.update_area_list_ui()
                self.render_page()
            self.lbl_status.config(text="Apply to all pages enabled. All pages will be signed.", foreground="#059669")
        else:
            if self.pdf_doc and self.signature_areas:
                self.signature_areas = [a for a in self.signature_areas if a['page'] == self.current_page]
                self.update_area_list_ui()
                self.render_page()
            self.lbl_status.config(text="Single page signature mode.", foreground="#1E3A8A")

    def _apply_area_to_all_pages_memory(self):
        if not self.pdf_doc or not self.signature_areas: return
        total_pages = len(self.pdf_doc)
        template = self.signature_areas[0]

        new_areas = []
        for p in range(total_pages):
            rect = self.pdf_doc[p].rect
            pw, ph = float(rect.width), float(rect.height)
            ax = float(template['x'])
            ay = float(template['y'])
            aw = min(float(template['width']), pw - ax)
            ah = min(float(template['height']), ph - ay)
            cx1 = template.get('c_x1', ax * self.scale)
            cy1 = template.get('c_y1', (ph - (ay + ah)) * self.scale)
            cx2 = template.get('c_x2', (ax + aw) * self.scale)
            cy2 = template.get('c_y2', (ph - ay) * self.scale)

            new_areas.append({
                'page': p,
                'x': ax, 'y': ay,
                'width': aw, 'height': ah,
                'c_x1': cx1, 'c_y1': cy1,
                'c_x2': cx2, 'c_y2': cy2
            })
        self.signature_areas = new_areas

    def open_pdf(self):
        path = filedialog.askopenfilename(filetypes=[("PDF Files", "*.pdf")])
        if not path: return
        self.input_pdf_path = path
        self.pdf_doc = fitz.open(path)
        self.current_page = 0
        self.signature_areas.clear()
        self.update_area_list_ui()

        for btn in [self.btn_prev, self.btn_next, self.btn_draw, self.btn_sign, self.btn_clear_pdf]:
            btn.config(state=tk.NORMAL)
        self.render_page()

    def clear_pdf(self):
        self.pdf_doc = None
        self.input_pdf_path = None
        self.current_page = 0
        self.signature_areas.clear()
        self.update_area_list_ui()
        self.canvas.delete("all")
        self.lbl_page.config(text="Page 0 / 0")
        for btn in [self.btn_prev, self.btn_next, self.btn_draw, self.btn_sign, self.btn_clear_pdf]:
            btn.config(state=tk.DISABLED)

    def render_page(self):
        if not self.pdf_doc: return
        self.lbl_page.config(text=f"Page {self.current_page + 1} / {len(self.pdf_doc)}")
        pix = self.pdf_doc[self.current_page].get_pixmap(matrix=fitz.Matrix(self.scale, self.scale))
        self.tk_img = ImageTk.PhotoImage(Image.frombytes("RGB", [pix.width, pix.height], pix.samples))

        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor=tk.NW, image=self.tk_img)
        self.canvas.config(scrollregion=(0, 0, pix.width, pix.height))

        for idx, area in enumerate(self.signature_areas):
            if area['page'] == self.current_page:
                self.canvas.create_rectangle(
                    area['c_x1'], area['c_y1'], area['c_x2'], area['c_y2'],
                    outline="#0F766E", width=2, fill="#CCFBF1", stipple="gray25"
                )
                self.canvas.create_text(
                    area['c_x1'] + 6, area['c_y1'] + 6, anchor=tk.NW,
                    text=f"🔏 Signature (Box {idx+1})", fill="#0F766E", font=get_sys_font(9, "bold")
                )

    def prev_page(self):
        if self.current_page > 0:
            self.current_page -= 1
            self.render_page()

    def next_page(self):
        if self.current_page < len(self.pdf_doc) - 1:
            self.current_page += 1
            self.render_page()

    def toggle_draw_mode(self):
        self.drawing_mode = not self.drawing_mode
        self.canvas.config(cursor="crosshair" if self.drawing_mode else "")
        self.btn_draw.config(text="❌ Cancel Drawing" if self.drawing_mode else "✏️ Draw Signature Box")

    def on_mouse_down(self, event):
        if not self.drawing_mode: return
        self.start_x = self.canvas.canvasx(event.x)
        self.start_y = self.canvas.canvasy(event.y)
        self.temp_rect = self.canvas.create_rectangle(
            self.start_x, self.start_y, self.start_x, self.start_y,
            outline="#0F766E", width=2, dash=(4, 4)
        )

    def on_mouse_drag(self, event):
        if not self.drawing_mode or not self.temp_rect: return
        self.canvas.coords(self.temp_rect, self.start_x, self.start_y, self.canvas.canvasx(event.x), self.canvas.canvasy(event.y))

    def on_mouse_up(self, event):
        if not self.drawing_mode or not self.temp_rect: return
        cur_x, cur_y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        x1, x2 = sorted([self.start_x, cur_x])
        y1, y2 = sorted([self.start_y, cur_y])

        self.canvas.delete(self.temp_rect)
        self.temp_rect = None
        self.toggle_draw_mode()

        if (x2 - x1) < 15 or (y2 - y1) < 15: return

        real_h = self.pdf_doc[self.current_page].rect.height
        pdf_x1, pdf_y1, pdf_x2, pdf_y2 = x1/self.scale, y1/self.scale, x2/self.scale, y2/self.scale

        new_area = {
            'page': self.current_page,
            'x': pdf_x1, 'y': real_h - pdf_y2,
            'width': pdf_x2 - pdf_x1, 'height': (real_h - pdf_y1) - (real_h - pdf_y2),
            'c_x1': x1, 'c_y1': y1, 'c_x2': x2, 'c_y2': y2
        }

        if self.all_pages_var.get():
            self.signature_areas = [new_area]
            self._apply_area_to_all_pages_memory()
        else:
            self.signature_areas.append(new_area)

        self.update_area_list_ui()
        self.render_page()

    def clear_boxes(self):
        if self.all_pages_var.get():
            self.signature_areas.clear()
        else:
            self.signature_areas = [a for a in self.signature_areas if a['page'] != self.current_page]
        self.update_area_list_ui()
        self.render_page()

    def start_signing(self):
        if LicenseManager.check_expiration_local() == "expired":
            messagebox.showerror("License Expired", "Your software license has expired!\n\nPlease contact 7016797720 to renew.")
            self._show_activation_screen()
            return

        if not self.input_pdf_path: return messagebox.showerror("Error", "Please open a PDF.")
        if self.all_pages_var.get():
            self._apply_area_to_all_pages_memory()
        if not self.signature_areas: return messagebox.showwarning("Warning", "Draw a signature box on the document.")

        mode = self.cert_source_var.get()
        if mode == "pfx":
            pfx_path = self.pfx_path_var.get().strip()
            pfx_pass = self.pfx_pass_var.get()
            if not pfx_path or not os.path.exists(pfx_path):
                return messagebox.showerror("Error", "Please select a valid .p12/.pfx certificate file.", parent=self.root)
            if not self.verify_pfx_certificate(silent=True):
                return messagebox.showerror("Password Required", "Please enter the correct password for the .p12/.pfx file.", parent=self.root)

            out_path = filedialog.asksaveasfilename(defaultextension=".pdf", filetypes=[("PDF", "*.pdf")])
            if not out_path: return

            self.lbl_status.config(text="Signing with .p12/.pfx certificate...", foreground="blue")
            self.btn_sign.config(state=tk.DISABLED)

            thread = threading.Thread(
                target=self._signing_worker_pfx,
                args=(self.input_pdf_path, out_path, pfx_path, pfx_pass, list(self.signature_areas))
            )
            thread.start()
        else:
            pin = self.pin_var.get()
            if not pin: return messagebox.showerror("Error", "Enter Token PIN.")
            idx = self.cert_combo.current()
            if idx < 0: return messagebox.showerror("Error", "Select a certificate.")

            out_path = filedialog.asksaveasfilename(defaultextension=".pdf", filetypes=[("PDF", "*.pdf")])
            if not out_path: return

            serial_number = self.loaded_certs[idx]['alias']
            driver_path = DRIVER_MAP.get(self.driver_var.get())
            self.lbl_status.config(text="Signing with USB Token...", foreground="blue")
            self.btn_sign.config(state=tk.DISABLED)

            thread = threading.Thread(
                target=self._signing_worker_token,
                args=(self.input_pdf_path, out_path, pin, serial_number, driver_path, list(self.signature_areas))
            )
            thread.start()

    def _signing_worker_pfx(self, in_path, out_path, pfx_path, pfx_pass, areas):
        try:
            with open(in_path, 'rb') as f: pdf_data = f.read()
            signer, cert_parsed = load_pfx_signer_and_cert(pfx_path, pfx_pass)
            try: tst_client = HTTPTimeStamper(TSA_URL)
            except Exception: tst_client = None

            signed_bytes = sign_pdf_stream(pdf_data, areas, signer, cert_parsed, tst_client)
            with open(out_path, 'wb') as f_out: f_out.write(signed_bytes)

            validation_symbol, validation_msg = self._validate_output(out_path)
            self.root.after(0, lambda: self.lbl_status.config(text=f"Successfully Signed with PFX! {validation_symbol}", foreground="green"))
            self.root.after(0, lambda: messagebox.showinfo("Validation Status", f"File saved to:\n{out_path}\n\nLocal Pre-Check:\n{validation_symbol} {validation_msg}"))
        except Exception as e:
            error_msg = str(e)
            logger.error(f"LOCAL PFX SIGNING ERROR: {error_msg}\n{traceback.format_exc()}")
            self.root.after(0, lambda: self.lbl_status.config(text="Signing Failed. ❌", foreground="red"))
            self.root.after(0, lambda msg=error_msg: messagebox.showerror("Signing Error", msg))
        finally:
            self.root.after(0, lambda: self.btn_sign.config(state=tk.NORMAL))

    def _signing_worker_token(self, in_path, out_path, pin, serial_number, driver_path, areas):
        try:
            with open(in_path, 'rb') as f: pdf_data = f.read()
            lib = pkcs11.lib(driver_path)
            token = None
            for slot in lib.get_slots(token_present=True):
                try: token = slot.get_token(); break
                except Exception: continue

            if not token: raise ValueError("Hardware Token not found. Ensure USB token is plugged in.")

            with token.open(user_pin=pin) as session:
                cert_id, cert_parsed = None, None
                for obj in session.get_objects({pkcs11.Attribute.CLASS: pkcs11.ObjectClass.CERTIFICATE}):
                    c = x509.load_der_x509_certificate(obj[pkcs11.Attribute.VALUE])
                    if str(c.serial_number) == str(serial_number):
                        cert_id, cert_parsed = obj[pkcs11.Attribute.ID], c
                        break

                if not cert_id: raise ValueError("Certificate not found on token. Check PIN.")
                signer = PKCS11Signer(pkcs11_session=session, cert_id=cert_id)

                try: tst_client = HTTPTimeStamper(TSA_URL)
                except Exception: tst_client = None

                signed_bytes = sign_pdf_stream(pdf_data, areas, signer, cert_parsed, tst_client)
                with open(out_path, 'wb') as f_out: f_out.write(signed_bytes)

            validation_symbol, validation_msg = self._validate_output(out_path)
            self.root.after(0, lambda: self.lbl_status.config(text=f"Successfully Signed with Token! {validation_symbol}", foreground="green"))
            self.root.after(0, lambda: messagebox.showinfo("Validation Status", f"File saved to:\n{out_path}\n\nLocal Pre-Check:\n{validation_symbol} {validation_msg}"))
        except Exception as e:
            error_msg = str(e)
            logger.error(f"LOCAL TOKEN SIGNING ERROR: {error_msg}\n{traceback.format_exc()}")
            self.root.after(0, lambda: self.lbl_status.config(text="Signing Failed. ❌", foreground="red"))
            self.root.after(0, lambda msg=error_msg: messagebox.showerror("Signing Error", msg))
        finally:
            self.root.after(0, lambda: self.btn_sign.config(state=tk.NORMAL))

    def _validate_output(self, out_path):
        try:
            with open(out_path, 'rb') as doc_to_verify:
                reader = PdfFileReader(doc_to_verify)
                if reader.embedded_signatures:
                    sig_obj = reader.embedded_signatures[-1]
                    status = validate_pdf_signature(sig_obj)
                    if status.intact:
                        return "🟢", "Cryptographically Valid & Intact"
                    else:
                        return "❌", "Signature Invalid or File Corrupted"
                else:
                    return "❌", "No signature found in output file."
        except Exception as val_e:
            logger.warning(f"Local validation error: {val_e}")
            return "❓", f"Validation Note: {str(val_e)}"

    def open_batch_dialog(self):
        BatchSignDialog(self.root, self)

# ==========================================
# 4.5. BATCH SIGN FOLDER DIALOG (CROSS-PLATFORM)
# ==========================================
class BatchSignDialog:
    def __init__(self, parent, main_app):
        self.dialog = tk.Toplevel(parent)
        self.dialog.title(f"📁 Batch Sign PDF Folder [{CURRENT_OS}]")
        self.dialog.geometry("640x520")
        self.dialog.transient(parent)
        self.dialog.grab_set()

        self.main_app = main_app
        self.in_dir_var = tk.StringVar()
        self.out_dir_var = tk.StringVar()

        self._build_ui()

    def _build_ui(self):
        f = ttk.Frame(self.dialog, padding="14")
        f.pack(fill=tk.BOTH, expand=True)

        ttk.Label(f, text="Batch Sign All PDFs in Folder", font=get_sys_font(12, "bold"), foreground="#1E40AF").pack(anchor=tk.W, pady=(0, 10))

        # Input Directory
        ttk.Label(f, text="1. Select Input Folder (containing PDF files):", font=get_sys_font(9, "bold")).pack(anchor=tk.W)
        in_row = ttk.Frame(f)
        in_row.pack(fill=tk.X, pady=(3, 10))
        ttk.Entry(in_row, textvariable=self.in_dir_var, font=get_sys_font(8)).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        ttk.Button(in_row, text="Browse...", style="Tool3D.TButton", command=self._browse_in).pack(side=tk.RIGHT)

        # Output Directory
        ttk.Label(f, text="2. Select Output Folder (where signed PDFs will be saved):", font=get_sys_font(9, "bold")).pack(anchor=tk.W)
        out_row = ttk.Frame(f)
        out_row.pack(fill=tk.X, pady=(3, 10))
        ttk.Entry(out_row, textvariable=self.out_dir_var, font=get_sys_font(8)).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        ttk.Button(out_row, text="Browse...", style="Tool3D.TButton", command=self._browse_out).pack(side=tk.RIGHT)

        # Info Box
        info_frame = tk.Frame(f, bg="#F0FDF4", relief=tk.SUNKEN, bd=1, padx=8, pady=8)
        info_frame.pack(fill=tk.X, pady=(5, 10))
        tk.Label(
            info_frame,
            text="ℹ️ Every PDF in the input folder will be signed across all pages\nusing the signature area defined on the main viewer screen.",
            bg="#F0FDF4", fg="#065F46", font=get_sys_font(8), justify="left"
        ).pack(anchor=tk.W)

        # Progress Display
        self.prog_var = tk.DoubleVar()
        self.prog_bar = ttk.Progressbar(f, variable=self.prog_var, maximum=100)
        self.prog_bar.pack(fill=tk.X, pady=(10, 5))

        self.lbl_progress = tk.Label(f, text="Ready to start batch processing.", font=get_sys_font(8), fg="#475569")
        self.lbl_progress.pack(anchor=tk.W)

        # Action Buttons
        btn_row = ttk.Frame(f)
        btn_row.pack(side=tk.BOTTOM, fill=tk.X, pady=(10, 0))
        self.btn_start = ttk.Button(btn_row, text="🚀 START BATCH SIGNING", style="Batch.TButton", command=self._start_batch)
        self.btn_start.pack(side=tk.RIGHT, padx=4)
        ttk.Button(btn_row, text="Close", style="Tool3D.TButton", command=self.dialog.destroy).pack(side=tk.RIGHT)

    def _browse_in(self):
        d = filedialog.askdirectory(title="Select Input Folder Containing PDFs")
        if d:
            self.in_dir_var.set(d)
            if not self.out_dir_var.get():
                self.out_dir_var.set(os.path.join(d, "Signed_PDFs"))

    def _browse_out(self):
        d = filedialog.askdirectory(title="Select Output Folder")
        if d: self.out_dir_var.set(d)

    def _start_batch(self):
        in_dir = self.in_dir_var.get().strip()
        out_dir = self.out_dir_var.get().strip()
        if not in_dir or not os.path.exists(in_dir):
            return messagebox.showerror("Error", "Please select a valid input directory.", parent=self.dialog)
        if not out_dir:
            return messagebox.showerror("Error", "Please specify an output directory.", parent=self.dialog)
        if not self.main_app.signature_areas:
            return messagebox.showwarning("Warning", "Please draw a signature box on the main document viewer first.", parent=self.dialog)

        os.makedirs(out_dir, exist_ok=True)
        pdf_files = [f for f in os.listdir(in_dir) if f.lower().endswith('.pdf')]
        if not pdf_files:
            return messagebox.showinfo("No PDFs", "No PDF files were found in the selected input directory.", parent=self.dialog)

        self.btn_start.config(state=tk.DISABLED)
        thread = threading.Thread(target=self._batch_worker, args=(in_dir, out_dir, pdf_files))
        thread.start()

    def _batch_worker(self, in_dir, out_dir, pdf_files):
        total = len(pdf_files)
        success_cnt, fail_cnt = 0, 0
        template_area = self.main_app.signature_areas[0]

        mode = self.main_app.cert_source_var.get()
        signer, cert_parsed = None, None
        session, token = None, None

        try:
            if mode == "pfx":
                pfx_path = self.main_app.pfx_path_var.get().strip()
                pfx_pass = self.main_app.pfx_pass_var.get()
                signer, cert_parsed = load_pfx_signer_and_cert(pfx_path, pfx_pass)
            else:
                driver_path = DRIVER_MAP.get(self.main_app.driver_var.get())
                pin = self.main_app.pin_var.get()
                idx = self.main_app.cert_combo.current()
                serial_number = self.main_app.loaded_certs[idx]['alias']

                lib = pkcs11.lib(driver_path)
                for slot in lib.get_slots(token_present=True):
                    try: token = slot.get_token(); break
                    except Exception: continue
                session = token.open(user_pin=pin)
                cert_id = None
                for obj in session.get_objects({pkcs11.Attribute.CLASS: pkcs11.ObjectClass.CERTIFICATE}):
                    c = x509.load_der_x509_certificate(obj[pkcs11.Attribute.VALUE])
                    if str(c.serial_number) == str(serial_number):
                        cert_id, cert_parsed = obj[pkcs11.Attribute.ID], c
                        break
                signer = PKCS11Signer(pkcs11_session=session, cert_id=cert_id)

            try: tst_client = HTTPTimeStamper(TSA_URL)
            except Exception: tst_client = None

            for i, fname in enumerate(pdf_files):
                in_path = os.path.join(in_dir, fname)
                out_path = os.path.join(out_dir, fname)
                self.dialog.after(0, lambda f=fname, n=i+1: self.lbl_progress.config(text=f"Signing ({n}/{total}): {f}"))

                try:
                    with open(in_path, 'rb') as f_in: pdf_data = f_in.read()
                    doc = fitz.open(stream=pdf_data, filetype="pdf")
                    n_pages = len(doc)
                    areas = []
                    for p in range(n_pages):
                        pw, ph = float(doc[p].rect.width), float(doc[p].rect.height)
                        ax = float(template_area['x'])
                        ay = float(template_area['y'])
                        aw = min(float(template_area['width']), pw - ax)
                        ah = min(float(template_area['height']), ph - ay)
                        areas.append({'page': p, 'x': ax, 'y': ay, 'width': aw, 'height': ah})
                    doc.close()

                    signed_bytes = sign_pdf_stream(pdf_data, areas, signer, cert_parsed, tst_client)
                    with open(out_path, 'wb') as f_out: f_out.write(signed_bytes)
                    success_cnt += 1
                except Exception as e:
                    logger.error(f"Batch signing error on {fname}: {e}")
                    fail_cnt += 1

                prog = ((i + 1) / total) * 100.0
                self.dialog.after(0, lambda p=prog: self.prog_var.set(p))

            if session:
                try: session.close()
                except Exception: pass

            msg = f"Batch Signing Complete!\n\n✓ Successfully Signed: {success_cnt}\n❌ Failed: {fail_cnt}\nSaved in: {out_dir}"
            self.dialog.after(0, lambda: messagebox.showinfo("Batch Finished", msg, parent=self.dialog))
            self.dialog.after(0, lambda: self.lbl_progress.config(text=f"Complete! Signed {success_cnt}/{total} files."))

        except Exception as batch_err:
            logger.error(f"Batch worker setup error: {batch_err}")
            self.dialog.after(0, lambda err=str(batch_err): messagebox.showerror("Batch Error", err, parent=self.dialog))
        finally:
            self.dialog.after(0, lambda: self.btn_start.config(state=tk.NORMAL))

# ==========================================
# 5. SYSTEM TRAY & BACKGROUND THREAD LOGIC
# ==========================================
server_thread = None
server_instance = None
is_running = False
tray_icon = None

def start_flask_server():
    global server_thread, server_instance, is_running
    if is_running: return

    def run():
        global server_instance
        try:
            logger.info(f"Starting Cross-Platform Bridge on {server_config['host']}:{server_config['port']}")
            server_instance = make_server(server_config['host'], server_config['port'], app)
            server_config['status'] = 'running'
            server_instance.serve_forever()
        except Exception as e:
            logger.error(f"Server crash: {e}")

    server_thread = threading.Thread(target=run, daemon=True)
    server_thread.start()
    is_running = True

def stop_flask_server():
    global server_instance, is_running
    if not is_running: return
    try:
        if server_instance:
            server_instance.shutdown()
            server_instance.server_close()
        is_running = False
        server_config['status'] = 'stopped'
        logger.info("Background Bridge stopped.")
    except Exception as e:
        logger.error(f"Error stopping server: {e}")

def load_icon():
    if os.path.exists(LicenseManager.LICENSE_FILE):
        try: return Image.open(LicenseManager.LICENSE_FILE)
        except Exception: pass

    if hasattr(sys, '_MEIPASS'):
        for icon_name in ['app_icon.ico', 'app_icon.png', 'app_icon.icns']:
            fallback = os.path.join(sys._MEIPASS, icon_name)
            if os.path.exists(fallback):
                try: return Image.open(fallback)
                except Exception: pass

    return Image.new('RGB', (64, 64), color=(0, 120, 215))

def on_open_app(icon, item):
    gui_root.after(0, gui_app.show_window)

def on_start_clicked(icon, item):
    start_flask_server()
    try: icon.notify("Web API Bridge Started.", "A B INFOTECH")
    except Exception: pass

def on_stop_clicked(icon, item):
    stop_flask_server()
    try: icon.notify("Web API Bridge Stopped.", "A B INFOTECH")
    except Exception: pass

def on_quit_clicked(icon, item):
    stop_flask_server()
    icon.stop()
    gui_root.after(0, gui_root.destroy)

def setup_tray():
    global tray_icon
    menu = pystray.Menu(
        item('Open Local Application', on_open_app, default=True),
        pystray.Menu.SEPARATOR,
        item('Start Web Bridge', on_start_clicked),
        item('Stop Web Bridge', on_stop_clicked),
        pystray.Menu.SEPARATOR,
        item('Quit Entirely', on_quit_clicked)
    )
    tray_icon = pystray.Icon("PDF_Signer", load_icon(), f"A B INFOTECH Signer [{CURRENT_OS}]", menu)
    threading.Thread(target=tray_icon.run, daemon=True).start()

# ==========================================
# 6. APPLICATION ENTRY POINT
# ==========================================
if __name__ == '__main__':
    start_flask_server()

    gui_root = tk.Tk()
    gui_app = VisualPdfSignerApp(gui_root)
    setup_tray()
    gui_root.after(200, gui_app.show_window)
    gui_root.mainloop()
