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
def find_epass_driver_path():
    """Dynamically finds the installed ePass2003 PKCS#11 driver on the system."""
    candidates = [
        r"C:\Program Files (x86)\Professional Digisign Private Limited\ProdigiSignStore\Lib\windows\x64\eps2003csp11v2.dll",
        r"C:\Program Files\Professional Digisign Private Limited\ProdigiSignStore\Lib\windows\x64\eps2003csp11v2.dll",
        r"C:\Windows\System32\eps2003csp11v2.dll",
        r"C:\Windows\SysWOW64\eps2003csp11v2.dll",
        r"C:\Windows\System32\eps2003csp11.dll",
        r"C:\Windows\SysWOW64\eps2003csp11.dll",
        r"C:\Program Files\ePass2003-India-Auto\eps2003csp11v2.dll",
        r"C:\Program Files (x86)\ePass2003-India-Auto\eps2003csp11v2.dll",
        r"C:\Program Files\EnterSafe\ePass2003\eps2003csp11v2.dll",
        r"C:\Program Files (x86)\EnterSafe\ePass2003\eps2003csp11v2.dll"
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    try:
        import glob
        ds = glob.glob(r"C:\Windows\System32\DriverStore\FileRepository\eps2003*\eps2003*.dll")
        if ds:
            return ds[0]
    except Exception:
        pass
    return r"C:\Windows\System32\eps2003csp11v2.dll"

CUSTOM_DRIVERS_FILE = os.path.join(APP_DIR, "custom_drivers.json")

def load_custom_drivers():
    """Loads user-defined custom PKCS#11 drivers from custom_drivers.json."""
    if os.path.exists(CUSTOM_DRIVERS_FILE):
        try:
            with open(CUSTOM_DRIVERS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
                elif isinstance(data, list):
                    res = {}
                    for item in data:
                        if isinstance(item, dict) and item.get('name') and item.get('path'):
                            res[item['name']] = item['path']
                    return res
        except Exception as e:
            logger.warning(f"Could not load custom drivers: {e}")
    return {}

def save_custom_drivers(drivers_dict):
    """Saves user-defined custom PKCS#11 drivers to custom_drivers.json."""
    try:
        with open(CUSTOM_DRIVERS_FILE, 'w', encoding='utf-8') as f:
            json.dump(drivers_dict, f, indent=2)
    except Exception as e:
        logger.error(f"Could not save custom drivers: {e}")

def get_platform_drivers():
    """Returns detected PKCS#11 drivers based on current Operating System and user additions."""
    drivers = {}
    if CURRENT_OS == 'Windows':
        is_64bit = sys.maxsize > 2**32
        p64 = r"C:\WINDOWS\system32\cryptoida_pkcs11.dll"
        p32 = r"C:\WINDOWS\sysWOW64\cryptoida_pkcs11.dll"
        mtoken = p64 if (is_64bit and os.path.exists(p64)) else p32

        # Standard Indian Token Drivers (Always present in list)
        drivers["ePass2003 Token"] = find_epass_driver_path()
        drivers["ProxKey India"] = r"C:\Windows\System32\Watchdata\PROXKey CSP India V3.0\wdpkcs.dll"
        drivers["mToken / CryptoID"] = mtoken
        drivers["InnaIT Token"] = r"C:\Windows\System32\InnaITPKCS11Driver.dll"
        
        opensc_win = r"C:\Program Files\OpenSC Project\OpenSC\pkcs11\opensc-pkcs11.dll"
        if os.path.exists(opensc_win):
            drivers["Generic OpenSC"] = opensc_win
            
    elif CURRENT_OS == 'Darwin':  # macOS
        drivers["ePass2003 Token (macOS)"] = "/Library/EnterSafe/libcastle.dylib"
        drivers["ProxKey India (macOS)"] = "/Library/ProxKey/libWD_PKCS11.dylib"
        drivers["mToken / CryptoID (macOS)"] = "/Library/mToken/libcryptoid_pkcs11.dylib"
        drivers["OpenSC (macOS)"] = "/opt/homebrew/lib/opensc-pkcs11.so"
    else:  # Linux
        drivers["ePass2003 Token (Linux)"] = "/usr/lib/libcastle.so.1.0.0"
        drivers["ProxKey India (Linux)"] = "/usr/lib/libWatchdataPKCS11.so"
        drivers["mToken / CryptoID (Linux)"] = "/usr/lib/libcryptoid_pkcs11.so"
        drivers["OpenSC (Linux)"] = "/usr/lib/x86_64-linux-gnu/opensc-pkcs11.so"

    # Merge user's saved custom drivers
    custom = load_custom_drivers()
    for name, path in custom.items():
        drivers[f"⭐ {name}"] = path

    drivers["➕ [Add Custom Token Driver...]"] = "__CUSTOM__"
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

# Global GUI handles for IPC / Single-Instance wakeup
gui_root = None
gui_app = None
_app_single_instance_mutex = None

@app.route('/api/show_window', methods=['GET', 'POST'])
def api_show_window():
    """Brings the existing application window to the foreground when requested."""
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
# 1.8. AUTOSTART & SINGLE-INSTANCE LOGIC
# ==========================================
AUTOSTART_REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_APP_NAME = "Abinfo_Signer_Offline"

def get_app_launch_command(background=True):
    flag = " --background" if background else ""
    if getattr(sys, 'frozen', False):
        exe_path = sys.executable
        return f'"{exe_path}"{flag}'
    else:
        py_exe = sys.executable
        dir_name = os.path.dirname(py_exe)
        pyw = os.path.join(dir_name, "pythonw.exe")
        runner = pyw if os.path.exists(pyw) else py_exe
        script_path = os.path.abspath(__file__)
        return f'"{runner}" "{script_path}"{flag}'

def is_autostart_enabled():
    if CURRENT_OS == 'Windows':
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_REG_KEY, 0, winreg.KEY_READ) as key:
                val, _ = winreg.QueryValueEx(key, AUTOSTART_APP_NAME)
                return bool(val and "--background" in val)
        except Exception:
            return False
    elif CURRENT_OS == 'Linux':
        desktop_file = os.path.expanduser("~/.config/autostart/abinfo_signer.desktop")
        return os.path.exists(desktop_file)
    elif CURRENT_OS == 'Darwin':
        plist_file = os.path.expanduser("~/Library/LaunchAgents/com.abinfotech.signer.plist")
        return os.path.exists(plist_file)
    return False

def set_autostart(enable: bool):
    """Configures system autostart in silent background mode across Windows, Linux, and macOS."""
    cmd = get_app_launch_command(background=True)
    if CURRENT_OS == 'Windows':
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_REG_KEY, 0, winreg.KEY_SET_VALUE) as key:
                if enable:
                    winreg.SetValueEx(key, AUTOSTART_APP_NAME, 0, winreg.REG_SZ, cmd)
                    for old_name in ["ABInfoSigner"]:
                        try: winreg.DeleteValue(key, old_name)
                        except FileNotFoundError: pass
                    logger.info(f"Windows Autostart enabled (silent background): {cmd}")
                else:
                    for name in [AUTOSTART_APP_NAME, "ABInfoSigner"]:
                        try: winreg.DeleteValue(key, name)
                        except FileNotFoundError: pass
                    logger.info("Windows Autostart disabled.")
            return True
        except Exception as e:
            logger.error(f"Failed to set Windows autostart: {e}")
            return False

    elif CURRENT_OS == 'Linux':
        try:
            autostart_dir = os.path.expanduser("~/.config/autostart")
            os.makedirs(autostart_dir, exist_ok=True)
            desktop_file = os.path.join(autostart_dir, "abinfo_signer.desktop")
            if enable:
                content = f"""[Desktop Entry]
Type=Application
Exec={cmd}
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
Name=A B INFOTECH Signer
Comment=Start A B INFOTECH Signer silently in background
"""
                with open(desktop_file, "w") as f:
                    f.write(content)
                logger.info(f"Linux Autostart enabled: {desktop_file}")
            else:
                if os.path.exists(desktop_file):
                    os.remove(desktop_file)
            return True
        except Exception as e:
            logger.error(f"Failed to set Linux autostart: {e}")
            return False

    elif CURRENT_OS == 'Darwin':
        try:
            agents_dir = os.path.expanduser("~/Library/LaunchAgents")
            os.makedirs(agents_dir, exist_ok=True)
            plist_file = os.path.join(agents_dir, "com.abinfotech.signer.plist")
            if enable:
                exe_part = sys.executable if getattr(sys, 'frozen', False) else os.path.abspath(__file__)
                content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.abinfotech.signer</string>
    <key>ProgramArguments</key>
    <array>
        <string>{exe_part}</string>
        <string>--background</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
</dict>
</plist>
"""
                with open(plist_file, "w") as f:
                    f.write(content)
                logger.info(f"macOS Autostart enabled: {plist_file}")
            else:
                if os.path.exists(plist_file):
                    os.remove(plist_file)
            return True
        except Exception as e:
            logger.error(f"Failed to set macOS autostart: {e}")
            return False

    return False

def check_single_instance_and_bring_to_front(wake_up=True):
    """
    Checks if an instance is already running.
    If wake_up is True, asks the running instance to bring its window to the front.
    Returns True if an instance is already running (caller should exit).
    Returns False if this is the primary instance.
    """
    port = server_config.get('port', 5000)
    ping_url = f"http://127.0.0.1:{port}/api/health"
    show_url = f"http://127.0.0.1:{port}/api/show_window"

    # Step 1: HTTP ping to existing Flask bridge
    try:
        req = urllib.request.Request(ping_url, method='GET')
        with urllib.request.urlopen(req, timeout=0.8) as resp:
            if resp.status == 200:
                if wake_up:
                    try:
                        show_req = urllib.request.Request(show_url, method='POST')
                        urllib.request.urlopen(show_req, timeout=1.0)
                    except Exception:
                        pass
                logger.info("Existing instance detected via HTTP bridge. Exiting duplicate process.")
                return True
    except Exception:
        pass

    # Step 2: Windows Named Mutex
    if CURRENT_OS == 'Windows':
        try:
            mutex_name = "Global\\ABInfo_Signer_SingleInstance_Mutex_v1"
            mutex = ctypes.windll.kernel32.CreateMutexW(None, False, mutex_name)
            last_err = ctypes.windll.kernel32.GetLastError()
            if last_err == 183:  # ERROR_ALREADY_EXISTS
                logger.info("Existing instance detected via Windows Named Mutex.")
                if wake_up:
                    for _ in range(4):
                        time.sleep(0.25)
                        try:
                            show_req = urllib.request.Request(show_url, method='POST')
                            with urllib.request.urlopen(show_req, timeout=0.8) as resp:
                                if resp.status == 200:
                                    break
                        except Exception:
                            pass
                return True
            global _app_single_instance_mutex
            _app_single_instance_mutex = mutex
        except Exception as e:
            logger.warning(f"Windows single-instance mutex check error: {e}")

    return False

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

    subj = cert.subject.rfc4514_string() if cert and hasattr(cert, 'subject') else ""
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

            offset_x = float(page.mediabox.left if hasattr(page.mediabox, 'left') else page.mediaBox.getLowerLeft_x())
            offset_y = float(page.mediabox.bottom if hasattr(page.mediabox, 'bottom') else page.mediaBox.getLowerLeft_y())

            packet = io.BytesIO()
            c = rl_canvas.Canvas(packet, pagesize=(pw, ph))

            for area in p_areas:
                ax = float(area['x']) + offset_x
                ay = float(area['y']) + offset_y
                aw = float(area['width'])
                ah = float(area['height'])

                opts = area.get('opts', {})
                heading = opts.get('heading', "Digitally signed by:")
                extra_line = opts.get('extra_line', "")
                reason_val = opts.get('reason', "")
                location_val = opts.get('location', "")
                show_name = opts.get('show_name', True)
                show_date = opts.get('show_date', True)
                show_reason = opts.get('show_reason', bool(reason_val))
                show_location = opts.get('show_location', bool(location_val))
                show_issuer = opts.get('show_issuer', False)
                show_tick = opts.get('show_tick', True)
                show_border = opts.get('show_border', True)
                logo_path = opts.get('logo_path', None)

                # 3D Drop Shadow & Box Border
                if show_border:
                    c.saveState()
                    c.setFillColor(HexColor('#0F172A'), alpha=0.10)
                    c.roundRect(ax + 2, ay - 2, aw, ah, 6, stroke=0, fill=1)
                    c.restoreState()

                    # Signature Box Background & Border (Clean subtle cyan-white tint)
                    c.saveState()
                    c.setFillColor(HexColor('#EBF6F9'), alpha=0.96)
                    c.setStrokeColor(HexColor('#0E7490'))
                    c.setLineWidth(1.2)
                    c.roundRect(ax, ay, aw, ah, 5, stroke=1, fill=1)
                    c.restoreState()

                # Clip strictly to the box interior so text NEVER bleeds outside
                c.saveState()
                clip_p = c.beginPath()
                clip_p.roundRect(ax, ay, aw, ah, 5)
                c.clipPath(clip_p, stroke=0, fill=0)

                # Custom Logo insertion if specified
                text_offset_x = 0
                if logo_path and os.path.exists(logo_path):
                    try:
                        logo_w = min(aw * 0.28, ah * 0.8)
                        logo_h = min(ah * 0.8, logo_w)
                        c.drawImage(logo_path, ax + 6, ay + (ah - logo_h) / 2, width=logo_w, height=logo_h, preserveAspectRatio=True, mask='auto')
                        text_offset_x = logo_w + 6
                    except Exception as le:
                        logger.warning(f"Error drawing signature logo image: {le}")

                # Prominent Green Right Tick Mark (✔) Watermark on right side
                if show_tick:
                    c.saveState()
                    c.setStrokeColor(HexColor('#10B981'), alpha=0.45)
                    c.setLineWidth(max(ah * 0.10, 3.0))
                    c.setLineCap(1)  # Round cap
                    c.setLineJoin(1) # Round join

                    bx = ax + aw * 0.78
                    by = ay + ah * 0.45
                    bsize = min(ah * 0.65, aw * 0.38)

                    p_bg = c.beginPath()
                    p_bg.moveTo(bx - bsize * 0.44, by - bsize * 0.05)
                    p_bg.lineTo(bx - bsize * 0.12, by - bsize * 0.38)
                    p_bg.lineTo(bx + bsize * 0.44, by + bsize * 0.42)
                    c.drawPath(p_bg, stroke=1, fill=0)
                    c.restoreState()

                # Text Content Lines formatted dynamically
                lines = []
                if heading:
                    lines.append(('HEADER', heading))
                if show_name and cn_name:
                    lines.append(('NAME', f"Name:- {cn_name}"))
                if show_reason and reason_val:
                    lines.append(('TEXT', f"Reason:- {reason_val}"))
                if show_location and location_val:
                    lines.append(('TEXT', f"Location:- {location_val}"))
                if org_name and org_name != "N/A":
                    lines.append(('TEXT', f"Organization:- {org_name}"))
                if unit_name and unit_name != "N/A":
                    lines.append(('TEXT', f"Unit:- {unit_name}"))
                if show_issuer:
                    iss_txt = org_name if org_name != "N/A" else "CCA Licensed CA"
                    lines.append(('TEXT', f"Issuer:- {iss_txt}"))
                if country and country != "N/A":
                    lines.append(('TEXT', f"Country:- {country}"))
                if state and state != "N/A":
                    lines.append(('TEXT', f"State:- {state}"))
                if show_date:
                    lines.append(('DATE', f"Date:- {now_ist}"))
                if extra_line:
                    lines.append(('EXTRA', f"{extra_line}"))

                if not lines:
                    lines = [('HEADER', "Digitally signed"), ('DATE', f"Date:- {now_ist}")]

                n_lines = len(lines)
                pad_x = max(aw * 0.04, 6.0) + text_offset_x
                pad_top = max(ah * 0.06, 3.5)
                pad_bot = max(ah * 0.05, 3.5)
                avail_h = max(ah - pad_top - pad_bot, 8.0)
                avail_w = max(aw - pad_x - max(aw * 0.04, 6.0), 20.0)

                # Dynamic Scaling so ALL lines ALWAYS fit inside the box
                max_fs_from_height = avail_h / float((n_lines - 1) * 1.18 + 1.0)
                max_str_len = max(len(t) for _, t in lines)
                max_fs_from_width = avail_w / float(max_str_len * 0.53)
                fs = min(max_fs_from_height, max_fs_from_width, 10.5)
                fs = max(fs, 2.5)

                line_step = (avail_h - fs) / float(max(n_lines - 1, 1))
                line_step = min(line_step, fs * 1.30)
                total_h = (n_lines - 1) * line_step + fs
                start_y = ay + ah - pad_top - fs - max(0.0, (avail_h - total_h) / 2.0)

                cur_y = start_y
                for ltype, text in lines:
                    if ltype == 'HEADER':
                        c.setFont('Helvetica-Bold', min(fs * 1.05, 11.0))
                        c.setFillColor(HexColor('#0E7490'))
                    elif ltype == 'NAME':
                        c.setFont('Helvetica-Bold', fs)
                        c.setFillColor(HexColor('#0F172A'))
                    elif ltype == 'DATE':
                        c.setFont('Helvetica-Bold', max(fs * 0.95, 2.5))
                        c.setFillColor(HexColor('#1E3A8A'))
                    elif ltype == 'EXTRA':
                        c.setFont('Helvetica-Bold', max(fs * 0.95, 2.5))
                        c.setFillColor(HexColor('#047857'))
                    else:
                        c.setFont('Helvetica', max(fs * 0.95, 2.5))
                        c.setFillColor(HexColor('#334155'))

                    c.drawString(ax + pad_x, cur_y, text)
                    cur_y -= line_step

                c.restoreState() # Restore from clipPath

            c.save(); packet.seek(0)
            overlay_page = PdfReader(packet).pages[0] if hasattr(PdfReader(packet), 'pages') else PdfReader(packet).getPage(0)

            if hasattr(page, 'merge_page'): page.merge_page(overlay_page)
            else: page.mergePage(overlay_page)

        if hasattr(writer, 'add_page'): writer.add_page(page)
        else: writer.addPage(page)

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()

def sign_pdf_stream(pdf_bytes, areas, signer, cert_parsed, tst_client=None, protect_opts=None):
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

    signed_bytes = current_io.getvalue()
    if protect_opts and protect_opts.get('enabled'):
        try:
            doc = fitz.open(stream=signed_bytes, filetype="pdf")
            perm = fitz.PDF_PERM_ACCESSIBILITY
            if protect_opts.get('allow_printing', True): perm |= fitz.PDF_PERM_PRINT
            if protect_opts.get('allow_copying', True): perm |= fitz.PDF_PERM_COPY
            if protect_opts.get('allow_editing', False): perm |= fitz.PDF_PERM_MODIFY
            u_pw = protect_opts.get('open_password', '')
            o_pw = protect_opts.get('permissions_password', u_pw or 'ABInfotechOwnerKey')
            prot_io = io.BytesIO()
            doc.save(
                prot_io,
                encryption=fitz.PDF_ENCRYPT_AES_256,
                user_pw=u_pw,
                owner_pw=o_pw,
                permissions=perm
            )
            doc.close()
            return prot_io.getvalue()
        except Exception as pe:
            logger.warning(f"Error applying post-signing protection: {pe}")

    return signed_bytes

# ==========================================
# 3.5. PDF ALL-IN-ONE POWER SUITE ENGINES
# ==========================================
class WinCertStore:
    """Enumerates certificates from Windows certificate store."""
    @staticmethod
    def get_certificates(store_name="MY"):
        certs = []
        if CURRENT_OS != 'Windows':
            return certs
        try:
            import ssl
            for cert_bytes, encoding_type, trust in ssl.enum_certificates(store_name):
                try:
                    cert = x509.load_der_x509_certificate(cert_bytes)
                    cn = "Unknown"
                    for attr in cert.subject:
                        if attr.oid == x509.NameOID.COMMON_NAME:
                            cn = attr.value
                            break
                    issuer_cn = "Unknown"
                    for attr in cert.issuer:
                        if attr.oid == x509.NameOID.COMMON_NAME:
                            issuer_cn = attr.value
                            break
                    not_after = cert.not_valid_after_utc if hasattr(cert, 'not_valid_after_utc') else cert.not_valid_after
                    certs.append({
                        "common_name": cn,
                        "issuer": issuer_cn,
                        "serial_number": str(cert.serial_number),
                        "expiry": not_after.strftime("%Y-%m-%d"),
                        "cert_obj": cert,
                        "cert_der": cert_bytes
                    })
                except Exception:
                    continue
        except Exception as e:
            logger.warning(f"Error enumerating Windows cert store {store_name}: {e}")
        return certs

class PdfCompressor:
    """Compresses PDF documents by optimizing streams and downsampling images."""
    @staticmethod
    def compress_pdf(input_path, output_path, level="medium", color_mode="color", progress_cb=None):
        orig_size = os.path.getsize(input_path)
        doc = fitz.open(input_path)
        dpi_limit = 72 if level == "high" else (120 if level == "medium" else 150)
        quality = 50 if level == "high" else (75 if level == "medium" else 85)
        total_pages = len(doc)
        for pno in range(total_pages):
            if progress_cb:
                progress_cb(pno + 1, total_pages)
            page = doc[pno]
            img_list = page.get_images(full=True)
            for img_info in img_list:
                xref = img_info[0]
                try:
                    base_img = doc.extract_image(xref)
                    if not base_img:
                        continue
                    img_bytes = base_img["image"]
                    pil_img = Image.open(io.BytesIO(img_bytes))
                    if color_mode == "grayscale" and pil_img.mode != "L":
                        pil_img = pil_img.convert("L")
                    elif color_mode == "bw":
                        pil_img = pil_img.convert("1")
                    elif pil_img.mode in ("RGBA", "P"):
                        pil_img = pil_img.convert("RGB")
                    w, h = pil_img.size
                    max_dim = int(dpi_limit * 11)
                    if w > max_dim or h > max_dim:
                        pil_img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
                    out_img_io = io.BytesIO()
                    if pil_img.mode in ("RGB", "L"):
                        pil_img.save(out_img_io, format="JPEG", quality=quality, optimize=True)
                    else:
                        pil_img.save(out_img_io, format="PNG", optimize=True)
                    page.replace_image(xref, stream=out_img_io.getvalue())
                except Exception:
                    continue
        doc.save(output_path, garbage=4, deflate=True, clean=True)
        doc.close()
        new_size = os.path.getsize(output_path)
        saved_bytes = max(0, orig_size - new_size)
        saved_pct = (saved_bytes / orig_size * 100.0) if orig_size > 0 else 0.0
        return {
            "orig_size": orig_size,
            "new_size": new_size,
            "saved_bytes": saved_bytes,
            "saved_pct": saved_pct
        }

class PdfMerger:
    """Merges multiple PDFs with support for custom page ranges."""
    @staticmethod
    def parse_page_range(range_str, max_pages):
        range_str = (range_str or "").strip()
        if not range_str or range_str.lower() in ("all", "*"):
            return list(range(max_pages))
        pages = []
        for part in range_str.split(","):
            part = part.strip()
            if not part: continue
            if "-" in part:
                try:
                    s, e = part.split("-", 1)
                    s_int = max(1, int(s)) - 1
                    e_int = min(max_pages, int(e)) - 1
                    pages.extend(range(s_int, e_int + 1))
                except ValueError:
                    pass
            else:
                try:
                    p = int(part) - 1
                    if 0 <= p < max_pages:
                        pages.append(p)
                except ValueError:
                    pass
        return sorted(list(set(pages))) if pages else list(range(max_pages))

    @staticmethod
    def merge_pdfs(file_configs, output_path, progress_cb=None):
        merged_doc = fitz.open()
        total = len(file_configs)
        for idx, (fpath, prange) in enumerate(file_configs):
            if progress_cb:
                progress_cb(idx + 1, total)
            if not os.path.exists(fpath):
                continue
            src = fitz.open(fpath)
            selected_pages = PdfMerger.parse_page_range(prange, len(src))
            for pno in selected_pages:
                merged_doc.insert_pdf(src, from_page=pno, to_page=pno)
            src.close()
        merged_doc.save(output_path, garbage=3, deflate=True)
        merged_doc.close()
        return os.path.exists(output_path)

class PdfStampManager:
    """Applies graphical rubber stamp / company seal / watermark to PDF pages."""
    @staticmethod
    def apply_stamp(input_path, output_path, stamp_image_path, position_mode="bottom_right", 
                    custom_rect=None, opacity=0.9, page_selection="all", target_page=1, stamp_size=(150, 60)):
        doc = fitz.open(input_path)
        with open(stamp_image_path, "rb") as img_f:
            img_bytes = img_f.read()

        total_pages = len(doc)
        pages_to_stamp = []
        if page_selection == "all":
            pages_to_stamp = list(range(total_pages))
        elif page_selection == "first":
            pages_to_stamp = [0]
        elif page_selection == "last":
            pages_to_stamp = [total_pages - 1]
        elif page_selection == "custom":
            pno = max(0, min(total_pages - 1, target_page - 1))
            pages_to_stamp = [pno]

        sw, sh = stamp_size
        for pno in pages_to_stamp:
            page = doc[pno]
            pw = page.rect.width
            ph = page.rect.height

            if position_mode == "custom" and custom_rect:
                rect = fitz.Rect(custom_rect[0], custom_rect[1], custom_rect[0] + custom_rect[2], custom_rect[1] + custom_rect[3])
            elif position_mode == "bottom_right":
                rect = fitz.Rect(pw - sw - 36, ph - sh - 36, pw - 36, ph - 36)
            elif position_mode == "bottom_left":
                rect = fitz.Rect(36, ph - sh - 36, 36 + sw, ph - 36)
            elif position_mode == "top_right":
                rect = fitz.Rect(pw - sw - 36, 36, pw - 36, 36 + sh)
            elif position_mode == "top_left":
                rect = fitz.Rect(36, 36, 36 + sw, 36 + sh)
            else: # center
                rect = fitz.Rect((pw - sw) / 2, (ph - sh) / 2, (pw + sw) / 2, (ph + sh) / 2)

            page.insert_image(rect, stream=img_bytes, keep_proportion=True, overlay=True)

        doc.save(output_path, garbage=3, deflate=True)
        doc.close()
        return True

class PdfVerifier:
    """Validates digital signatures using PyHanko and Cryptography."""
    @staticmethod
    def verify_pdf(pdf_path):
        if not os.path.exists(pdf_path):
            return {"success": False, "has_signatures": False, "signatures": [], "message": "File not found."}

        try:
            with open(pdf_path, 'rb') as f:
                reader = PdfFileReader(f)
                sig_fields = list(reader.embedded_signatures)
                if not sig_fields:
                    return {"success": True, "has_signatures": False, "signatures": [], "message": "No digital signatures found in this PDF document."}

                results = []
                all_valid = True
                for sig in sig_fields:
                    fname = sig.field_name
                    try:
                        val = validate_pdf_signature(sig)
                        is_valid = bool(val.valid)
                        is_intact = bool(val.intact)
                        if not (is_valid and is_intact):
                            all_valid = False

                        signer_cert = val.signing_cert
                        cn_name = "Unknown Signer"
                        issuer_name = "Unknown CA"
                        cert_serial = ""
                        expiry_date = "N/A"
                        if signer_cert:
                            cert_serial = str(signer_cert.serial_number)
                            for attr in signer_cert.subject:
                                if attr.oid == x509.NameOID.COMMON_NAME:
                                    cn_name = attr.value
                                    break
                            for attr in signer_cert.issuer:
                                if attr.oid == x509.NameOID.COMMON_NAME:
                                    issuer_name = attr.value
                                    break
                            not_after = signer_cert.not_valid_after_utc if hasattr(signer_cert, 'not_valid_after_utc') else signer_cert.not_valid_after
                            expiry_date = not_after.strftime("%Y-%m-%d %H:%M UTC")

                        has_tsa = False
                        tsa_time_str = "No qualified timestamp embedded"
                        if val.timestamp_validity and val.timestamp_validity.valid:
                            has_tsa = True
                            if val.timestamp_validity.timestamp:
                                tsa_time_str = str(val.timestamp_validity.timestamp)

                        results.append({
                            "field_name": fname,
                            "valid": is_valid,
                            "intact": is_intact,
                            "signer_cn": cn_name,
                            "issuer_cn": issuer_name,
                            "serial": cert_serial,
                            "expiry": expiry_date,
                            "has_tsa": has_tsa,
                            "tsa_time": tsa_time_str,
                            "summary": "Valid & Intact (Document unmodified)" if (is_valid and is_intact) else "Invalid or Modified"
                        })
                    except Exception as ve:
                        all_valid = False
                        results.append({
                            "field_name": fname,
                            "valid": False,
                            "intact": False,
                            "signer_cn": "Error Parsing",
                            "issuer_cn": "Error Parsing",
                            "serial": "",
                            "expiry": "N/A",
                            "has_tsa": False,
                            "tsa_time": "N/A",
                            "summary": f"Verification error: {ve}"
                        })

                msg = f"Verified {len(results)} digital signature(s). {'All signatures are VALID & document is INTACT.' if all_valid else 'One or more signatures are invalid or modified.'}"
                return {
                    "success": True,
                    "has_signatures": True,
                    "all_valid": all_valid,
                    "signatures": results,
                    "message": msg
                }
        except Exception as e:
            return {"success": False, "has_signatures": False, "signatures": [], "message": f"Error validating PDF signatures: {e}"}

class PdfToolsManager:
    """PDF Utilities: Split, Rotate, Extract, Delete, AES-256 Encrypt & Decrypt."""
    @staticmethod
    def split_pdf(input_path, output_dir, mode="single", ranges=""):
        doc = fitz.open(input_path)
        base = Path(input_path).stem
        created = []
        if mode == "single":
            for i, page in enumerate(doc):
                out_p = os.path.join(output_dir, f"{base}_page_{i+1}.pdf")
                new_d = fitz.open()
                new_d.insert_pdf(doc, from_page=i, to_page=i)
                new_d.save(out_p)
                new_d.close()
                created.append(out_p)
        else:
            parts = [p.strip() for p in ranges.split(",") if p.strip()]
            for idx, p_str in enumerate(parts):
                if "-" in p_str:
                    s, e = p_str.split("-", 1)
                    s_int = max(0, int(s) - 1)
                    e_int = min(len(doc) - 1, int(e) - 1)
                else:
                    s_int = e_int = max(0, min(len(doc) - 1, int(p_str) - 1))
                out_p = os.path.join(output_dir, f"{base}_part_{idx+1}_p{s_int+1}-{e_int+1}.pdf")
                new_d = fitz.open()
                new_d.insert_pdf(doc, from_page=s_int, to_page=e_int)
                new_d.save(out_p)
                new_d.close()
                created.append(out_p)
        doc.close()
        return created

    @staticmethod
    def rotate_pdf(input_path, output_path, angle=90, page_selection="all", specific_pages=""):
        doc = fitz.open(input_path)
        total = len(doc)
        if page_selection == "all":
            pages = list(range(total))
        elif page_selection == "even":
            pages = [i for i in range(total) if (i + 1) % 2 == 0]
        elif page_selection == "odd":
            pages = [i for i in range(total) if (i + 1) % 2 != 0]
        elif page_selection == "specific":
            pages = PdfMerger.parse_page_range(specific_pages, total)
        else:
            pages = list(range(total))
        for pno in pages:
            doc[pno].set_rotation(doc[pno].rotation + angle)
        doc.save(output_path, garbage=3, deflate=True)
        doc.close()
        return True

    @staticmethod
    def extract_pages(input_path, output_path, page_range_str):
        doc = fitz.open(input_path)
        selected = PdfMerger.parse_page_range(page_range_str, len(doc))
        new_d = fitz.open()
        for pno in selected:
            new_d.insert_pdf(doc, from_page=pno, to_page=pno)
        new_d.save(output_path, garbage=3, deflate=True)
        new_d.close()
        doc.close()
        return True

    @staticmethod
    def delete_pages(input_path, output_path, page_range_str):
        doc = fitz.open(input_path)
        to_del = set(PdfMerger.parse_page_range(page_range_str, len(doc)))
        to_keep = [i for i in range(len(doc)) if i not in to_del]
        if not to_keep:
            doc.close()
            raise ValueError("Cannot delete all pages from PDF!")
        new_d = fitz.open()
        for pno in to_keep:
            new_d.insert_pdf(doc, from_page=pno, to_page=pno)
        new_d.save(output_path, garbage=3, deflate=True)
        new_d.close()
        doc.close()
        return True

    @staticmethod
    def protect_pdf(input_path, output_path, user_pw, owner_pw, allow_print=True, allow_copy=True, allow_edit=False):
        doc = fitz.open(input_path)
        perm = fitz.PDF_PERM_ACCESSIBILITY
        if allow_print: perm |= fitz.PDF_PERM_PRINT
        if allow_copy: perm |= fitz.PDF_PERM_COPY
        if allow_edit: perm |= fitz.PDF_PERM_MODIFY
        doc.save(
            output_path,
            encryption=fitz.PDF_ENCRYPT_AES_256,
            user_pw=user_pw,
            owner_pw=owner_pw or user_pw or "owner_key",
            permissions=perm
        )
        doc.close()
        return True

    @staticmethod
    def unlock_pdf(input_path, output_path, password):
        doc = fitz.open(input_path)
        if doc.is_encrypted:
            if not doc.authenticate(password):
                doc.close()
                raise ValueError("Incorrect password for protected PDF.")
        doc.save(output_path, encryption=fitz.PDF_ENCRYPT_NONE)
        doc.close()
        return True

class PdfOcrEditManager:
    """PDF OCR Text Extraction, Redaction & Watermarking."""
    @staticmethod
    def extract_text_and_ocr(input_path, output_txt_path=None, page_range="all"):
        doc = fitz.open(input_path)
        total = len(doc)
        pages = PdfMerger.parse_page_range(page_range, total)
        lines = []
        for pno in pages:
            page = doc[pno]
            txt = page.get_text("text")
            if not txt.strip():
                try:
                    tp = page.get_textpage_ocr(flags=0, full=True)
                    txt = tp.extractText()
                except Exception:
                    pass
            lines.append(f"=== [ PAGE {pno+1} ] ===\n" + (txt if txt.strip() else "[No text content found on this page]"))
        doc.close()
        full = "\n\n".join(lines)
        if output_txt_path:
            with open(output_txt_path, "w", encoding="utf-8") as f:
                f.write(full)
        return full

    @staticmethod
    def search_and_redact(input_path, output_path, search_term, replace_text="", fill_color=(0, 0, 0)):
        doc = fitz.open(input_path)
        count = 0
        for page in doc:
            rects = page.search_for(search_term)
            for r in rects:
                page.add_redact_annot(r, text=replace_text, fill=fill_color)
                count += 1
            if rects:
                page.apply_redactions()
        doc.save(output_path, garbage=3, deflate=True)
        doc.close()
        return count

    @staticmethod
    def add_watermark(input_path, output_path, watermark_text="CONFIDENTIAL", opacity=0.3, font_size=42, angle=45, color=(0.8, 0.2, 0.2)):
        doc = fitz.open(input_path)
        for page in doc:
            pw = page.rect.width
            ph = page.rect.height
            rect = fitz.Rect(pw * 0.1, ph * 0.2, pw * 0.9, ph * 0.8)
            page.insert_textbox(
                rect,
                watermark_text,
                fontsize=font_size,
                fontname="helv",
                rotate=angle,
                color=color,
                align=fitz.TEXT_ALIGN_CENTER
            )
        doc.save(output_path, garbage=3, deflate=True)
        doc.close()
        return True

class SignatureAreaPickerWindow:
    """Interactive visual dialog to preview PDF page and choose signature / stamp position."""
    def __init__(self, parent, pdf_path, on_area_selected, initial_area=None, title="Choose Position & Size on Page"):
        self.dialog = tk.Toplevel(parent)
        self.dialog.title(title)
        self.dialog.geometry("1100x820")
        self.dialog.minsize(820, 600)
        self.dialog.transient(parent)
        self.dialog.grab_set()

        self.pdf_path = pdf_path
        self.on_area_selected = on_area_selected
        self.current_page = 0
        self.doc = fitz.open(pdf_path)
        self.total_pages = len(self.doc)
        self.scale = 1.2
        self.drawing_mode = True
        self.start_x = 0
        self.start_y = 0
        self.temp_rect = None
        self.selected_area = initial_area.copy() if initial_area else {}
        if self.selected_area and 'page' in self.selected_area:
            self.current_page = min(self.total_pages - 1, max(0, int(self.selected_area['page'])))

        self.dialog.protocol("WM_DELETE_WINDOW", self._on_close)
        self._build_ui()
        self.render_page()

    def _build_ui(self):
        top_bar = tk.Frame(self.dialog, bg="#1E3A8A", padx=10, pady=8)
        top_bar.pack(side=tk.TOP, fill=tk.X)

        self.btn_prev = ttk.Button(top_bar, text="◀ Prev Page", command=self.prev_page, style="Tool3D.TButton")
        self.btn_prev.pack(side=tk.LEFT, padx=3)
        self.lbl_page = tk.Label(top_bar, text=f"Page {self.current_page+1} / {self.total_pages}", font=get_sys_font(9, "bold"), bg="#1E40AF", fg="#FFFFFF", padx=8, pady=3, relief=tk.SUNKEN, bd=1)
        self.lbl_page.pack(side=tk.LEFT, padx=4)
        self.btn_next = ttk.Button(top_bar, text="Next Page ▶", command=self.next_page, style="Tool3D.TButton")
        self.btn_next.pack(side=tk.LEFT, padx=3)

        ttk.Separator(top_bar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8)

        tk.Label(top_bar, text="Presets:", font=get_sys_font(9, "bold"), fg="#93C5FD", bg="#1E3A8A").pack(side=tk.LEFT, padx=(2, 4))
        ttk.Button(top_bar, text="📍 Bottom-Right (DSC)", command=lambda: self.apply_preset("bottom_right"), style="Tool3D.TButton").pack(side=tk.LEFT, padx=2)
        ttk.Button(top_bar, text="📍 Bottom-Left", command=lambda: self.apply_preset("bottom_left"), style="Tool3D.TButton").pack(side=tk.LEFT, padx=2)
        ttk.Button(top_bar, text="📍 Top-Right", command=lambda: self.apply_preset("top_right"), style="Tool3D.TButton").pack(side=tk.LEFT, padx=2)
        ttk.Button(top_bar, text="📍 Center", command=lambda: self.apply_preset("center"), style="Tool3D.TButton").pack(side=tk.LEFT, padx=2)

        ttk.Button(top_bar, text="🔍 Zoom +", command=self.zoom_in, style="Tool3D.TButton").pack(side=tk.LEFT, padx=4)
        ttk.Button(top_bar, text="🔍 Zoom -", command=self.zoom_out, style="Tool3D.TButton").pack(side=tk.LEFT, padx=2)

        btn_cancel = ttk.Button(top_bar, text="Cancel", command=self._on_close, style="Tool3D.TButton")
        btn_cancel.pack(side=tk.RIGHT, padx=4)

        btn_apply = ttk.Button(top_bar, text="✅ Set Selected Position", command=self.apply_and_close, style="Primary3D.TButton")
        btn_apply.pack(side=tk.RIGHT, padx=6)

        info_bar = tk.Frame(self.dialog, bg="#F0F4F8", padx=10, pady=4, relief=tk.GROOVE, bd=1)
        info_bar.pack(side=tk.TOP, fill=tk.X)
        self.lbl_info = tk.Label(info_bar, text="👉 Click & drag on page to draw custom box, or choose a preset above.", font=get_sys_font(9), fg="#1E40AF", bg="#F0F4F8")
        self.lbl_info.pack(side=tk.LEFT)

        canvas_outer = tk.Frame(self.dialog, bg="#94A3B8")
        canvas_outer.pack(fill=tk.BOTH, expand=True)

        self.canvas_vscroll = ttk.Scrollbar(canvas_outer, orient=tk.VERTICAL)
        self.canvas_vscroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas_hscroll = ttk.Scrollbar(canvas_outer, orient=tk.HORIZONTAL)
        self.canvas_hscroll.pack(side=tk.BOTTOM, fill=tk.X)

        self.canvas = tk.Canvas(canvas_outer, bg="#94A3B8", xscrollcommand=self.canvas_hscroll.set, yscrollcommand=self.canvas_vscroll.set, highlightthickness=0)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.canvas_vscroll.config(command=self.canvas.yview)
        self.canvas_hscroll.config(command=self.canvas.xview)

        self.canvas.bind("<ButtonPress-1>", self.on_mouse_down)
        self.canvas.bind("<B1-Motion>", self.on_mouse_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_mouse_up)

    def _on_close(self):
        try: self.doc.close()
        except Exception: pass
        self.dialog.destroy()

    def zoom_in(self):
        self.scale = min(2.5, self.scale + 0.2)
        self.render_page()

    def zoom_out(self):
        self.scale = max(0.6, self.scale - 0.2)
        self.render_page()

    def prev_page(self):
        if self.current_page > 0:
            self.current_page -= 1
            self.render_page()

    def next_page(self):
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
            self.render_page()

    def render_page(self):
        self.canvas.delete("all")
        page = self.doc[self.current_page]
        mat = fitz.Matrix(self.scale, self.scale)
        pix = page.get_pixmap(matrix=mat)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        self.tk_img = ImageTk.PhotoImage(img)
        self.canvas.create_image(0, 0, anchor=tk.NW, image=self.tk_img)
        self.canvas.config(scrollregion=(0, 0, pix.width, pix.height))
        self.lbl_page.config(text=f"Page {self.current_page+1} / {self.total_pages}")

        # Render selected box if exists on this page
        if self.selected_area and int(self.selected_area.get('page', 0)) == self.current_page:
            real_h = page.rect.height
            px1 = float(self.selected_area['x']) * self.scale
            pw = float(self.selected_area['width']) * self.scale
            ph = float(self.selected_area['height']) * self.scale
            py1 = (real_h - float(self.selected_area['y']) - float(self.selected_area['height'])) * self.scale
            px2 = px1 + pw
            py2 = py1 + ph

            # Draw outer shadow & box
            self.canvas.create_rectangle(px1+2, py1+2, px2+2, py2+2, fill="#0F172A", stipple="gray25", outline="")
            self.canvas.create_rectangle(px1, py1, px2, py2, fill="#E0F2FE", outline="#0284C7", width=2, stipple="gray50")
            self.canvas.create_text((px1+px2)/2, (py1+py2)/2, text="✍️ Signature / Stamp Area", fill="#0369A1", font=get_sys_font(9, "bold"))
            self.lbl_info.config(
                text=f"✓ Selected Box: X={int(self.selected_area['x'])}pt, Y={int(self.selected_area['y'])}pt | W={int(self.selected_area['width'])}pt, H={int(self.selected_area['height'])}pt (Page {self.current_page+1})",
                fg="#047857"
            )

    def apply_preset(self, preset):
        page = self.doc[self.current_page]
        pw = page.rect.width
        ph = page.rect.height
        w = 160.0
        h = 65.0
        margin = 36.0

        if preset == "bottom_right":
            x = pw - w - margin
            y = margin
        elif preset == "bottom_left":
            x = margin
            y = margin
        elif preset == "top_right":
            x = pw - w - margin
            y = ph - h - margin
        elif preset == "top_left":
            x = margin
            y = ph - h - margin
        else: # center
            x = (pw - w) / 2.0
            y = (ph - h) / 2.0

        self.selected_area = {
            'page': self.current_page,
            'x': x, 'y': y,
            'width': w, 'height': h
        }
        self.render_page()

    def on_mouse_down(self, event):
        self.start_x = self.canvas.canvasx(event.x)
        self.start_y = self.canvas.canvasy(event.y)
        self.temp_rect = self.canvas.create_rectangle(
            self.start_x, self.start_y, self.start_x, self.start_y,
            outline="#0284C7", width=2, dash=(4, 4)
        )

    def on_mouse_drag(self, event):
        if not self.temp_rect: return
        self.canvas.coords(self.temp_rect, self.start_x, self.start_y, self.canvas.canvasx(event.x), self.canvas.canvasy(event.y))

    def on_mouse_up(self, event):
        if not self.temp_rect: return
        cur_x, cur_y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        x1, x2 = sorted([self.start_x, cur_x])
        y1, y2 = sorted([self.start_y, cur_y])
        self.canvas.delete(self.temp_rect)
        self.temp_rect = None

        if (x2 - x1) < 15 or (y2 - y1) < 15: return

        real_h = self.doc[self.current_page].rect.height
        pdf_x1, pdf_y1, pdf_x2, pdf_y2 = x1/self.scale, y1/self.scale, x2/self.scale, y2/self.scale
        self.selected_area = {
            'page': self.current_page,
            'x': pdf_x1, 'y': real_h - pdf_y2,
            'width': pdf_x2 - pdf_x1, 'height': (real_h - pdf_y1) - (real_h - pdf_y2)
        }
        self.render_page()

    def apply_and_close(self):
        if not self.selected_area:
            self.apply_preset("bottom_right")
        self.on_area_selected(self.selected_area)
        self._on_close()


# ==========================================
# 4. CROSS-PLATFORM DESKTOP APP (Tkinter GUI)
# ==========================================
class VisualPdfSignerApp:
    def __init__(self, root):
        self.root = root
        global gui_root, gui_app
        gui_root = self.root
        gui_app = self

        self.root.title(f"A B INFOTECH - PDF Sign & All-in-One Power Suite v2.0 [{CURRENT_OS}]")
        self.root.geometry("1340x880")
        self.root.minsize(1080, 720)
        self.root.protocol('WM_DELETE_WINDOW', self.on_window_close)

        self._configure_styles()
        self._apply_window_icon(self.root)

        # ------------------------------------------
        # State: Tab 1 (Sign PDF)
        # ------------------------------------------
        self.sign_pdf_files = []
        self.sign_output_mode = tk.StringVar(value="beside")  # 'beside' or 'folder'
        self.sign_output_folder = tk.StringVar(value="")
        self.sign_flatten_old = tk.BooleanVar(value=False)

        # Certificate Settings
        self.cert_source_var = tk.StringVar(value="token" if DRIVER_MAP and list(DRIVER_MAP.keys())[0] != "[Browse Custom Driver Library...]" else "pfx")
        self.driver_var = tk.StringVar()
        self.pin_var = tk.StringVar()
        self.token_certs = []

        self.pfx_path_var = tk.StringVar()
        self.pfx_pass_var = tk.StringVar()
        self.pfx_loaded_cert = None

        self.win_store_var = tk.StringVar(value="MY")
        self.win_store_certs = []

        # Signature Details
        self.sign_reason_var = tk.StringVar(value="I approve this document")
        self.sign_location_var = tk.StringVar(value="India")
        self.sign_contact_var = tk.StringVar(value="")
        self.sign_hash_var = tk.StringVar(value="SHA-256")
        self.sign_tsa_var = tk.StringVar(value="DigiCert TSA (Recommended)")
        self.sign_type_var = tk.StringVar(value="Approval signature (normal)")

        # Appearance Details
        self.chk_visible_sig = tk.BooleanVar(value=True)
        self.sign_page_mode = tk.StringVar(value="all")  # 'all', 'each', 'first', 'last', 'specific'
        self.sign_page_num_var = tk.StringVar(value="1")
        self.sign_selected_area = {'x': 400.0, 'y': 36.0, 'width': 160.0, 'height': 65.0, 'page': 0}
        self.chk_name_var = tk.BooleanVar(value=True)
        self.chk_date_var = tk.BooleanVar(value=True)
        self.chk_reason_var = tk.BooleanVar(value=True)
        self.chk_loc_var = tk.BooleanVar(value=True)
        self.chk_issuer_var = tk.BooleanVar(value=False)
        self.chk_tick_var = tk.BooleanVar(value=True)
        self.chk_border_var = tk.BooleanVar(value=True)
        self.sign_heading_var = tk.StringVar(value="Digitally signed by:")
        self.sign_extra_line_var = tk.StringVar(value="A B INFOTECH")
        self.sign_logo_path_var = tk.StringVar(value="")

        # Protection Details
        self.chk_protect_var = tk.BooleanVar(value=False)
        self.protect_open_pw_var = tk.StringVar(value="")
        self.protect_perm_pw_var = tk.StringVar(value="")
        self.chk_allow_print = tk.BooleanVar(value=True)
        self.chk_allow_copy = tk.BooleanVar(value=True)
        self.chk_allow_edit = tk.BooleanVar(value=False)

        # ------------------------------------------
        # State: Tab 2 (Image Stamp)
        # ------------------------------------------
        self.stamp_pdf_files = []
        self.stamp_img_path = tk.StringVar(value="")
        self.stamp_pos_mode = tk.StringVar(value="bottom_right")
        self.stamp_custom_rect = None
        self.stamp_page_scope = tk.StringVar(value="all")
        self.stamp_page_num = tk.StringVar(value="1")
        self.stamp_width_var = tk.StringVar(value="150")
        self.stamp_height_var = tk.StringVar(value="60")
        self.stamp_opacity_var = tk.DoubleVar(value=90.0)
        self.stamp_out_mode = tk.StringVar(value="beside")
        self.stamp_out_dir = tk.StringVar(value="")

        # ------------------------------------------
        # State: Tab 3 (Compress)
        # ------------------------------------------
        self.compress_pdf_files = []
        self.compress_level_var = tk.StringVar(value="medium")
        self.compress_color_var = tk.StringVar(value="color")
        self.compress_out_mode = tk.StringVar(value="beside")
        self.compress_out_dir = tk.StringVar(value="")

        # ------------------------------------------
        # State: Tab 4 (Merge)
        # ------------------------------------------
        self.merge_pdf_items = []
        self.merge_out_path = tk.StringVar(value="")

        # ------------------------------------------
        # State: Tab 5 (Verify)
        # ------------------------------------------
        self.verify_pdf_path = tk.StringVar(value="")

        # ------------------------------------------
        # State: Tab 6 (Tools)
        # ------------------------------------------
        self.tool_split_pdf = tk.StringVar()
        self.tool_split_mode = tk.StringVar(value="single")
        self.tool_split_ranges = tk.StringVar(value="1-3, 4-6")
        self.tool_split_out_dir = tk.StringVar()

        self.tool_rot_pdf = tk.StringVar()
        self.tool_rot_angle = tk.IntVar(value=90)
        self.tool_rot_scope = tk.StringVar(value="all")
        self.tool_rot_spec = tk.StringVar(value="")

        self.tool_ext_pdf = tk.StringVar()
        self.tool_ext_pages = tk.StringVar(value="1-3")

        self.tool_del_pdf = tk.StringVar()
        self.tool_del_pages = tk.StringVar(value="2")

        self.tool_prot_pdf = tk.StringVar()
        self.tool_prot_open = tk.StringVar()
        self.tool_prot_perm = tk.StringVar()
        self.tool_prot_print = tk.BooleanVar(value=True)
        self.tool_prot_copy = tk.BooleanVar(value=True)
        self.tool_prot_edit = tk.BooleanVar(value=False)

        self.tool_unl_pdf = tk.StringVar()
        self.tool_unl_pw = tk.StringVar()

        # ------------------------------------------
        # State: Tab 7 (OCR & Edit)
        # ------------------------------------------
        self.ocr_pdf_path = tk.StringVar()
        self.ocr_page_scope = tk.StringVar(value="all")

        self.redact_pdf_path = tk.StringVar()
        self.redact_term_var = tk.StringVar()
        self.redact_mode_var = tk.StringVar(value="blackout")
        self.redact_replace_var = tk.StringVar()

        self.wm_pdf_path = tk.StringVar()
        self.wm_text_var = tk.StringVar(value="CONFIDENTIAL")
        self.wm_angle_var = tk.IntVar(value=45)
        self.wm_opacity_var = tk.DoubleVar(value=30.0)

        # Legacy backward-compatibility states
        self.bulk_mode = False
        self.bulk_in_dir = None
        self.bulk_out_dir = None
        self.bulk_pdf_files = []
        self.signature_areas = []
        self.all_pages_var = tk.BooleanVar(value=True)
        self.just_activated = False

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
        style.configure("Header.TLabel", font=get_sys_font(13, "bold"), foreground="#1E40AF")
        style.configure("Toolbar.TFrame", background="#F1F5F9")

        # 3D Push Button Styles
        style.configure("Primary3D.TButton", font=get_sys_font(10, "bold"),
            background="#059669", foreground="#FFFFFF", borderwidth=1, focuscolor="none",
            lightcolor="#34D399", darkcolor="#047857"
        )
        style.map("Primary3D.TButton",
            background=[("pressed", "#047857"), ("active", "#10B981")],
            foreground=[("pressed", "#FFFFFF"), ("active", "#FFFFFF")],
            lightcolor=[("pressed", "#047857"), ("active", "#6EE7B7")],
            darkcolor=[("pressed", "#34D399"), ("active", "#065F46")]
        )

        style.configure("Tool3D.TButton", font=f_bold,
            background="#FFFFFF", foreground="#1E293B", borderwidth=1, focuscolor="none",
            lightcolor="#FFFFFF", darkcolor="#CBD5E1"
        )
        style.map("Tool3D.TButton",
            background=[("pressed", "#E2E8F0"), ("active", "#F8FAFC")],
            lightcolor=[("pressed", "#CBD5E1"), ("active", "#FFFFFF")],
            darkcolor=[("pressed", "#FFFFFF"), ("active", "#94A3B8")]
        )

        style.configure("Batch.TButton", font=f_bold,
            background="#2563EB", foreground="#FFFFFF", borderwidth=1, focuscolor="none",
            lightcolor="#60A5FA", darkcolor="#1D4ED8"
        )
        style.map("Batch.TButton",
            background=[("pressed", "#1D4ED8"), ("active", "#3B82F6")],
            foreground=[("pressed", "#FFFFFF"), ("active", "#FFFFFF")],
            lightcolor=[("pressed", "#1D4ED8"), ("active", "#93C5FD")],
            darkcolor=[("pressed", "#60A5FA"), ("active", "#1E3A8A")]
        )

        style.configure("BuyDSC.TButton", font=get_sys_font(9, "bold"),
            background="#16A34A", foreground="#FFFFFF", borderwidth=1, focuscolor="none",
            lightcolor="#4ADE80", darkcolor="#15803D"
        )
        style.map("BuyDSC.TButton",
            background=[("pressed", "#15803D"), ("active", "#22C55E")],
            foreground=[("pressed", "#FFFFFF"), ("active", "#FFFFFF")]
        )

        style.configure("TNotebook", background="#F1F5F9", tabmargins=[4, 4, 4, 0])
        style.configure("TNotebook.Tab", font=get_sys_font(9, "bold"), padding=[14, 6], background="#E2E8F0", foreground="#334155")
        style.map("TNotebook.Tab",
            background=[("selected", "#FFFFFF"), ("active", "#F8FAFC")],
            foreground=[("selected", "#1E3A8A"), ("active", "#1E40AF")]
        )

    def _create_3d_card(self, parent, bg="#FFFFFF", shadow_color="#CBD5E1", highlight_color="#FFFFFF", pady=(0, 6)):
        shadow = tk.Frame(parent, bg=shadow_color, bd=0)
        shadow.pack(fill=tk.BOTH if pady == "fill" else tk.X, pady=0 if pady == "fill" else pady)
        bevel = tk.Frame(shadow, bg=highlight_color, bd=0)
        bevel.pack(fill=tk.BOTH, expand=True, padx=(1, 2), pady=(1, 2))
        card = tk.Frame(bevel, bg=bg, relief=tk.RAISED, bd=1, padx=8, pady=6)
        card.pack(fill=tk.BOTH, expand=True, padx=(1, 1), pady=(1, 1))
        card.shadow = shadow
        card.bevel = bevel
        return card

    def _maximize_window(self, window):
        try:
            if CURRENT_OS == 'Windows':
                window.state('zoomed')
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
        try:
            self.root.withdraw()
        except Exception as e:
            logger.error(f"Error hiding window: {e}")

    def show_window(self):
        try:
            self.root.deiconify()
            self._maximize_window(self.root)
            self.root.lift()
            self.root.focus_force()
            if CURRENT_OS == 'Windows':
                self.root.attributes('-topmost', True)
                self.root.update_idletasks()
                self.root.attributes('-topmost', False)
                try:
                    hwnd = self.root.winfo_id()
                    ctypes.windll.user32.ShowWindow(hwnd, 9)
                    ctypes.windll.user32.SetForegroundWindow(hwnd)
                except Exception:
                    pass
        except Exception as e:
            logger.error(f"Error showing window: {e}")

    def on_window_close(self):
        self.hide_window()

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
        is_bg = any(arg.lower() in ['--background', '--tray', '--minimized', '--autostart', '-b'] for arg in sys.argv[1:])
        if is_bg:
            if LicenseManager.check_expiration_local() == "expired":
                self._force_lock_ui()
            return

        key = LicenseManager.get_license_key()
        if not key or key == "ABI-MASTER-PASS": return
        try:
            safe_key = urllib.parse.quote(key)
            url = f"{LicenseManager.WEBSITE_URL}?action=browser_sync&key={safe_key}"
            self._open_url_in_app_mode(url, hide_window=True)
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

    def _force_lock_ui(self):
        messagebox.showwarning("License Expired", "Your software license was checked online and has been suspended or expired!\n\nPlease contact 7016797720 to renew.")
        self.root.withdraw()
        self._show_activation_screen()

    def _init_app_interface(self):
        is_bg = any(arg.lower() in ['--background', '--tray', '--minimized', '--autostart', '-b'] for arg in sys.argv[1:])
        if not is_bg:
            self.root.deiconify()
            self._maximize_window(self.root)
        else:
            self.hide_window()

        self._build_ui()
        if self.just_activated: self.just_activated = False
        else: self._run_startup_sync()

    def _build_ui(self):
        # ------------------------------------------
        # Top Header Bar
        # ------------------------------------------
        header_bar = tk.Frame(self.root, bg="#1E3A8A", height=54, padx=12, pady=6)
        header_bar.pack(side=tk.TOP, fill=tk.X)
        header_bar.pack_propagate(False)

        # Brand / Logo icon & Title
        title_box = tk.Frame(header_bar, bg="#1E3A8A")
        title_box.pack(side=tk.LEFT, fill=tk.Y)

        tk.Label(title_box, text="⚡", font=get_sys_font(15, "bold"), fg="#38BDF8", bg="#1E3A8A").pack(side=tk.LEFT, padx=(0, 6))
        tk.Label(title_box, text="A B INFOTECH", font=get_sys_font(13, "bold"), fg="#FFFFFF", bg="#1E3A8A").pack(side=tk.LEFT)
        tk.Label(title_box, text="— PDF Sign & All-in-One Power Suite", font=get_sys_font(10), fg="#BFDBFE", bg="#1E3A8A").pack(side=tk.LEFT, padx=(6, 8))

        lic_badge = tk.Label(title_box, text="v2.0 PRO SUITE", font=get_sys_font(8, "bold"), fg="#10B981", bg="#064E3B", padx=6, pady=2, relief=tk.SOLID, bd=1)
        lic_badge.pack(side=tk.LEFT)

        # Right Header Buttons
        btn_box = tk.Frame(header_bar, bg="#1E3A8A")
        btn_box.pack(side=tk.RIGHT, fill=tk.Y)

        ttk.Button(btn_box, text="🛒 Buy DSC / License", style="BuyDSC.TButton", command=self.open_buy_dsc).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_box, text="💬 WhatsApp Support", style="Tool3D.TButton", command=self.open_whatsapp).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_box, text="⚙️ Drivers & Settings", style="Tool3D.TButton", command=self.open_settings_dialog).pack(side=tk.LEFT, padx=4)

        # ------------------------------------------
        # Main Notebook (8 Tabs matching Reference Application)
        # ------------------------------------------
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=6, pady=(4, 6))

        # 1. Sign PDF Tab
        self.tab_sign = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_sign, text=" ✍️ Sign PDF ")
        self._build_tab_sign(self.tab_sign)

        # 2. Image Stamp Tab
        self.tab_stamp = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_stamp, text=" 🖼️ Image stamp ")
        self._build_tab_stamp(self.tab_stamp)

        # 3. Compress Tab
        self.tab_compress = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_compress, text=" 🗜️ Compress ")
        self._build_tab_compress(self.tab_compress)

        # 4. Merge Tab
        self.tab_merge = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_merge, text=" 📑 Merge ")
        self._build_tab_merge(self.tab_merge)

        # 5. Verify Tab
        self.tab_verify = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_verify, text=" 🔍 Verify ")
        self._build_tab_verify(self.tab_verify)

        # 6. Tools Tab
        self.tab_tools = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_tools, text=" 🛠️ Tools ")
        self._build_tab_tools(self.tab_tools)

        # 7. OCR & Edit Tab
        self.tab_ocr_edit = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_ocr_edit, text=" 📖 OCR & Edit ")
        self._build_tab_ocr_edit(self.tab_ocr_edit)

        # 8. About & License Tab
        self.tab_about = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_about, text=" ℹ️ About & License ")
        self._build_tab_about(self.tab_about)

    # =========================================================================
    # TAB 1: ✍️ SIGN PDF (Flagship Dual-Column Layout matching Reference App)
    # =========================================================================
    def _build_tab_sign(self, parent):
        outer = tk.Frame(parent, bg="#F1F5F9")
        outer.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)

        left_col = tk.Frame(outer, bg="#F1F5F9", width=540)
        left_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 6))

        right_col = tk.Frame(outer, bg="#F1F5F9", width=560)
        right_col.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(6, 0))

        # LEFT COLUMN: Section 1 (PDF documents)
        card_sec1 = self._create_3d_card(left_col, pady=(0, 6))
        tk.Label(card_sec1, text="1. PDF documents", font=get_sys_font(10, "bold"), fg="#1E40AF", bg="#FFFFFF").pack(anchor=tk.W, pady=(0, 4))

        list_frame = tk.Frame(card_sec1, bg="#FFFFFF")
        list_frame.pack(fill=tk.X, pady=(0, 4))

        self.sign_listbox = tk.Listbox(list_frame, height=5, font=get_sys_font(8), selectmode=tk.EXTENDED, bd=1, relief=tk.SOLID)
        self.sign_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb1 = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.sign_listbox.yview)
        sb1.pack(side=tk.RIGHT, fill=tk.Y)
        self.sign_listbox.config(yscrollcommand=sb1.set)

        btn_row1 = tk.Frame(card_sec1, bg="#FFFFFF")
        btn_row1.pack(fill=tk.X, pady=(2, 4))
        ttk.Button(btn_row1, text="Add files...", style="Tool3D.TButton", command=self.sign_add_files).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(btn_row1, text="Add folder...", style="Tool3D.TButton", command=self.sign_add_folder).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_row1, text="Remove selected", style="Tool3D.TButton", command=self.sign_remove_selected).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_row1, text="Clear", style="Tool3D.TButton", command=self.sign_clear).pack(side=tk.LEFT, padx=4)

        dest_frame = tk.Frame(card_sec1, bg="#FFFFFF")
        dest_frame.pack(fill=tk.X, pady=(4, 2))
        tk.Radiobutton(dest_frame, text="Save beside the original (name_signed.pdf)", variable=self.sign_output_mode, value="beside", bg="#FFFFFF", fg="#1E3A8A", font=get_sys_font(8)).pack(anchor=tk.W)

        dest_row2 = tk.Frame(dest_frame, bg="#FFFFFF")
        dest_row2.pack(fill=tk.X, pady=(2, 2))
        tk.Radiobutton(dest_row2, text="Save in folder:", variable=self.sign_output_mode, value="folder", bg="#FFFFFF", fg="#1E3A8A", font=get_sys_font(8)).pack(side=tk.LEFT)
        self.entry_sign_out_folder = ttk.Entry(dest_row2, textvariable=self.sign_output_folder, font=get_sys_font(8))
        self.entry_sign_out_folder.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)
        ttk.Button(dest_row2, text="Browse...", style="Tool3D.TButton", command=self.sign_browse_out_folder).pack(side=tk.RIGHT)

        tk.Label(card_sec1, text="Your original files are never changed.", font=get_sys_font(7, "italic"), fg="#64748B", bg="#FFFFFF").pack(anchor=tk.W)
        tk.Checkbutton(card_sec1, text="If a PDF is already signed: flatten the old signatures first", variable=self.sign_flatten_old, bg="#FFFFFF", fg="#334155", font=get_sys_font(8)).pack(anchor=tk.W, pady=(2, 0))

        # LEFT COLUMN: Section 2 (Digital certificate (DSC))
        card_sec2 = self._create_3d_card(left_col, pady=(0, 6))
        tk.Label(card_sec2, text="2. Digital certificate (DSC)", font=get_sys_font(10, "bold"), fg="#1E40AF", bg="#FFFFFF").pack(anchor=tk.W, pady=(0, 4))

        self.nb_dsc = ttk.Notebook(card_sec2)
        self.nb_dsc.pack(fill=tk.X, expand=True, pady=(2, 4))

        # Sub-tab A: USB Token / Smart card
        f_token = ttk.Frame(self.nb_dsc, padding=6)
        self.nb_dsc.add(f_token, text=" USB Token / Smart card ")

        tk.Label(f_token, text="Token PKCS#11 Driver:", font=get_sys_font(8, "bold"), fg="#1E3A8A").pack(anchor=tk.W)
        d_row = tk.Frame(f_token)
        d_row.pack(fill=tk.X, pady=(2, 4))
        self.token_driver_combo = ttk.Combobox(d_row, textvariable=self.driver_var, values=list(DRIVER_MAP.keys()), state="readonly", font=get_sys_font(8))
        self.token_driver_combo.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        if DRIVER_MAP: self.token_driver_combo.current(0)
        ttk.Button(d_row, text="Refresh", style="Tool3D.TButton", command=self.load_token_certificates).pack(side=tk.RIGHT)

        tk.Label(f_token, text="Certificate in Token:", font=get_sys_font(8, "bold"), fg="#1E3A8A").pack(anchor=tk.W)
        self.token_cert_combo = ttk.Combobox(f_token, state="readonly", font=get_sys_font(8))
        self.token_cert_combo.pack(fill=tk.X, pady=(2, 4))

        pin_row = tk.Frame(f_token)
        pin_row.pack(fill=tk.X, pady=(2, 2))
        tk.Label(pin_row, text="Hardware Token PIN:", font=get_sys_font(8, "bold"), fg="#1E3A8A").pack(side=tk.LEFT)
        ttk.Entry(pin_row, textvariable=self.pin_var, show="*", width=16).pack(side=tk.LEFT, padx=6)
        ttk.Button(pin_row, text="⚙️ Manage Drivers", style="Tool3D.TButton", command=lambda: self.open_settings_dialog(initial_tab="drivers")).pack(side=tk.RIGHT)

        # Sub-tab B: PFX / P12 file
        f_pfx = ttk.Frame(self.nb_dsc, padding=6)
        self.nb_dsc.add(f_pfx, text=" PFX / P12 file ")

        tk.Label(f_pfx, text="Select .PFX / .P12 File:", font=get_sys_font(8, "bold"), fg="#1E3A8A").pack(anchor=tk.W)
        p_row1 = tk.Frame(f_pfx)
        p_row1.pack(fill=tk.X, pady=(2, 4))
        ttk.Entry(p_row1, textvariable=self.pfx_path_var, font=get_sys_font(8)).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        ttk.Button(p_row1, text="Browse...", style="Tool3D.TButton", command=self.browse_pfx_file).pack(side=tk.RIGHT)

        p_row2 = tk.Frame(f_pfx)
        p_row2.pack(fill=tk.X, pady=(2, 4))
        tk.Label(p_row2, text="Password:", font=get_sys_font(8, "bold"), fg="#1E3A8A").pack(side=tk.LEFT)
        ttk.Entry(p_row2, textvariable=self.pfx_pass_var, show="*", width=16).pack(side=tk.LEFT, padx=6)
        ttk.Button(p_row2, text="Verify", style="Tool3D.TButton", command=lambda: self.verify_pfx_certificate(silent=False)).pack(side=tk.LEFT)

        self.lbl_pfx_info = tk.Label(f_pfx, text="Choose .pfx file & enter password.", font=get_sys_font(8), fg="#64748B", bg="#F8FAFC", relief=tk.SUNKEN, bd=1, padx=4, pady=2, anchor=tk.W)
        self.lbl_pfx_info.pack(fill=tk.X, pady=(2, 0))

        # Sub-tab C: Windows certificate store
        f_win = ttk.Frame(self.nb_dsc, padding=6)
        self.nb_dsc.add(f_win, text=" Windows certificate store ")

        w_row1 = tk.Frame(f_win)
        w_row1.pack(fill=tk.X, pady=(2, 4))
        tk.Label(w_row1, text="Store:", font=get_sys_font(8, "bold"), fg="#1E3A8A").pack(side=tk.LEFT)
        self.combo_win_store = ttk.Combobox(w_row1, textvariable=self.win_store_var, values=["MY (Current User)", "ROOT (Local Machine)"], state="readonly", width=18, font=get_sys_font(8))
        self.combo_win_store.pack(side=tk.LEFT, padx=6)
        self.combo_win_store.current(0)
        ttk.Button(w_row1, text="Refresh", style="Tool3D.TButton", command=self.load_win_store_certificates).pack(side=tk.RIGHT)

        tk.Label(f_win, text="Certificate:", font=get_sys_font(8, "bold"), fg="#1E3A8A").pack(anchor=tk.W)
        self.win_cert_combo = ttk.Combobox(f_win, state="readonly", font=get_sys_font(8))
        self.win_cert_combo.pack(fill=tk.X, pady=(2, 4))

        win_note = ("Certificates installed in Windows. If signing fails with 'Keyset does not exist', "
                    "use the USB Token tab. Windows asks for the PIN when you sign. "
                    "If your certificate is missing, also try Store: Local Machine.")
        tk.Label(f_win, text=win_note, font=get_sys_font(7, "italic"), fg="#64748B", wraplength=480, justify="left").pack(anchor=tk.W, pady=(2, 0))

        # Brand Footer Box (A B INFOTECH)
        brand_bar = tk.Frame(card_sec2, bg="#F0F9FF", relief=tk.SOLID, bd=1, padx=8, pady=6)
        brand_bar.pack(fill=tk.X, pady=(6, 0))
        tk.Label(brand_bar, text="A B INFOTECH", font=get_sys_font(10, "bold"), fg="#0369A1", bg="#F0F9FF").pack(anchor=tk.W)
        tk.Label(brand_bar, text="Reliable, Secure & Perfect  |  WhatsApp: 9752318084  |  a-b-infotech.in", font=get_sys_font(7), fg="#0284C7", bg="#F0F9FF").pack(anchor=tk.W)

        # RIGHT COLUMN: Section 3 (Signature details)
        card_sec3 = self._create_3d_card(right_col, pady=(0, 6))
        tk.Label(card_sec3, text="3. Signature details", font=get_sys_font(10, "bold"), fg="#1E40AF", bg="#FFFFFF").pack(anchor=tk.W, pady=(0, 4))

        grid3 = tk.Frame(card_sec3, bg="#FFFFFF")
        grid3.pack(fill=tk.X)

        tk.Label(grid3, text="Reason:", font=get_sys_font(8, "bold"), fg="#334155", bg="#FFFFFF").grid(row=0, column=0, sticky="w", pady=2)
        reasons_list = ["I approve this document", "I am the author of this document", "I have reviewed and verified this document", "Official Authentication"]
        self.combo_reason = ttk.Combobox(grid3, textvariable=self.sign_reason_var, values=reasons_list, font=get_sys_font(8))
        self.combo_reason.grid(row=0, column=1, sticky="ew", padx=(4, 12), pady=2)

        tk.Label(grid3, text="Location:", font=get_sys_font(8, "bold"), fg="#334155", bg="#FFFFFF").grid(row=0, column=2, sticky="w", pady=2)
        ttk.Entry(grid3, textvariable=self.sign_location_var, font=get_sys_font(8), width=16).grid(row=0, column=3, sticky="ew", pady=2)

        tk.Label(grid3, text="Contact info:", font=get_sys_font(8, "bold"), fg="#334155", bg="#FFFFFF").grid(row=1, column=0, sticky="w", pady=2)
        ttk.Entry(grid3, textvariable=self.sign_contact_var, font=get_sys_font(8)).grid(row=1, column=1, sticky="ew", padx=(4, 12), pady=2)

        tk.Label(grid3, text="Hash algorithm:", font=get_sys_font(8, "bold"), fg="#334155", bg="#FFFFFF").grid(row=1, column=2, sticky="w", pady=2)
        ttk.Combobox(grid3, textvariable=self.sign_hash_var, values=["SHA-256", "SHA-384", "SHA-512"], state="readonly", width=14, font=get_sys_font(8)).grid(row=1, column=3, sticky="ew", pady=2)

        tk.Label(grid3, text="Time-stamp server:", font=get_sys_font(8, "bold"), fg="#334155", bg="#FFFFFF").grid(row=2, column=0, sticky="w", pady=2)
        tsa_list = ["DigiCert TSA (Recommended)", "FreeTSA.org", "None (Local clock)"]
        ttk.Combobox(grid3, textvariable=self.sign_tsa_var, values=tsa_list, state="readonly", font=get_sys_font(8)).grid(row=2, column=1, sticky="ew", padx=(4, 12), pady=2)

        tk.Label(grid3, text="Signature type:", font=get_sys_font(8, "bold"), fg="#334155", bg="#FFFFFF").grid(row=2, column=2, sticky="w", pady=2)
        ttk.Combobox(grid3, textvariable=self.sign_type_var, values=["Approval signature (normal)", "Certification signature"], state="readonly", width=14, font=get_sys_font(8)).grid(row=2, column=3, sticky="ew", pady=2)
        grid3.columnconfigure(1, weight=1)

        # RIGHT COLUMN: Section 4 (Appearance)
        card_sec4 = self._create_3d_card(right_col, pady=(0, 6))
        tk.Label(card_sec4, text="4. Appearance", font=get_sys_font(10, "bold"), fg="#1E40AF", bg="#FFFFFF").pack(anchor=tk.W, pady=(0, 2))

        tk.Checkbutton(card_sec4, text="Show the signature on the page (visible signature)", variable=self.chk_visible_sig, font=get_sys_font(8, "bold"), fg="#047857", bg="#FFFFFF").pack(anchor=tk.W)

        scope_row = tk.Frame(card_sec4, bg="#FFFFFF")
        scope_row.pack(fill=tk.X, pady=(2, 2))
        tk.Radiobutton(scope_row, text="All pages", variable=self.sign_page_mode, value="all", bg="#FFFFFF", font=get_sys_font(8)).pack(side=tk.LEFT, padx=(0, 6))
        tk.Radiobutton(scope_row, text="Each page separately", variable=self.sign_page_mode, value="each", bg="#FFFFFF", font=get_sys_font(8)).pack(side=tk.LEFT, padx=6)
        tk.Radiobutton(scope_row, text="First page", variable=self.sign_page_mode, value="first", bg="#FFFFFF", font=get_sys_font(8)).pack(side=tk.LEFT, padx=6)
        tk.Radiobutton(scope_row, text="Last page", variable=self.sign_page_mode, value="last", bg="#FFFFFF", font=get_sys_font(8)).pack(side=tk.LEFT, padx=6)
        tk.Radiobutton(scope_row, text="Page no.", variable=self.sign_page_mode, value="specific", bg="#FFFFFF", font=get_sys_font(8)).pack(side=tk.LEFT, padx=(6, 2))
        ttk.Entry(scope_row, textvariable=self.sign_page_num_var, width=4, font=get_sys_font(8)).pack(side=tk.LEFT)

        pos_bar = tk.Frame(card_sec4, bg="#FFFFFF")
        pos_bar.pack(fill=tk.X, pady=(4, 4))
        btn_pos = ttk.Button(pos_bar, text="📐 Choose position and size on the page...", style="Batch.TButton", command=self.open_sign_position_picker)
        btn_pos.pack(fill=tk.X, ipady=3)

        self.lbl_sign_pos_info = tk.Label(card_sec4, text="📍 Position: Bottom-Right corner (X: 400 pt, Y: 36 pt, W: 160 pt, H: 65 pt)", font=get_sys_font(8), fg="#047857", bg="#ECFDF5", relief=tk.SOLID, bd=1, padx=4, pady=2)
        self.lbl_sign_pos_info.pack(fill=tk.X, pady=(1, 4))

        chk_row = tk.Frame(card_sec4, bg="#FFFFFF")
        chk_row.pack(fill=tk.X, pady=(2, 4))
        tk.Checkbutton(chk_row, text="Name", variable=self.chk_name_var, bg="#FFFFFF", font=get_sys_font(8)).pack(side=tk.LEFT, padx=(0, 4))
        tk.Checkbutton(chk_row, text="Date", variable=self.chk_date_var, bg="#FFFFFF", font=get_sys_font(8)).pack(side=tk.LEFT, padx=4)
        tk.Checkbutton(chk_row, text="Reason", variable=self.chk_reason_var, bg="#FFFFFF", font=get_sys_font(8)).pack(side=tk.LEFT, padx=4)
        tk.Checkbutton(chk_row, text="Location", variable=self.chk_loc_var, bg="#FFFFFF", font=get_sys_font(8)).pack(side=tk.LEFT, padx=4)
        tk.Checkbutton(chk_row, text="Issuer", variable=self.chk_issuer_var, bg="#FFFFFF", font=get_sys_font(8)).pack(side=tk.LEFT, padx=4)
        tk.Checkbutton(chk_row, text="Green tick ✓", variable=self.chk_tick_var, bg="#FFFFFF", font=get_sys_font(8, "bold"), fg="#059669").pack(side=tk.LEFT, padx=4)
        tk.Checkbutton(chk_row, text="Border", variable=self.chk_border_var, bg="#FFFFFF", font=get_sys_font(8)).pack(side=tk.LEFT, padx=4)

        app_grid = tk.Frame(card_sec4, bg="#FFFFFF")
        app_grid.pack(fill=tk.X, pady=(2, 2))
        tk.Label(app_grid, text="Heading:", font=get_sys_font(8, "bold"), fg="#334155", bg="#FFFFFF").grid(row=0, column=0, sticky="w", pady=2)
        ttk.Entry(app_grid, textvariable=self.sign_heading_var, font=get_sys_font(8)).grid(row=0, column=1, sticky="ew", padx=4, pady=2)

        tk.Label(app_grid, text="Extra line / firm:", font=get_sys_font(8, "bold"), fg="#334155", bg="#FFFFFF").grid(row=1, column=0, sticky="w", pady=2)
        ttk.Entry(app_grid, textvariable=self.sign_extra_line_var, font=get_sys_font(8)).grid(row=1, column=1, sticky="ew", padx=4, pady=2)

        tk.Label(app_grid, text="Logo image (optional):", font=get_sys_font(8, "bold"), fg="#334155", bg="#FFFFFF").grid(row=2, column=0, sticky="w", pady=2)
        logo_f = tk.Frame(app_grid, bg="#FFFFFF")
        logo_f.grid(row=2, column=1, sticky="ew", padx=4, pady=2)
        ttk.Entry(logo_f, textvariable=self.sign_logo_path_var, font=get_sys_font(8)).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        ttk.Button(logo_f, text="Logo...", style="Tool3D.TButton", command=self.browse_sign_logo).pack(side=tk.RIGHT)
        app_grid.columnconfigure(1, weight=1)

        # RIGHT COLUMN: Section 5 (Protect the signed PDF)
        card_sec5 = self._create_3d_card(right_col, pady=(0, 4))
        tk.Label(card_sec5, text="5. Protect the signed PDF (optional)", font=get_sys_font(10, "bold"), fg="#1E40AF", bg="#FFFFFF").pack(anchor=tk.W, pady=(0, 2))

        tk.Checkbutton(card_sec5, text="Protect with a password (256-bit AES)", variable=self.chk_protect_var, font=get_sys_font(8, "bold"), fg="#DC2626", bg="#FFFFFF").pack(anchor=tk.W)

        pw_grid = tk.Frame(card_sec5, bg="#FFFFFF")
        pw_grid.pack(fill=tk.X, pady=(2, 2))
        tk.Label(pw_grid, text="Password to open (optional):", font=get_sys_font(8), bg="#FFFFFF").grid(row=0, column=0, sticky="w")
        ttk.Entry(pw_grid, textvariable=self.protect_open_pw_var, show="*", width=18).grid(row=0, column=1, padx=4, sticky="w")

        tk.Label(pw_grid, text="Permissions password:", font=get_sys_font(8), bg="#FFFFFF").grid(row=0, column=2, sticky="w", padx=(8, 0))
        ttk.Entry(pw_grid, textvariable=self.protect_perm_pw_var, show="*", width=18).grid(row=0, column=3, padx=4, sticky="w")

        perm_row = tk.Frame(card_sec5, bg="#FFFFFF")
        perm_row.pack(fill=tk.X, pady=(2, 0))
        tk.Checkbutton(perm_row, text="Allow printing", variable=self.chk_allow_print, bg="#FFFFFF", font=get_sys_font(8)).pack(side=tk.LEFT, padx=(0, 8))
        tk.Checkbutton(perm_row, text="Allow copying text", variable=self.chk_allow_copy, bg="#FFFFFF", font=get_sys_font(8)).pack(side=tk.LEFT, padx=8)
        tk.Checkbutton(perm_row, text="Allow editing", variable=self.chk_allow_edit, bg="#FFFFFF", font=get_sys_font(8)).pack(side=tk.LEFT, padx=8)

        # BOTTOM ACTION BAR
        bot_bar = tk.Frame(parent, bg="#F1F5F9", padx=8, pady=4)
        bot_bar.pack(side=tk.BOTTOM, fill=tk.X)

        self.sign_progress = ttk.Progressbar(bot_bar, mode='determinate')
        self.sign_progress.pack(fill=tk.X, pady=(0, 4))

        act_row = tk.Frame(bot_bar, bg="#F1F5F9")
        act_row.pack(fill=tk.X)

        sign_shadow = tk.Frame(act_row, bg="#CBD5E1", bd=0)
        sign_shadow.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        sign_rim = tk.Frame(sign_shadow, bg="#34D399", bd=0)
        sign_rim.pack(fill=tk.BOTH, expand=True, padx=(1, 2), pady=(1, 2))
        self.btn_sign_docs = ttk.Button(sign_rim, text="✍️ SIGN DOCUMENTS", style="Primary3D.TButton", command=self.start_document_signing)
        self.btn_sign_docs.pack(fill=tk.BOTH, expand=True, ipady=6)

        self.btn_open_folder = ttk.Button(act_row, text="Open output folder", style="Tool3D.TButton", command=self.open_output_folder)
        self.btn_open_folder.pack(side=tk.RIGHT, padx=4)

        self.lbl_sign_status = tk.Label(bot_bar, text="Ready to sign documents.", font=get_sys_font(9, "bold"), fg="#047857", bg="#F1F5F9", anchor="w")
        self.lbl_sign_status.pack(fill=tk.X, pady=(2, 0))

        self.load_token_certificates()

    # =========================================================================
    # TAB 2: 🖼️ IMAGE STAMP
    # =========================================================================
    def _build_tab_stamp(self, parent):
        outer = tk.Frame(parent, bg="#F1F5F9")
        outer.pack(fill=tk.BOTH, expand=True, padx=12, pady=8)

        card1 = self._create_3d_card(outer, pady=(0, 8))
        tk.Label(card1, text="1. Select PDF Documents to Stamp", font=get_sys_font(10, "bold"), fg="#1E40AF", bg="#FFFFFF").pack(anchor=tk.W, pady=(0, 4))
        
        lf = tk.Frame(card1, bg="#FFFFFF")
        lf.pack(fill=tk.X, pady=(0, 4))
        self.stamp_listbox = tk.Listbox(lf, height=4, font=get_sys_font(8), selectmode=tk.EXTENDED)
        self.stamp_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb = ttk.Scrollbar(lf, orient=tk.VERTICAL, command=self.stamp_listbox.yview)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.stamp_listbox.config(yscrollcommand=sb.set)

        b_row = tk.Frame(card1, bg="#FFFFFF")
        b_row.pack(fill=tk.X, pady=(2, 2))
        ttk.Button(b_row, text="Add files...", style="Tool3D.TButton", command=self.stamp_add_files).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(b_row, text="Add folder...", style="Tool3D.TButton", command=self.stamp_add_folder).pack(side=tk.LEFT, padx=4)
        ttk.Button(b_row, text="Remove", style="Tool3D.TButton", command=self.stamp_remove).pack(side=tk.LEFT, padx=4)
        ttk.Button(b_row, text="Clear", style="Tool3D.TButton", command=self.stamp_clear).pack(side=tk.LEFT, padx=4)

        card2 = self._create_3d_card(outer, pady=(0, 8))
        tk.Label(card2, text="2. Stamp Image & Placement Settings", font=get_sys_font(10, "bold"), fg="#1E40AF", bg="#FFFFFF").pack(anchor=tk.W, pady=(0, 4))

        i_row = tk.Frame(card2, bg="#FFFFFF")
        i_row.pack(fill=tk.X, pady=(2, 4))
        tk.Label(i_row, text="Stamp Image (PNG/JPG):", font=get_sys_font(8, "bold"), bg="#FFFFFF").pack(side=tk.LEFT)
        ttk.Entry(i_row, textvariable=self.stamp_img_path, font=get_sys_font(8)).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        ttk.Button(i_row, text="Browse Image...", style="Tool3D.TButton", command=self.browse_stamp_image).pack(side=tk.RIGHT)

        pos_row = tk.Frame(card2, bg="#FFFFFF")
        pos_row.pack(fill=tk.X, pady=(4, 4))
        tk.Label(pos_row, text="Position:", font=get_sys_font(8, "bold"), bg="#FFFFFF").pack(side=tk.LEFT, padx=(0, 6))
        for p_val, p_lbl in [("bottom_right", "Bottom-Right"), ("bottom_left", "Bottom-Left"), ("top_right", "Top-Right"), ("top_left", "Top-Left"), ("center", "Center")]:
            tk.Radiobutton(pos_row, text=p_lbl, variable=self.stamp_pos_mode, value=p_val, bg="#FFFFFF", font=get_sys_font(8)).pack(side=tk.LEFT, padx=4)
        ttk.Button(pos_row, text="📐 Choose on Page...", style="Tool3D.TButton", command=self.open_stamp_position_picker).pack(side=tk.LEFT, padx=8)

        prop_row = tk.Frame(card2, bg="#FFFFFF")
        prop_row.pack(fill=tk.X, pady=(4, 2))
        tk.Label(prop_row, text="Pages:", font=get_sys_font(8, "bold"), bg="#FFFFFF").pack(side=tk.LEFT, padx=(0, 4))
        for s_val, s_lbl in [("all", "All pages"), ("first", "First page"), ("last", "Last page"), ("custom", "Page no.")]:
            tk.Radiobutton(prop_row, text=s_lbl, variable=self.stamp_page_scope, value=s_val, bg="#FFFFFF", font=get_sys_font(8)).pack(side=tk.LEFT, padx=3)
        ttk.Entry(prop_row, textvariable=self.stamp_page_num, width=4).pack(side=tk.LEFT, padx=2)

        tk.Label(prop_row, text="Size (WxH pt):", font=get_sys_font(8, "bold"), bg="#FFFFFF").pack(side=tk.LEFT, padx=(12, 4))
        ttk.Entry(prop_row, textvariable=self.stamp_width_var, width=5).pack(side=tk.LEFT)
        tk.Label(prop_row, text="x", bg="#FFFFFF").pack(side=tk.LEFT, padx=2)
        ttk.Entry(prop_row, textvariable=self.stamp_height_var, width=5).pack(side=tk.LEFT)

        card3 = self._create_3d_card(outer, pady=(0, 8))
        tk.Label(card3, text="3. Output Options", font=get_sys_font(10, "bold"), fg="#1E40AF", bg="#FFFFFF").pack(anchor=tk.W, pady=(0, 4))
        tk.Radiobutton(card3, text="Save beside original (_stamped.pdf)", variable=self.stamp_out_mode, value="beside", bg="#FFFFFF", font=get_sys_font(8)).pack(anchor=tk.W)
        s_out_row = tk.Frame(card3, bg="#FFFFFF")
        s_out_row.pack(fill=tk.X, pady=(2, 2))
        tk.Radiobutton(s_out_row, text="Save in folder:", variable=self.stamp_out_mode, value="folder", bg="#FFFFFF", font=get_sys_font(8)).pack(side=tk.LEFT)
        ttk.Entry(s_out_row, textvariable=self.stamp_out_dir, font=get_sys_font(8)).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)
        ttk.Button(s_out_row, text="Browse...", style="Tool3D.TButton", command=self.stamp_browse_out_folder).pack(side=tk.RIGHT)

        b_bot = tk.Frame(outer, bg="#F1F5F9")
        b_bot.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(b_bot, text="🖼️ APPLY STAMP TO DOCUMENTS", style="Primary3D.TButton", command=self.start_image_stamping).pack(fill=tk.X, ipady=5)
        self.lbl_stamp_status = tk.Label(b_bot, text="", font=get_sys_font(9, "bold"), fg="#047857", bg="#F1F5F9")
        self.lbl_stamp_status.pack(pady=4)

    # =========================================================================
    # TAB 3: 🗜️ COMPRESS
    # =========================================================================
    def _build_tab_compress(self, parent):
        outer = tk.Frame(parent, bg="#F1F5F9")
        outer.pack(fill=tk.BOTH, expand=True, padx=12, pady=8)

        card1 = self._create_3d_card(outer, pady=(0, 6))
        tk.Label(card1, text="1. Select PDF Documents to Compress", font=get_sys_font(10, "bold"), fg="#1E40AF", bg="#FFFFFF").pack(anchor=tk.W, pady=(0, 4))
        
        lf = tk.Frame(card1, bg="#FFFFFF")
        lf.pack(fill=tk.X, pady=(0, 4))
        self.compress_listbox = tk.Listbox(lf, height=4, font=get_sys_font(8), selectmode=tk.EXTENDED)
        self.compress_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb = ttk.Scrollbar(lf, orient=tk.VERTICAL, command=self.compress_listbox.yview)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.compress_listbox.config(yscrollcommand=sb.set)

        b_row = tk.Frame(card1, bg="#FFFFFF")
        b_row.pack(fill=tk.X, pady=(2, 2))
        ttk.Button(b_row, text="Add files...", style="Tool3D.TButton", command=self.compress_add_files).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(b_row, text="Add folder...", style="Tool3D.TButton", command=self.compress_add_folder).pack(side=tk.LEFT, padx=4)
        ttk.Button(b_row, text="Remove", style="Tool3D.TButton", command=self.compress_remove).pack(side=tk.LEFT, padx=4)
        ttk.Button(b_row, text="Clear", style="Tool3D.TButton", command=self.compress_clear).pack(side=tk.LEFT, padx=4)

        card2 = self._create_3d_card(outer, pady=(0, 6))
        tk.Label(card2, text="2. Compression Profiles & Color Mode", font=get_sys_font(10, "bold"), fg="#1E40AF", bg="#FFFFFF").pack(anchor=tk.W, pady=(0, 4))

        p_row = tk.Frame(card2, bg="#FFFFFF")
        p_row.pack(fill=tk.X, pady=(2, 2))
        tk.Radiobutton(p_row, text="Medium Compression (Recommended - 120 DPI, 75% quality)", variable=self.compress_level_var, value="medium", bg="#FFFFFF", font=get_sys_font(8, "bold"), fg="#047857").pack(anchor=tk.W)
        tk.Radiobutton(p_row, text="High Compression (Smallest file size - 72 DPI, 50% quality)", variable=self.compress_level_var, value="high", bg="#FFFFFF", font=get_sys_font(8)).pack(anchor=tk.W)
        tk.Radiobutton(p_row, text="Low Compression (High print quality - 150 DPI, 85% quality)", variable=self.compress_level_var, value="low", bg="#FFFFFF", font=get_sys_font(8)).pack(anchor=tk.W)

        c_row = tk.Frame(card2, bg="#FFFFFF")
        c_row.pack(fill=tk.X, pady=(4, 2))
        tk.Label(c_row, text="Color Mode:", font=get_sys_font(8, "bold"), bg="#FFFFFF").pack(side=tk.LEFT, padx=(0, 6))
        tk.Radiobutton(c_row, text="Color", variable=self.compress_color_var, value="color", bg="#FFFFFF", font=get_sys_font(8)).pack(side=tk.LEFT, padx=4)
        tk.Radiobutton(c_row, text="Grayscale", variable=self.compress_color_var, value="grayscale", bg="#FFFFFF", font=get_sys_font(8)).pack(side=tk.LEFT, padx=4)
        tk.Radiobutton(c_row, text="Black & White (Monochrome)", variable=self.compress_color_var, value="bw", bg="#FFFFFF", font=get_sys_font(8)).pack(side=tk.LEFT, padx=4)

        card3 = self._create_3d_card(outer, pady=(0, 6))
        tk.Label(card3, text="3. Compression Results Table", font=get_sys_font(10, "bold"), fg="#1E40AF", bg="#FFFFFF").pack(anchor=tk.W, pady=(0, 4))
        
        self.tree_compress = ttk.Treeview(card3, columns=("file", "orig", "comp", "saved", "pct"), show="headings", height=5)
        self.tree_compress.heading("file", text="File Name")
        self.tree_compress.heading("orig", text="Original Size")
        self.tree_compress.heading("comp", text="Compressed Size")
        self.tree_compress.heading("saved", text="Space Saved")
        self.tree_compress.heading("pct", text="Reduction %")
        self.tree_compress.column("file", width=340)
        self.tree_compress.column("orig", width=120)
        self.tree_compress.column("comp", width=120)
        self.tree_compress.column("saved", width=120)
        self.tree_compress.column("pct", width=100)
        self.tree_compress.pack(fill=tk.X)

        b_bot = tk.Frame(outer, bg="#F1F5F9")
        b_bot.pack(fill=tk.X, pady=(6, 0))
        ttk.Button(b_bot, text="🗜️ COMPRESS PDF DOCUMENTS", style="Primary3D.TButton", command=self.start_compressing).pack(fill=tk.X, ipady=5)
        self.lbl_compress_status = tk.Label(b_bot, text="", font=get_sys_font(9, "bold"), fg="#047857", bg="#F1F5F9")
        self.lbl_compress_status.pack(pady=4)

    # =========================================================================
    # TAB 4: 📑 MERGE
    # =========================================================================
    def _build_tab_merge(self, parent):
        outer = tk.Frame(parent, bg="#F1F5F9")
        outer.pack(fill=tk.BOTH, expand=True, padx=12, pady=8)

        card1 = self._create_3d_card(outer, pady=(0, 8))
        tk.Label(card1, text="1. PDF Documents Order & Page Ranges", font=get_sys_font(10, "bold"), fg="#1E40AF", bg="#FFFFFF").pack(anchor=tk.W, pady=(0, 4))

        self.tree_merge = ttk.Treeview(card1, columns=("num", "name", "range", "path"), show="headings", height=8)
        self.tree_merge.heading("num", text="#")
        self.tree_merge.heading("name", text="File Name")
        self.tree_merge.heading("range", text="Pages (e.g. all, 1-3, 5)")
        self.tree_merge.heading("path", text="Full Path")
        self.tree_merge.column("num", width=40)
        self.tree_merge.column("name", width=280)
        self.tree_merge.column("range", width=180)
        self.tree_merge.column("path", width=400)
        self.tree_merge.pack(fill=tk.X, pady=(0, 4))
        self.tree_merge.bind("<<TreeviewSelect>>", self._on_merge_select)

        b_row = tk.Frame(card1, bg="#FFFFFF")
        b_row.pack(fill=tk.X, pady=(2, 4))
        ttk.Button(b_row, text="Add files...", style="Tool3D.TButton", command=self.merge_add_files).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(b_row, text="Add folder...", style="Tool3D.TButton", command=self.merge_add_folder).pack(side=tk.LEFT, padx=4)
        ttk.Button(b_row, text="⬆ Move Up", style="Tool3D.TButton", command=self.merge_move_up).pack(side=tk.LEFT, padx=4)
        ttk.Button(b_row, text="⬇ Move Down", style="Tool3D.TButton", command=self.merge_move_down).pack(side=tk.LEFT, padx=4)
        ttk.Button(b_row, text="Remove", style="Tool3D.TButton", command=self.merge_remove).pack(side=tk.LEFT, padx=4)
        ttk.Button(b_row, text="Clear", style="Tool3D.TButton", command=self.merge_clear).pack(side=tk.LEFT, padx=4)

        edit_row = tk.Frame(card1, bg="#FFFFFF")
        edit_row.pack(fill=tk.X, pady=(4, 2))
        tk.Label(edit_row, text="Pages to include for selected PDF:", font=get_sys_font(8, "bold"), bg="#FFFFFF").pack(side=tk.LEFT)
        self.entry_merge_range = ttk.Entry(edit_row, font=get_sys_font(8), width=18)
        self.entry_merge_range.pack(side=tk.LEFT, padx=6)
        ttk.Button(edit_row, text="Set Range", style="Tool3D.TButton", command=self.merge_set_range).pack(side=tk.LEFT)

        card2 = self._create_3d_card(outer, pady=(0, 8))
        tk.Label(card2, text="2. Output Merged File Destination", font=get_sys_font(10, "bold"), fg="#1E40AF", bg="#FFFFFF").pack(anchor=tk.W, pady=(0, 4))
        m_row = tk.Frame(card2, bg="#FFFFFF")
        m_row.pack(fill=tk.X, pady=(2, 2))
        ttk.Entry(m_row, textvariable=self.merge_out_path, font=get_sys_font(8)).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        ttk.Button(m_row, text="Browse Destination...", style="Tool3D.TButton", command=self.merge_browse_out).pack(side=tk.RIGHT)

        b_bot = tk.Frame(outer, bg="#F1F5F9")
        b_bot.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(b_bot, text="📑 MERGE ALL PDFS", style="Primary3D.TButton", command=self.start_merging).pack(fill=tk.X, ipady=5)
        self.lbl_merge_status = tk.Label(b_bot, text="", font=get_sys_font(9, "bold"), fg="#047857", bg="#F1F5F9")
        self.lbl_merge_status.pack(pady=4)

    # =========================================================================
    # TAB 5: 🔍 VERIFY
    # =========================================================================
    def _build_tab_verify(self, parent):
        outer = tk.Frame(parent, bg="#F1F5F9")
        outer.pack(fill=tk.BOTH, expand=True, padx=12, pady=8)

        card1 = self._create_3d_card(outer, pady=(0, 8))
        tk.Label(card1, text="1. Select Digitally Signed PDF to Verify", font=get_sys_font(10, "bold"), fg="#1E40AF", bg="#FFFFFF").pack(anchor=tk.W, pady=(0, 4))

        v_row = tk.Frame(card1, bg="#FFFFFF")
        v_row.pack(fill=tk.X, pady=(2, 4))
        ttk.Entry(v_row, textvariable=self.verify_pdf_path, font=get_sys_font(8)).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        ttk.Button(v_row, text="Browse PDF...", style="Tool3D.TButton", command=self.verify_browse_file).pack(side=tk.RIGHT)

        ttk.Button(card1, text="🔍 VERIFY DIGITAL SIGNATURES", style="Primary3D.TButton", command=self.start_verification).pack(fill=tk.X, ipady=4, pady=(6, 2))

        card2 = self._create_3d_card(outer, pady=(0, 8))
        tk.Label(card2, text="2. Cryptographic Verification Results", font=get_sys_font(10, "bold"), fg="#1E40AF", bg="#FFFFFF").pack(anchor=tk.W, pady=(0, 4))

        self.lbl_verify_banner = tk.Label(card2, text="Select a signed PDF and click 'Verify Digital Signatures'.", font=get_sys_font(10, "bold"), fg="#64748B", bg="#F8FAFC", padx=8, pady=8, relief=tk.SOLID, bd=1)
        self.lbl_verify_banner.pack(fill=tk.X, pady=(0, 6))

        self.txt_verify_report = tk.Text(card2, height=14, font=get_sys_font(8), bg="#FFFFFF", bd=1, relief=tk.SOLID)
        self.txt_verify_report.pack(fill=tk.BOTH, expand=True)

    # =========================================================================
    # TAB 6: 🛠️ TOOLS (Split, Rotate, Extract, Delete, Protect, Unlock)
    # =========================================================================
    def _build_tab_tools(self, parent):
        outer = tk.Frame(parent, bg="#F1F5F9")
        outer.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)

        nb_tools = ttk.Notebook(outer)
        nb_tools.pack(fill=tk.BOTH, expand=True)

        f_split = ttk.Frame(nb_tools, padding=12)
        nb_tools.add(f_split, text=" Split PDF ")
        self._build_tool_split(f_split)

        f_rot = ttk.Frame(nb_tools, padding=12)
        nb_tools.add(f_rot, text=" Rotate Pages ")
        self._build_tool_rotate(f_rot)

        f_ext = ttk.Frame(nb_tools, padding=12)
        nb_tools.add(f_ext, text=" Extract Pages ")
        self._build_tool_extract(f_ext)

        f_del = ttk.Frame(nb_tools, padding=12)
        nb_tools.add(f_del, text=" Delete Pages ")
        self._build_tool_delete(f_del)

        f_prot = ttk.Frame(nb_tools, padding=12)
        nb_tools.add(f_prot, text=" Protect / Encrypt ")
        self._build_tool_protect(f_prot)

        f_unl = ttk.Frame(nb_tools, padding=12)
        nb_tools.add(f_unl, text=" Unlock / Decrypt ")
        self._build_tool_unlock(f_unl)

    def _build_tool_split(self, parent):
        tk.Label(parent, text="Select PDF to Split:", font=get_sys_font(9, "bold"), fg="#1E40AF").pack(anchor=tk.W)
        r1 = tk.Frame(parent); r1.pack(fill=tk.X, pady=(2, 6))
        ttk.Entry(r1, textvariable=self.tool_split_pdf, font=get_sys_font(8)).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        ttk.Button(r1, text="Browse...", style="Tool3D.TButton", command=lambda: self._browse_single_file(self.tool_split_pdf)).pack(side=tk.RIGHT)

        tk.Radiobutton(parent, text="Split each page into a separate PDF file", variable=self.tool_split_mode, value="single", font=get_sys_font(8)).pack(anchor=tk.W)
        r2 = tk.Frame(parent); r2.pack(fill=tk.X, pady=(2, 6))
        tk.Radiobutton(r2, text="Split by page ranges (e.g. 1-3, 4-6):", variable=self.tool_split_mode, value="range", font=get_sys_font(8)).pack(side=tk.LEFT)
        ttk.Entry(r2, textvariable=self.tool_split_ranges, width=20).pack(side=tk.LEFT, padx=6)

        tk.Label(parent, text="Output Folder:", font=get_sys_font(9, "bold"), fg="#1E40AF").pack(anchor=tk.W)
        r3 = tk.Frame(parent); r3.pack(fill=tk.X, pady=(2, 10))
        ttk.Entry(r3, textvariable=self.tool_split_out_dir, font=get_sys_font(8)).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        ttk.Button(r3, text="Browse Folder...", style="Tool3D.TButton", command=lambda: self._browse_single_folder(self.tool_split_out_dir)).pack(side=tk.RIGHT)

        ttk.Button(parent, text="✂️ SPLIT PDF", style="Primary3D.TButton", command=self.exec_split_pdf).pack(fill=tk.X, ipady=4)
        self.lbl_tool_split_status = tk.Label(parent, text="", font=get_sys_font(8, "bold"), fg="#047857")
        self.lbl_tool_split_status.pack(pady=4)

    def _build_tool_rotate(self, parent):
        tk.Label(parent, text="Select PDF to Rotate:", font=get_sys_font(9, "bold"), fg="#1E40AF").pack(anchor=tk.W)
        r1 = tk.Frame(parent); r1.pack(fill=tk.X, pady=(2, 6))
        ttk.Entry(r1, textvariable=self.tool_rot_pdf, font=get_sys_font(8)).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        ttk.Button(r1, text="Browse...", style="Tool3D.TButton", command=lambda: self._browse_single_file(self.tool_rot_pdf)).pack(side=tk.RIGHT)

        tk.Label(parent, text="Rotation Angle:", font=get_sys_font(8, "bold")).pack(anchor=tk.W)
        a_row = tk.Frame(parent); a_row.pack(fill=tk.X, pady=(2, 6))
        tk.Radiobutton(a_row, text="90° Clockwise", variable=self.tool_rot_angle, value=90).pack(side=tk.LEFT, padx=4)
        tk.Radiobutton(a_row, text="180° Half Turn", variable=self.tool_rot_angle, value=180).pack(side=tk.LEFT, padx=4)
        tk.Radiobutton(a_row, text="270° Counter-Clockwise", variable=self.tool_rot_angle, value=270).pack(side=tk.LEFT, padx=4)

        tk.Label(parent, text="Pages to Rotate:", font=get_sys_font(8, "bold")).pack(anchor=tk.W)
        s_row = tk.Frame(parent); s_row.pack(fill=tk.X, pady=(2, 10))
        tk.Radiobutton(s_row, text="All pages", variable=self.tool_rot_scope, value="all").pack(side=tk.LEFT, padx=4)
        tk.Radiobutton(s_row, text="Even pages", variable=self.tool_rot_scope, value="even").pack(side=tk.LEFT, padx=4)
        tk.Radiobutton(s_row, text="Odd pages", variable=self.tool_rot_scope, value="odd").pack(side=tk.LEFT, padx=4)
        tk.Radiobutton(s_row, text="Specific pages:", variable=self.tool_rot_scope, value="specific").pack(side=tk.LEFT, padx=4)
        ttk.Entry(s_row, textvariable=self.tool_rot_spec, width=12).pack(side=tk.LEFT)

        ttk.Button(parent, text="🔄 ROTATE & SAVE PDF", style="Primary3D.TButton", command=self.exec_rotate_pdf).pack(fill=tk.X, ipady=4)
        self.lbl_tool_rot_status = tk.Label(parent, text="", font=get_sys_font(8, "bold"), fg="#047857")
        self.lbl_tool_rot_status.pack(pady=4)

    def _build_tool_extract(self, parent):
        tk.Label(parent, text="Select PDF to Extract Pages From:", font=get_sys_font(9, "bold"), fg="#1E40AF").pack(anchor=tk.W)
        r1 = tk.Frame(parent); r1.pack(fill=tk.X, pady=(2, 6))
        ttk.Entry(r1, textvariable=self.tool_ext_pdf, font=get_sys_font(8)).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        ttk.Button(r1, text="Browse...", style="Tool3D.TButton", command=lambda: self._browse_single_file(self.tool_ext_pdf)).pack(side=tk.RIGHT)

        tk.Label(parent, text="Page Numbers to Extract (e.g. 1-3, 5, 8):", font=get_sys_font(8, "bold")).pack(anchor=tk.W)
        ttk.Entry(parent, textvariable=self.tool_ext_pages, font=get_sys_font(8)).pack(fill=tk.X, pady=(2, 10))

        ttk.Button(parent, text="📤 EXTRACT PAGES", style="Primary3D.TButton", command=self.exec_extract_pages).pack(fill=tk.X, ipady=4)
        self.lbl_tool_ext_status = tk.Label(parent, text="", font=get_sys_font(8, "bold"), fg="#047857")
        self.lbl_tool_ext_status.pack(pady=4)

    def _build_tool_delete(self, parent):
        tk.Label(parent, text="Select PDF to Delete Pages From:", font=get_sys_font(9, "bold"), fg="#1E40AF").pack(anchor=tk.W)
        r1 = tk.Frame(parent); r1.pack(fill=tk.X, pady=(2, 6))
        ttk.Entry(r1, textvariable=self.tool_del_pdf, font=get_sys_font(8)).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        ttk.Button(r1, text="Browse...", style="Tool3D.TButton", command=lambda: self._browse_single_file(self.tool_del_pdf)).pack(side=tk.RIGHT)

        tk.Label(parent, text="Page Numbers to Delete (e.g. 2, 4, 6):", font=get_sys_font(8, "bold")).pack(anchor=tk.W)
        ttk.Entry(parent, textvariable=self.tool_del_pages, font=get_sys_font(8)).pack(fill=tk.X, pady=(2, 10))

        ttk.Button(parent, text="🗑️ DELETE PAGES & SAVE", style="Primary3D.TButton", command=self.exec_delete_pages).pack(fill=tk.X, ipady=4)
        self.lbl_tool_del_status = tk.Label(parent, text="", font=get_sys_font(8, "bold"), fg="#047857")
        self.lbl_tool_del_status.pack(pady=4)

    def _build_tool_protect(self, parent):
        tk.Label(parent, text="Select PDF to Encrypt & Protect:", font=get_sys_font(9, "bold"), fg="#1E40AF").pack(anchor=tk.W)
        r1 = tk.Frame(parent); r1.pack(fill=tk.X, pady=(2, 6))
        ttk.Entry(r1, textvariable=self.tool_prot_pdf, font=get_sys_font(8)).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        ttk.Button(r1, text="Browse...", style="Tool3D.TButton", command=lambda: self._browse_single_file(self.tool_prot_pdf)).pack(side=tk.RIGHT)

        grid = tk.Frame(parent)
        grid.pack(fill=tk.X, pady=(2, 6))
        tk.Label(grid, text="Open Password (User):", font=get_sys_font(8, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Entry(grid, textvariable=self.tool_prot_open, show="*").grid(row=0, column=1, padx=4, sticky="ew")
        tk.Label(grid, text="Permissions Password (Owner):", font=get_sys_font(8, "bold")).grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(grid, textvariable=self.tool_prot_perm, show="*").grid(row=1, column=1, padx=4, pady=4, sticky="ew")

        p_row = tk.Frame(parent); p_row.pack(fill=tk.X, pady=(2, 10))
        tk.Checkbutton(p_row, text="Allow Printing", variable=self.tool_prot_print).pack(side=tk.LEFT, padx=4)
        tk.Checkbutton(p_row, text="Allow Copying Text", variable=self.tool_prot_copy).pack(side=tk.LEFT, padx=4)
        tk.Checkbutton(p_row, text="Allow Editing", variable=self.tool_prot_edit).pack(side=tk.LEFT, padx=4)

        ttk.Button(parent, text="🔒 ENCRYPT WITH 256-BIT AES", style="Primary3D.TButton", command=self.exec_protect_pdf).pack(fill=tk.X, ipady=4)
        self.lbl_tool_prot_status = tk.Label(parent, text="", font=get_sys_font(8, "bold"), fg="#047857")
        self.lbl_tool_prot_status.pack(pady=4)

    def _build_tool_unlock(self, parent):
        tk.Label(parent, text="Select Password-Protected PDF to Unlock:", font=get_sys_font(9, "bold"), fg="#1E40AF").pack(anchor=tk.W)
        r1 = tk.Frame(parent); r1.pack(fill=tk.X, pady=(2, 6))
        ttk.Entry(r1, textvariable=self.tool_unl_pdf, font=get_sys_font(8)).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        ttk.Button(r1, text="Browse...", style="Tool3D.TButton", command=lambda: self._browse_single_file(self.tool_unl_pdf)).pack(side=tk.RIGHT)

        tk.Label(parent, text="Enter PDF Password to Unlock:", font=get_sys_font(8, "bold")).pack(anchor=tk.W)
        ttk.Entry(parent, textvariable=self.tool_unl_pw, show="*").pack(fill=tk.X, pady=(2, 10))

        ttk.Button(parent, text="🔓 UNLOCK & DECRYPT PDF", style="Primary3D.TButton", command=self.exec_unlock_pdf).pack(fill=tk.X, ipady=4)
        self.lbl_tool_unl_status = tk.Label(parent, text="", font=get_sys_font(8, "bold"), fg="#047857")
        self.lbl_tool_unl_status.pack(pady=4)

    # =========================================================================
    # TAB 7: 📖 OCR & EDIT (License Integrated Feature)
    # =========================================================================
    def _build_tab_ocr_edit(self, parent):
        outer = tk.Frame(parent, bg="#F1F5F9")
        outer.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)

        is_lic = LicenseManager.is_activated()
        lic_box = tk.Frame(outer, bg="#ECFDF5" if is_lic else "#FFFBEB", relief=tk.SOLID, bd=1, padx=10, pady=8)
        lic_box.pack(fill=tk.X, pady=(0, 8))
        if is_lic:
            tk.Label(lic_box, text="⭐ Professional Suite Active: OCR Recognition, Text Redaction & Watermarking Unlocked", font=get_sys_font(9, "bold"), fg="#065F46", bg="#ECFDF5").pack(side=tk.LEFT)
        else:
            tk.Label(lic_box, text="🔒 Professional Feature: Activate License to Unlock Full PDF OCR Recognition & Redaction", font=get_sys_font(9, "bold"), fg="#92400E", bg="#FFFBEB").pack(side=tk.LEFT)
            ttk.Button(lic_box, text="Activate License", style="BuyDSC.TButton", command=self._show_activation_screen).pack(side=tk.RIGHT)

        nb_ocr = ttk.Notebook(outer)
        nb_ocr.pack(fill=tk.BOTH, expand=True)

        f_ocr = ttk.Frame(nb_ocr, padding=10)
        nb_ocr.add(f_ocr, text=" OCR & Text Extraction ")
        
        tk.Label(f_ocr, text="Select PDF for OCR & Text Extraction:", font=get_sys_font(9, "bold"), fg="#1E40AF").pack(anchor=tk.W)
        r1 = tk.Frame(f_ocr); r1.pack(fill=tk.X, pady=(2, 6))
        ttk.Entry(r1, textvariable=self.ocr_pdf_path, font=get_sys_font(8)).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        ttk.Button(r1, text="Browse...", style="Tool3D.TButton", command=lambda: self._browse_single_file(self.ocr_pdf_path)).pack(side=tk.RIGHT)

        r2 = tk.Frame(f_ocr); r2.pack(fill=tk.X, pady=(2, 6))
        tk.Label(r2, text="Pages to Process:", font=get_sys_font(8, "bold")).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Entry(r2, textvariable=self.ocr_page_scope, width=12).pack(side=tk.LEFT, padx=4)
        tk.Label(r2, text="(e.g. 'all' or '1-5')", font=get_sys_font(8, "italic"), fg="#64748B").pack(side=tk.LEFT)

        ttk.Button(f_ocr, text="🔍 RUN OCR & EXTRACT TEXT", style="Primary3D.TButton", command=self.exec_ocr_extract).pack(fill=tk.X, ipady=4, pady=(2, 6))

        tk.Label(f_ocr, text="Extracted Content Preview:", font=get_sys_font(8, "bold")).pack(anchor=tk.W)
        self.txt_ocr_preview = tk.Text(f_ocr, height=10, font=get_sys_font(8), bg="#FFFFFF", bd=1, relief=tk.SOLID)
        self.txt_ocr_preview.pack(fill=tk.BOTH, expand=True, pady=(2, 4))

        b_row = tk.Frame(f_ocr)
        b_row.pack(fill=tk.X)
        ttk.Button(b_row, text="💾 Save to .TXT File", style="Tool3D.TButton", command=self.save_ocr_to_file).pack(side=tk.LEFT)

        f_redact = ttk.Frame(nb_ocr, padding=10)
        nb_ocr.add(f_redact, text=" Search & Redact / Replace ")

        tk.Label(f_redact, text="Select PDF to Redact Sensitive Text (Aadhaar, PAN, phone numbers, etc.):", font=get_sys_font(9, "bold"), fg="#1E40AF").pack(anchor=tk.W)
        r_f1 = tk.Frame(f_redact); r_f1.pack(fill=tk.X, pady=(2, 6))
        ttk.Entry(r_f1, textvariable=self.redact_pdf_path, font=get_sys_font(8)).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        ttk.Button(r_f1, text="Browse...", style="Tool3D.TButton", command=lambda: self._browse_single_file(self.redact_pdf_path)).pack(side=tk.RIGHT)

        tk.Label(f_redact, text="Text to Search & Redact:", font=get_sys_font(8, "bold")).pack(anchor=tk.W)
        ttk.Entry(f_redact, textvariable=self.redact_term_var, font=get_sys_font(8)).pack(fill=tk.X, pady=(2, 6))

        tk.Radiobutton(f_redact, text="Redact permanently (Blackout / Cover sensitive text)", variable=self.redact_mode_var, value="blackout", font=get_sys_font(8, "bold"), fg="#DC2626").pack(anchor=tk.W)
        r_f2 = tk.Frame(f_redact); r_f2.pack(fill=tk.X, pady=(2, 10))
        tk.Radiobutton(r_f2, text="Replace text with:", variable=self.redact_mode_var, value="replace", font=get_sys_font(8)).pack(side=tk.LEFT)
        ttk.Entry(r_f2, textvariable=self.redact_replace_var, font=get_sys_font(8), width=24).pack(side=tk.LEFT, padx=6)

        ttk.Button(f_redact, text="🔒 APPLY REDACTION & SAVE PDF", style="Primary3D.TButton", command=self.exec_search_and_redact).pack(fill=tk.X, ipady=4)
        self.lbl_redact_status = tk.Label(f_redact, text="", font=get_sys_font(8, "bold"), fg="#047857")
        self.lbl_redact_status.pack(pady=4)

        f_wm = ttk.Frame(nb_ocr, padding=10)
        nb_ocr.add(f_wm, text=" Text Watermark ")

        tk.Label(f_wm, text="Select PDF to Add Watermark:", font=get_sys_font(9, "bold"), fg="#1E40AF").pack(anchor=tk.W)
        wm_f1 = tk.Frame(f_wm); wm_f1.pack(fill=tk.X, pady=(2, 6))
        ttk.Entry(wm_f1, textvariable=self.wm_pdf_path, font=get_sys_font(8)).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        ttk.Button(wm_f1, text="Browse...", style="Tool3D.TButton", command=lambda: self._browse_single_file(self.wm_pdf_path)).pack(side=tk.RIGHT)

        tk.Label(f_wm, text="Watermark Text (e.g. CONFIDENTIAL, DRAFT, APPROVED):", font=get_sys_font(8, "bold")).pack(anchor=tk.W)
        ttk.Entry(f_wm, textvariable=self.wm_text_var, font=get_sys_font(8)).pack(fill=tk.X, pady=(2, 6))

        wm_opts = tk.Frame(f_wm); wm_opts.pack(fill=tk.X, pady=(2, 10))
        tk.Label(wm_opts, text="Angle:", font=get_sys_font(8, "bold")).pack(side=tk.LEFT)
        tk.Radiobutton(wm_opts, text="45° Diagonal", variable=self.wm_angle_var, value=45).pack(side=tk.LEFT, padx=4)
        tk.Radiobutton(wm_opts, text="0° Horizontal", variable=self.wm_angle_var, value=0).pack(side=tk.LEFT, padx=4)

        ttk.Button(f_wm, text="💧 APPLY WATERMARK & SAVE", style="Primary3D.TButton", command=self.exec_add_watermark).pack(fill=tk.X, ipady=4)
        self.lbl_wm_status = tk.Label(f_wm, text="", font=get_sys_font(8, "bold"), fg="#047857")
        self.lbl_wm_status.pack(pady=4)

    # =========================================================================
    # TAB 8: ℹ️ ABOUT & LICENSE
    # =========================================================================
    def _build_tab_about(self, parent):
        outer = tk.Frame(parent, bg="#F1F5F9")
        outer.pack(fill=tk.BOTH, expand=True, padx=16, pady=12)

        card1 = self._create_3d_card(outer, pady=(0, 10))
        tk.Label(card1, text="A B INFOTECH", font=get_sys_font(14, "bold"), fg="#1E3A8A", bg="#FFFFFF").pack(anchor=tk.W)
        tk.Label(card1, text="Unified Digital Signer & All-in-One PDF Power Suite v2.0", font=get_sys_font(9, "bold"), fg="#059669", bg="#FFFFFF").pack(anchor=tk.W, pady=(2, 6))
        tk.Label(card1, text="A high-performance cryptographic and document processing workstation supporting USB hardware tokens, PKCS#12 certificates, compression, merging, image stamps, verification, redaction, and OCR.", font=get_sys_font(8), fg="#475569", bg="#FFFFFF", wraplength=700, justify="left").pack(anchor=tk.W)

        card2 = self._create_3d_card(outer, pady=(0, 10))
        tk.Label(card2, text="License & System Information", font=get_sys_font(10, "bold"), fg="#1E40AF", bg="#FFFFFF").pack(anchor=tk.W, pady=(0, 4))

        lic_data = LicenseManager.get_license_data() or {}
        exp_date = lic_data.get("expiredate", "Unknown")
        key_val = LicenseManager.get_license_key() or "Not Installed"
        hw_id = LicenseManager.get_hardware_id()

        tk.Label(card2, text=f"• Machine / Hardware ID: {hw_id}", font=get_sys_font(8), bg="#FFFFFF").pack(anchor=tk.W, pady=2)
        tk.Label(card2, text=f"• License Key: {key_val}", font=get_sys_font(8), bg="#FFFFFF").pack(anchor=tk.W, pady=2)
        tk.Label(card2, text=f"• Expiry Date: {exp_date}", font=get_sys_font(8, "bold"), fg="#047857" if exp_date != "expired" else "#DC2626", bg="#FFFFFF").pack(anchor=tk.W, pady=2)
        tk.Label(card2, text=f"• Platform: {CURRENT_OS} ({platform.machine()})", font=get_sys_font(8), bg="#FFFFFF").pack(anchor=tk.W, pady=2)

        btn_row = tk.Frame(outer, bg="#F1F5F9")
        btn_row.pack(fill=tk.X, pady=(6, 0))
        ttk.Button(btn_row, text="🔑 Enter License Key", style="Tool3D.TButton", command=self._show_activation_screen).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_row, text="🛒 Buy DSC / Renew License Online", style="Primary3D.TButton", command=self.open_buy_dsc).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_row, text="⚙️ Manage Token Drivers", style="Tool3D.TButton", command=lambda: self.open_settings_dialog(initial_tab="drivers")).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_row, text="💬 WhatsApp: 9752318084", style="Tool3D.TButton", command=self.open_whatsapp).pack(side=tk.LEFT, padx=4)

    # =========================================================================
    # TAB 1 OPERATIONS (Sign PDF)
    # =========================================================================
    def sign_add_files(self):
        paths = filedialog.askopenfilenames(title="Select PDF Files to Sign", filetypes=[("PDF Files", "*.pdf")])
        if paths:
            for p in paths:
                if p not in self.sign_pdf_files:
                    self.sign_pdf_files.append(p)
                    self.sign_listbox.insert(tk.END, os.path.basename(p))
            self.lbl_sign_status.config(text=f"Added {len(paths)} file(s). Total: {len(self.sign_pdf_files)} PDF(s).")

    def sign_add_folder(self):
        folder = filedialog.askdirectory(title="Select Folder Containing PDFs")
        if folder:
            count = 0
            for f in os.listdir(folder):
                if f.lower().endswith(".pdf"):
                    full = os.path.join(folder, f)
                    if full not in self.sign_pdf_files:
                        self.sign_pdf_files.append(full)
                        self.sign_listbox.insert(tk.END, f)
                        count += 1
            self.lbl_sign_status.config(text=f"Found & added {count} PDF(s) from folder. Total: {len(self.sign_pdf_files)} PDF(s).")

    def sign_remove_selected(self):
        sel = list(self.sign_listbox.curselection())
        for idx in reversed(sel):
            self.sign_listbox.delete(idx)
            del self.sign_pdf_files[idx]
        self.lbl_sign_status.config(text=f"Total files in queue: {len(self.sign_pdf_files)}")

    def sign_clear(self):
        self.sign_listbox.delete(0, tk.END)
        self.sign_pdf_files.clear()
        self.lbl_sign_status.config(text="Queue cleared.")

    def sign_browse_out_folder(self):
        folder = filedialog.askdirectory(title="Select Output Folder for Signed PDFs")
        if folder:
            self.sign_output_folder.set(folder)
            self.sign_output_mode.set("folder")

    def load_token_certificates(self):
        driver_name = self.driver_var.get()
        driver_path = DRIVER_MAP.get(driver_name)
        if not driver_path or not os.path.exists(driver_path):
            self.token_cert_combo['values'] = ["No driver found / Select driver"]
            return

        def _worker():
            try:
                lib = pkcs11.lib(driver_path)
                slots = lib.get_slots(token_present=True)
                if not slots:
                    self.root.after(0, lambda: self.token_cert_combo.config(values=["No Token Detected. Insert USB Token."]))
                    return
                slot = slots[0]
                token = slot.get_token()
                certs_found = []
                self.token_certs.clear()
                with token.open() as session:
                    for obj in session.get_objects({pkcs11.Attribute.CLASS: pkcs11.ObjectClass.CERTIFICATE}):
                        raw_der = obj[pkcs11.Attribute.VALUE]
                        c = x509.load_der_x509_certificate(raw_der)
                        cn = "Unknown"
                        for attr in c.subject:
                            if attr.oid == x509.NameOID.COMMON_NAME:
                                cn = attr.value
                                break
                        certs_found.append(f"{cn} (SN: {c.serial_number})")
                        self.token_certs.append({"cn": cn, "cert": c, "obj": obj})

                if certs_found:
                    self.root.after(0, lambda: self._apply_token_certs(certs_found))
                else:
                    self.root.after(0, lambda: self.token_cert_combo.config(values=["No certificates found in token"]))
            except Exception as e:
                logger.warning(f"Token cert load note: {e}")
                self.root.after(0, lambda: self.token_cert_combo.config(values=[f"Token Read: {str(e)[:40]}"]))

        threading.Thread(target=_worker, daemon=True).start()

    def _apply_token_certs(self, certs_list):
        self.token_cert_combo['values'] = certs_list
        if certs_list:
            self.token_cert_combo.current(0)
            self.lbl_sign_status.config(text=f"✓ Token detected: {certs_list[0]}", foreground="#047857")

    def browse_pfx_file(self):
        f = filedialog.askopenfilename(title="Select .PFX / .P12 File", filetypes=[("PKCS#12 Certificates", "*.pfx;*.p12")])
        if f:
            self.pfx_path_var.set(f)
            self.verify_pfx_certificate(silent=False)

    def verify_pfx_certificate(self, silent=False):
        path = self.pfx_path_var.get().strip()
        pwd = self.pfx_pass_var.get()
        if not path or not os.path.exists(path):
            if not silent: messagebox.showerror("Error", "Please select a valid .pfx/.p12 file first.")
            return False

        signer, cert = load_pfx_signer_and_cert(path, pwd)
        if signer and cert:
            self.pfx_loaded_cert = cert
            cn = "Certificate Owner"
            for attr in cert.subject:
                if attr.oid == x509.NameOID.COMMON_NAME:
                    cn = attr.value
                    break
            self.lbl_pfx_info.config(text=f"✓ Valid: {cn} (Exp: {cert.not_valid_after_utc.strftime('%Y-%m-%d')})", fg="#047857")
            if not silent: messagebox.showinfo("Success", f"Certificate unlocked successfully:\n\n{cn}")
            return True
        else:
            self.lbl_pfx_info.config(text="❌ Invalid password or corrupt certificate.", fg="#DC2626")
            if not silent: messagebox.showerror("Failed", "Could not unlock certificate with the provided password.")
            return False

    def load_win_store_certificates(self):
        store_choice = "MY" if "MY" in self.win_store_var.get() else "ROOT"
        certs = WinCertStore.get_certificates(store_choice)
        self.win_store_certs = certs
        if certs:
            vals = [f"{c['common_name']} ({c['expiry']})" for c in certs]
            self.win_cert_combo['values'] = vals
            self.win_cert_combo.current(0)
            self.lbl_sign_status.config(text=f"Loaded {len(certs)} certificate(s) from Windows store.")
        else:
            self.win_cert_combo['values'] = ["No certificates found in Windows store"]

    def browse_sign_logo(self):
        f = filedialog.askopenfilename(title="Select Signature Stamp Logo", filetypes=[("Images", "*.png;*.jpg;*.jpeg;*.bmp")])
        if f:
            self.sign_logo_path_var.set(f)

    def open_sign_position_picker(self):
        pdf_to_view = self.sign_pdf_files[0] if self.sign_pdf_files else None
        if not pdf_to_view or not os.path.exists(pdf_to_view):
            pdf_to_view = filedialog.askopenfilename(title="Select PDF to Choose Signature Position", filetypes=[("PDF", "*.pdf")])

        if not pdf_to_view:
            messagebox.showinfo("Note", "Please add at least one PDF or select a template PDF to view pages.")
            return

        def _on_area_set(area):
            self.sign_selected_area = area
            self.lbl_sign_pos_info.config(
                text=f"📍 Selected: X={int(area['x'])} pt, Y={int(area['y'])} pt | W={int(area['width'])} pt, H={int(area['height'])} pt (Page {int(area.get('page', 0))+1})"
            )

        SignatureAreaPickerWindow(self.root, pdf_to_view, _on_area_set, initial_area=self.sign_selected_area, title="Choose Signature Position & Size on Page")

    def start_document_signing(self):
        if not self.sign_pdf_files:
            messagebox.showerror("Error", "Please add at least one PDF document to sign.")
            return

        mode = self.cert_source_var.get()
        active_tab_text = self.nb_dsc.tab(self.nb_dsc.select(), "text")

        signer = None
        cert_parsed = None

        if "USB Token" in active_tab_text:
            driver_name = self.driver_var.get()
            driver_path = DRIVER_MAP.get(driver_name)
            pin = self.pin_var.get().strip()
            if not pin:
                messagebox.showerror("PIN Required", "Please enter your Hardware Token PIN.")
                return
            if not driver_path or not os.path.exists(driver_path):
                messagebox.showerror("Driver Error", "Selected PKCS#11 driver was not found.")
                return

            try:
                signer = PKCS11Signer(
                    pkcs11_lib=pkcs11.lib(driver_path),
                    user_pin=pin,
                    token_criteria=None,
                    other_certs=None
                )
                if self.token_certs:
                    sel_idx = self.token_cert_combo.current()
                    if sel_idx >= 0 and sel_idx < len(self.token_certs):
                        cert_parsed = self.token_certs[sel_idx]["cert"]
            except Exception as e:
                messagebox.showerror("Token Error", f"Failed to connect to USB Token:\n{e}")
                return

        elif "PFX" in active_tab_text:
            pfx_path = self.pfx_path_var.get().strip()
            pfx_pwd = self.pfx_pass_var.get()
            signer, cert_parsed = load_pfx_signer_and_cert(pfx_path, pfx_pwd)
            if not signer or not cert_parsed:
                messagebox.showerror("PFX Error", "Please verify your .PFX certificate file and password.")
                return
        else: # Windows Store
            if not self.win_store_certs:
                self.load_win_store_certificates()
            sel_idx = self.win_cert_combo.current()
            if sel_idx >= 0 and sel_idx < len(self.win_store_certs):
                cert_parsed = self.win_store_certs[sel_idx]["cert_obj"]
            messagebox.showinfo("Windows Store Note", "Windows Store certificate selected. If hardware token requires driver PIN, please use the USB Token tab.")
            return

        area_opts = {
            'heading': self.sign_heading_var.get().strip(),
            'extra_line': self.sign_extra_line_var.get().strip(),
            'reason': self.sign_reason_var.get().strip(),
            'location': self.sign_location_var.get().strip(),
            'show_name': self.chk_name_var.get(),
            'show_date': self.chk_date_var.get(),
            'show_reason': self.chk_reason_var.get(),
            'show_location': self.chk_loc_var.get(),
            'show_issuer': self.chk_issuer_var.get(),
            'show_tick': self.chk_tick_var.get(),
            'show_border': self.chk_border_var.get(),
            'logo_path': self.sign_logo_path_var.get().strip() if self.sign_logo_path_var.get().strip() else None
        }

        protect_opts = {
            'enabled': self.chk_protect_var.get(),
            'open_password': self.protect_open_pw_var.get().strip(),
            'permissions_password': self.protect_perm_pw_var.get().strip(),
            'allow_printing': self.chk_allow_print.get(),
            'allow_copying': self.chk_allow_copy.get(),
            'allow_editing': self.chk_allow_edit.get()
        }

        tst_client = None
        tsa_choice = self.sign_tsa_var.get()
        if "DigiCert" in tsa_choice:
            tst_client = HTTPTimeStamper("http://timestamp.digicert.com")
        elif "FreeTSA" in tsa_choice:
            tst_client = HTTPTimeStamper("https://freetsa.org/tsr")

        self.btn_sign_docs.config(state=tk.DISABLED, text="SIGNING IN PROGRESS...")
        self.sign_progress['value'] = 0
        self.sign_progress['maximum'] = len(self.sign_pdf_files)

        def _worker():
            success_count = 0
            err_count = 0
            last_out_dir = None

            for idx, pdf_path in enumerate(self.sign_pdf_files):
                try:
                    self.root.after(0, lambda i=idx, p=pdf_path: self.lbl_sign_status.config(text=f"Signing ({i+1}/{len(self.sign_pdf_files)}): {os.path.basename(p)}..."))
                    with open(pdf_path, 'rb') as f:
                        pdf_bytes = f.read()

                    base_name = Path(pdf_path).stem
                    if self.sign_output_mode.get() == "folder" and self.sign_output_folder.get():
                        out_dir = self.sign_output_folder.get()
                    else:
                        out_dir = os.path.dirname(pdf_path)
                    os.makedirs(out_dir, exist_ok=True)
                    last_out_dir = out_dir
                    out_path = os.path.join(out_dir, f"{base_name}_signed.pdf")

                    reader = PdfReader(io.BytesIO(pdf_bytes))
                    total_pages = len(reader.pages)
                    page_mode = self.sign_page_mode.get()

                    areas = []
                    if not self.chk_visible_sig.get():
                        areas = [{'page': 0, 'x': 0, 'y': 0, 'width': 0, 'height': 0, 'opts': area_opts}]
                    elif page_mode == "all":
                        for pno in range(total_pages):
                            a = self.sign_selected_area.copy()
                            a['page'] = pno
                            a['opts'] = area_opts
                            areas.append(a)
                    elif page_mode == "first":
                        a = self.sign_selected_area.copy()
                        a['page'] = 0
                        a['opts'] = area_opts
                        areas.append(a)
                    elif page_mode == "last":
                        a = self.sign_selected_area.copy()
                        a['page'] = max(0, total_pages - 1)
                        a['opts'] = area_opts
                        areas.append(a)
                    elif page_mode == "specific":
                        try: pno = int(self.sign_page_num_var.get()) - 1
                        except Exception: pno = 0
                        a = self.sign_selected_area.copy()
                        a['page'] = max(0, min(total_pages - 1, pno))
                        a['opts'] = area_opts
                        areas.append(a)
                    else:
                        a = self.sign_selected_area.copy()
                        a['opts'] = area_opts
                        areas.append(a)

                    signed_bytes = sign_pdf_stream(pdf_bytes, areas, signer, cert_parsed, tst_client=tst_client, protect_opts=protect_opts)
                    with open(out_path, 'wb') as out_f:
                        out_f.write(signed_bytes)
                    success_count += 1
                except Exception as e:
                    logger.error(f"Error signing {pdf_path}: {e}")
                    err_count += 1

                self.root.after(0, lambda i=idx: self.sign_progress.config(value=i+1))

            def _finish():
                self.btn_sign_docs.config(state=tk.NORMAL, text="✍️ SIGN DOCUMENTS")
                msg = f"Signing Complete!\n\nSuccessfully signed: {success_count} PDF(s)"
                if err_count: msg += f"\nFailed: {err_count} PDF(s)"
                self.lbl_sign_status.config(text=f"✓ Complete: {success_count} signed successfully.", foreground="#047857")
                messagebox.showinfo("Signing Complete", msg)
                if last_out_dir and os.path.exists(last_out_dir):
                    self.last_output_dir = last_out_dir

            self.root.after(0, _finish)

        threading.Thread(target=_worker, daemon=True).start()

    def open_output_folder(self):
        folder = getattr(self, 'last_output_dir', None)
        if not folder or not os.path.exists(folder):
            if self.sign_output_folder.get() and os.path.exists(self.sign_output_folder.get()):
                folder = self.sign_output_folder.get()
            elif self.sign_pdf_files and os.path.exists(os.path.dirname(self.sign_pdf_files[0])):
                folder = os.path.dirname(self.sign_pdf_files[0])
            else:
                folder = os.getcwd()

        try:
            if CURRENT_OS == 'Windows':
                os.startfile(folder)
            elif CURRENT_OS == 'Darwin':
                subprocess.Popen(['open', folder])
            else:
                subprocess.Popen(['xdg-open', folder])
        except Exception as e:
            messagebox.showerror("Error", f"Could not open folder: {e}")

    # =========================================================================
    # TAB 2 OPERATIONS (Image Stamp)
    # =========================================================================
    def stamp_add_files(self):
        paths = filedialog.askopenfilenames(title="Select PDF Files to Stamp", filetypes=[("PDF", "*.pdf")])
        if paths:
            for p in paths:
                if p not in self.stamp_pdf_files:
                    self.stamp_pdf_files.append(p)
                    self.stamp_listbox.insert(tk.END, os.path.basename(p))

    def stamp_add_folder(self):
        folder = filedialog.askdirectory(title="Select Folder Containing PDFs")
        if folder:
            for f in os.listdir(folder):
                if f.lower().endswith(".pdf"):
                    full = os.path.join(folder, f)
                    if full not in self.stamp_pdf_files:
                        self.stamp_pdf_files.append(full)
                        self.stamp_listbox.insert(tk.END, f)

    def stamp_remove(self):
        for idx in reversed(list(self.stamp_listbox.curselection())):
            self.stamp_listbox.delete(idx)
            del self.stamp_pdf_files[idx]

    def stamp_clear(self):
        self.stamp_listbox.delete(0, tk.END)
        self.stamp_pdf_files.clear()

    def browse_stamp_image(self):
        f = filedialog.askopenfilename(title="Select Stamp Image", filetypes=[("Images", "*.png;*.jpg;*.jpeg;*.bmp")])
        if f: self.stamp_img_path.set(f)

    def stamp_browse_out_folder(self):
        f = filedialog.askdirectory(title="Select Output Folder")
        if f:
            self.stamp_out_dir.set(f)
            self.stamp_out_mode.set("folder")

    def open_stamp_position_picker(self):
        pdf_path = self.stamp_pdf_files[0] if self.stamp_pdf_files else None
        if not pdf_path or not os.path.exists(pdf_path):
            pdf_path = filedialog.askopenfilename(title="Select Template PDF", filetypes=[("PDF", "*.pdf")])
        if not pdf_path: return

        def _on_area(area):
            self.stamp_custom_rect = (area['x'], area['y'], area['width'], area['height'])
            self.stamp_pos_mode.set("custom")
            self.lbl_stamp_status.config(text=f"✓ Custom position chosen on Page {int(area.get('page', 0))+1}")

        SignatureAreaPickerWindow(self.root, pdf_path, _on_area, title="Choose Image Stamp Position on Page")

    def start_image_stamping(self):
        if not self.stamp_pdf_files:
            messagebox.showerror("Error", "Please add at least one PDF file to stamp.")
            return
        img_path = self.stamp_img_path.get().strip()
        if not img_path or not os.path.exists(img_path):
            messagebox.showerror("Error", "Please select a valid stamp image file.")
            return

        pos_mode = self.stamp_pos_mode.get()
        p_scope = self.stamp_page_scope.get()
        try: target_p = int(self.stamp_page_num.get())
        except Exception: target_p = 1
        try: sw = float(self.stamp_width_var.get()); sh = float(self.stamp_height_var.get())
        except Exception: sw = 150.0; sh = 60.0

        def _worker():
            success = 0
            for p in self.stamp_pdf_files:
                try:
                    stem = Path(p).stem
                    out_d = self.stamp_out_dir.get() if (self.stamp_out_mode.get() == "folder" and self.stamp_out_dir.get()) else os.path.dirname(p)
                    out_path = os.path.join(out_d, f"{stem}_stamped.pdf")
                    PdfStampManager.apply_stamp(p, out_path, img_path, position_mode=pos_mode, custom_rect=self.stamp_custom_rect, page_selection=p_scope, target_page=target_p, stamp_size=(sw, sh))
                    success += 1
                except Exception as e:
                    logger.error(f"Error stamping {p}: {e}")

            self.root.after(0, lambda: self.lbl_stamp_status.config(text=f"✓ Stamping Complete! Stamped {success} file(s)."))
            self.root.after(0, lambda: messagebox.showinfo("Complete", f"Successfully applied stamp to {success} PDF(s)."))

        threading.Thread(target=_worker, daemon=True).start()

    # =========================================================================
    # TAB 3 OPERATIONS (Compress)
    # =========================================================================
    def compress_add_files(self):
        paths = filedialog.askopenfilenames(title="Select PDFs to Compress", filetypes=[("PDF", "*.pdf")])
        if paths:
            for p in paths:
                if p not in self.compress_pdf_files:
                    self.compress_pdf_files.append(p)
                    self.compress_listbox.insert(tk.END, os.path.basename(p))

    def compress_add_folder(self):
        folder = filedialog.askdirectory(title="Select Folder")
        if folder:
            for f in os.listdir(folder):
                if f.lower().endswith(".pdf"):
                    full = os.path.join(folder, f)
                    if full not in self.compress_pdf_files:
                        self.compress_pdf_files.append(full)
                        self.compress_listbox.insert(tk.END, f)

    def compress_remove(self):
        for idx in reversed(list(self.compress_listbox.curselection())):
            self.compress_listbox.delete(idx)
            del self.compress_pdf_files[idx]

    def compress_clear(self):
        self.compress_listbox.delete(0, tk.END)
        self.compress_pdf_files.clear()

    def start_compressing(self):
        if not self.compress_pdf_files:
            messagebox.showerror("Error", "Please add at least one PDF file to compress.")
            return

        level = self.compress_level_var.get()
        color = self.compress_color_var.get()
        for item in self.tree_compress.get_children():
            self.tree_compress.delete(item)

        def _worker():
            total_saved = 0
            for p in self.compress_pdf_files:
                try:
                    stem = Path(p).stem
                    out_d = self.compress_out_dir.get() if (self.compress_out_mode.get() == "folder" and self.compress_out_dir.get()) else os.path.dirname(p)
                    out_path = os.path.join(out_d, f"{stem}_compressed.pdf")
                    stats = PdfCompressor.compress_pdf(p, out_path, level=level, color_mode=color)
                    total_saved += stats["saved_bytes"]
                    orig_kb = f"{stats['orig_size'] / 1024:.1f} KB"
                    new_kb = f"{stats['new_size'] / 1024:.1f} KB"
                    saved_kb = f"{stats['saved_bytes'] / 1024:.1f} KB"
                    pct_str = f"{stats['saved_pct']:.1f}%"
                    self.root.after(0, lambda fn=os.path.basename(p), o=orig_kb, n=new_kb, s=saved_kb, pct=pct_str: self.tree_compress.insert("", tk.END, values=(fn, o, n, s, pct)))
                except Exception as e:
                    logger.error(f"Compress error on {p}: {e}")

            self.root.after(0, lambda: self.lbl_compress_status.config(text=f"✓ Compression Complete! Total space saved: {total_saved / (1024*1024):.2f} MB"))
            self.root.after(0, lambda: messagebox.showinfo("Complete", f"Compressed all PDFs!\nTotal Space Saved: {total_saved / (1024*1024):.2f} MB"))

        threading.Thread(target=_worker, daemon=True).start()

    # =========================================================================
    # TAB 4 OPERATIONS (Merge)
    # =========================================================================
    def merge_add_files(self):
        paths = filedialog.askopenfilenames(title="Select PDFs to Merge", filetypes=[("PDF", "*.pdf")])
        if paths:
            for p in paths:
                self.merge_pdf_items.append({'path': p, 'name': os.path.basename(p), 'range': 'all'})
            self._refresh_merge_tree()

    def merge_add_folder(self):
        folder = filedialog.askdirectory(title="Select Folder")
        if folder:
            for f in sorted(os.listdir(folder)):
                if f.lower().endswith(".pdf"):
                    full = os.path.join(folder, f)
                    self.merge_pdf_items.append({'path': full, 'name': f, 'range': 'all'})
            self._refresh_merge_tree()

    def _refresh_merge_tree(self):
        for item in self.tree_merge.get_children():
            self.tree_merge.delete(item)
        for idx, item in enumerate(self.merge_pdf_items):
            self.tree_merge.insert("", tk.END, values=(idx + 1, item['name'], item['range'], item['path']))

    def merge_move_up(self):
        sel = self.tree_merge.selection()
        if sel:
            idx = self.tree_merge.index(sel[0])
            if idx > 0:
                self.merge_pdf_items[idx], self.merge_pdf_items[idx-1] = self.merge_pdf_items[idx-1], self.merge_pdf_items[idx]
                self._refresh_merge_tree()
                self.tree_merge.selection_set(self.tree_merge.get_children()[idx-1])

    def merge_move_down(self):
        sel = self.tree_merge.selection()
        if sel:
            idx = self.tree_merge.index(sel[0])
            if idx < len(self.merge_pdf_items) - 1:
                self.merge_pdf_items[idx], self.merge_pdf_items[idx+1] = self.merge_pdf_items[idx+1], self.merge_pdf_items[idx]
                self._refresh_merge_tree()
                self.tree_merge.selection_set(self.tree_merge.get_children()[idx+1])

    def merge_remove(self):
        sel = self.tree_merge.selection()
        if sel:
            idx = self.tree_merge.index(sel[0])
            del self.merge_pdf_items[idx]
            self._refresh_merge_tree()

    def merge_clear(self):
        self.merge_pdf_items.clear()
        self._refresh_merge_tree()

    def _on_merge_select(self, event):
        sel = self.tree_merge.selection()
        if sel:
            idx = self.tree_merge.index(sel[0])
            self.entry_merge_range.delete(0, tk.END)
            self.entry_merge_range.insert(0, self.merge_pdf_items[idx]['range'])

    def merge_set_range(self):
        sel = self.tree_merge.selection()
        if sel:
            idx = self.tree_merge.index(sel[0])
            new_r = self.entry_merge_range.get().strip() or "all"
            self.merge_pdf_items[idx]['range'] = new_r
            self._refresh_merge_tree()

    def merge_browse_out(self):
        f = filedialog.asksaveasfilename(title="Save Merged PDF As", defaultextension=".pdf", filetypes=[("PDF", "*.pdf")])
        if f: self.merge_out_path.set(f)

    def start_merging(self):
        if len(self.merge_pdf_items) < 2:
            messagebox.showerror("Error", "Please add at least 2 PDF files to merge.")
            return
        out_p = self.merge_out_path.get().strip()
        if not out_p:
            out_p = filedialog.asksaveasfilename(title="Save Merged PDF As", defaultextension=".pdf", filetypes=[("PDF", "*.pdf")])
            if not out_p: return
            self.merge_out_path.set(out_p)

        file_configs = [(item['path'], item['range']) for item in self.merge_pdf_items]
        try:
            success = PdfMerger.merge_pdfs(file_configs, out_p)
            if success:
                self.lbl_merge_status.config(text=f"✓ Merged {len(file_configs)} documents successfully into: {os.path.basename(out_p)}")
                messagebox.showinfo("Success", f"Merged successfully into:\n{out_p}")
            else:
                messagebox.showerror("Error", "Could not complete PDF merge.")
        except Exception as e:
            messagebox.showerror("Merge Error", str(e))

    # =========================================================================
    # TAB 5 OPERATIONS (Verify)
    # =========================================================================
    def verify_browse_file(self):
        f = filedialog.askopenfilename(title="Select Signed PDF to Verify", filetypes=[("PDF", "*.pdf")])
        if f:
            self.verify_pdf_path.set(f)
            self.start_verification()

    def start_verification(self):
        p = self.verify_pdf_path.get().strip()
        if not p or not os.path.exists(p):
            messagebox.showerror("Error", "Please select a valid PDF file to verify.")
            return

        res = PdfVerifier.verify_pdf(p)
        self.txt_verify_report.delete("1.0", tk.END)

        if not res.get("has_signatures"):
            self.lbl_verify_banner.config(text="⚠️ No Digital Signatures Found in this PDF.", fg="#D97706", bg="#FEF3C7")
            self.txt_verify_report.insert(tk.END, f"File: {p}\nStatus: Document contains no cryptographic signature fields.\n")
            return

        all_ok = res.get("all_valid")
        if all_ok:
            self.lbl_verify_banner.config(text="✅ All Digital Signatures are VALID & Document is INTACT", fg="#047857", bg="#ECFDF5")
        else:
            self.lbl_verify_banner.config(text="❌ Signature Invalid or Document Has Been Modified", fg="#DC2626", bg="#FEF2F2")

        report_lines = [
            f"FILE: {p}",
            f"VERIFICATION SUMMARY: {res.get('message')}",
            "=" * 60,
        ]
        for idx, sig in enumerate(res.get("signatures", [])):
            report_lines.extend([
                f"\n[SIGNATURE #{idx+1}]: {sig['field_name']}",
                f"  • Common Name (CN): {sig['signer_cn']}",
                f"  • Issuer: {sig['issuer_cn']}",
                f"  • Status: {'VALID & INTACT' if (sig['valid'] and sig['intact']) else 'INVALID / MODIFIED'}",
                f"  • Serial Number: {sig['serial']}",
                f"  • Validity Date: {sig['expiry']}",
                f"  • Qualified Timestamp (TSA): {sig['tsa_time']}",
                f"  • Assessment: {sig['summary']}",
            ])
        self.txt_verify_report.insert(tk.END, "\n".join(report_lines))

    # =========================================================================
    # TAB 6 OPERATIONS (Tools)
    # =========================================================================
    def _browse_single_file(self, target_var):
        f = filedialog.askopenfilename(title="Select PDF", filetypes=[("PDF", "*.pdf")])
        if f: target_var.set(f)

    def _browse_single_folder(self, target_var):
        f = filedialog.askdirectory(title="Select Destination Folder")
        if f: target_var.set(f)

    def exec_split_pdf(self):
        p = self.tool_split_pdf.get().strip()
        out_d = self.tool_split_out_dir.get().strip() or os.path.dirname(p)
        if not p or not os.path.exists(p):
            messagebox.showerror("Error", "Please select a valid PDF to split.")
            return
        mode = self.tool_split_mode.get()
        ranges = self.tool_split_ranges.get()
        try:
            created = PdfToolsManager.split_pdf(p, out_d, mode=mode, ranges=ranges)
            self.lbl_tool_split_status.config(text=f"✓ Split into {len(created)} separate PDF file(s).")
            messagebox.showinfo("Split Complete", f"Successfully created {len(created)} file(s) in:\n{out_d}")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def exec_rotate_pdf(self):
        p = self.tool_rot_pdf.get().strip()
        if not p or not os.path.exists(p):
            messagebox.showerror("Error", "Please select a valid PDF to rotate.")
            return
        stem = Path(p).stem
        out_p = os.path.join(os.path.dirname(p), f"{stem}_rotated.pdf")
        try:
            PdfToolsManager.rotate_pdf(p, out_p, angle=self.tool_rot_angle.get(), page_selection=self.tool_rot_scope.get(), specific_pages=self.tool_rot_spec.get())
            self.lbl_tool_rot_status.config(text=f"✓ Rotated and saved: {os.path.basename(out_p)}")
            messagebox.showinfo("Rotate Complete", f"Saved rotated document to:\n{out_p}")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def exec_extract_pages(self):
        p = self.tool_ext_pdf.get().strip()
        pages = self.tool_ext_pages.get().strip()
        if not p or not os.path.exists(p) or not pages:
            messagebox.showerror("Error", "Please select a PDF and specify pages to extract.")
            return
        stem = Path(p).stem
        out_p = os.path.join(os.path.dirname(p), f"{stem}_extracted.pdf")
        try:
            PdfToolsManager.extract_pages(p, out_p, pages)
            self.lbl_tool_ext_status.config(text=f"✓ Extracted pages ({pages}) to: {os.path.basename(out_p)}")
            messagebox.showinfo("Extract Complete", f"Saved extracted pages to:\n{out_p}")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def exec_delete_pages(self):
        p = self.tool_del_pdf.get().strip()
        pages = self.tool_del_pages.get().strip()
        if not p or not os.path.exists(p) or not pages:
            messagebox.showerror("Error", "Please select a PDF and specify pages to delete.")
            return
        stem = Path(p).stem
        out_p = os.path.join(os.path.dirname(p), f"{stem}_deleted_pages.pdf")
        try:
            PdfToolsManager.delete_pages(p, out_p, pages)
            self.lbl_tool_del_status.config(text=f"✓ Deleted pages ({pages}) and saved: {os.path.basename(out_p)}")
            messagebox.showinfo("Delete Complete", f"Saved cleaned PDF to:\n{out_p}")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def exec_protect_pdf(self):
        p = self.tool_prot_pdf.get().strip()
        if not p or not os.path.exists(p):
            messagebox.showerror("Error", "Please select a PDF to protect.")
            return
        u_pw = self.tool_prot_open.get().strip()
        o_pw = self.tool_prot_perm.get().strip()
        if not u_pw and not o_pw:
            messagebox.showerror("Error", "Please enter at least one password.")
            return
        stem = Path(p).stem
        out_p = os.path.join(os.path.dirname(p), f"{stem}_protected.pdf")
        try:
            PdfToolsManager.protect_pdf(p, out_p, u_pw, o_pw, allow_print=self.tool_prot_print.get(), allow_copy=self.tool_prot_copy.get(), allow_edit=self.tool_prot_edit.get())
            self.lbl_tool_prot_status.config(text=f"✓ Protected with 256-bit AES encryption: {os.path.basename(out_p)}")
            messagebox.showinfo("Protection Complete", f"Encrypted and protected with AES-256:\n{out_p}")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def exec_unlock_pdf(self):
        p = self.tool_unl_pdf.get().strip()
        pw = self.tool_unl_pw.get()
        if not p or not os.path.exists(p):
            messagebox.showerror("Error", "Please select a protected PDF.")
            return
        stem = Path(p).stem
        out_p = os.path.join(os.path.dirname(p), f"{stem}_unlocked.pdf")
        try:
            PdfToolsManager.unlock_pdf(p, out_p, pw)
            self.lbl_tool_unl_status.config(text=f"✓ Password removed successfully: {os.path.basename(out_p)}")
            messagebox.showinfo("Unlock Complete", f"Saved decrypted PDF to:\n{out_p}")
        except Exception as e:
            messagebox.showerror("Unlock Error", str(e))

    # =========================================================================
    # TAB 7 OPERATIONS (OCR & Edit)
    # =========================================================================
    def exec_ocr_extract(self):
        p = self.ocr_pdf_path.get().strip()
        if not p or not os.path.exists(p):
            messagebox.showerror("Error", "Please select a PDF for OCR & Text extraction.")
            return
        scope = self.ocr_page_scope.get().strip() or "all"
        try:
            content = PdfOcrEditManager.extract_text_and_ocr(p, page_range=scope)
            self.txt_ocr_preview.delete("1.0", tk.END)
            self.txt_ocr_preview.insert(tk.END, content)
            messagebox.showinfo("OCR Complete", f"Successfully extracted text from document ({scope}).")
        except Exception as e:
            messagebox.showerror("OCR Error", str(e))

    def save_ocr_to_file(self):
        content = self.txt_ocr_preview.get("1.0", tk.END).strip()
        if not content:
            messagebox.showinfo("Note", "No text content available to save.")
            return
        f = filedialog.asksaveasfilename(title="Save Extracted Text As", defaultextension=".txt", filetypes=[("Text Files", "*.txt")])
        if f:
            with open(f, "w", encoding="utf-8") as out_f:
                out_f.write(content)
            messagebox.showinfo("Saved", f"Text saved to:\n{f}")

    def exec_search_and_redact(self):
        p = self.redact_pdf_path.get().strip()
        term = self.redact_term_var.get().strip()
        if not p or not os.path.exists(p) or not term:
            messagebox.showerror("Error", "Please select a PDF and enter search term to redact.")
            return
        rep_text = self.redact_replace_var.get() if self.redact_mode_var.get() == "replace" else ""
        stem = Path(p).stem
        out_p = os.path.join(os.path.dirname(p), f"{stem}_redacted.pdf")
        try:
            count = PdfOcrEditManager.search_and_redact(p, out_p, term, replace_text=rep_text)
            self.lbl_redact_status.config(text=f"✓ Redacted {count} occurrence(s) of '{term}'")
            messagebox.showinfo("Redaction Complete", f"Permanently redacted {count} occurrence(s).\nSaved to:\n{out_p}")
        except Exception as e:
            messagebox.showerror("Redaction Error", str(e))

    def exec_add_watermark(self):
        p = self.wm_pdf_path.get().strip()
        text = self.wm_text_var.get().strip()
        if not p or not os.path.exists(p) or not text:
            messagebox.showerror("Error", "Please select a PDF and enter watermark text.")
            return
        stem = Path(p).stem
        out_p = os.path.join(os.path.dirname(p), f"{stem}_watermarked.pdf")
        try:
            PdfOcrEditManager.add_watermark(p, out_p, watermark_text=text, angle=self.wm_angle_var.get())
            self.lbl_wm_status.config(text=f"✓ Watermark '{text}' applied successfully.")
            messagebox.showinfo("Watermark Complete", f"Applied watermark successfully:\n{out_p}")
        except Exception as e:
            messagebox.showerror("Watermark Error", str(e))

    # =========================================================================
    # EXTERNAL SHORTCUTS & HELPERS
    # =========================================================================
    def open_buy_dsc(self):
        url = "https://pay.a-b-infotech.in/license.php"
        self._open_url_in_app_mode(url, hide_window=False)

    def open_whatsapp(self):
        webbrowser.open("https://wa.me/919752318084")

    def reload_drivers(self, select_driver=None):
        global DRIVER_MAP
        DRIVER_MAP = get_platform_drivers()
        all_keys = list(DRIVER_MAP.keys())
        self.token_driver_combo['values'] = all_keys
        if select_driver and select_driver in all_keys:
            self.driver_var.set(select_driver)
        elif all_keys:
            self.token_driver_combo.current(0)

    def open_settings_dialog(self, initial_tab=None):
        SettingsDialog(self.root, self, initial_tab=initial_tab)

    def open_batch_dialog(self):
        BatchFolderDialog(self.root, self)

    def open_pdf(self):
        self.notebook.select(self.tab_sign)
        self.sign_add_files()



# ==========================================
# 4.5. BULK SIGN FOLDER & PROGRESS DIALOGS
# ==========================================
class BatchFolderDialog:
    """
    Step 1: Get both folder paths (Input Folder & Output Folder).
    Once both are verified, closes and loads the sample PDF in the main viewer
    to allow the user to select the signature area.
    """
    def __init__(self, parent, main_app):
        self.dialog = tk.Toplevel(parent)
        self.dialog.title(f"📁 Bulk Sign - Select Folders [{CURRENT_OS}]")
        self.dialog.geometry("620x400")
        self.dialog.minsize(560, 360)
        self.dialog.transient(parent)
        self.dialog.grab_set()

        self.main_app = main_app
        if hasattr(self.main_app, '_apply_window_icon'):
            self.main_app._apply_window_icon(self.dialog)

        self.in_dir_var = tk.StringVar(value=self.main_app.bulk_in_dir or "")
        self.out_dir_var = tk.StringVar(value=self.main_app.bulk_out_dir or "")

        self.dialog.protocol("WM_DELETE_WINDOW", self.dialog.destroy)
        self._build_ui()

    def _build_ui(self):
        f = ttk.Frame(self.dialog, padding="16")
        f.pack(fill=tk.BOTH, expand=True)

        header_frame = ttk.Frame(f)
        header_frame.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(header_frame, text="📁 Bulk Sign PDF Folder", font=get_sys_font(13, "bold"), foreground="#1E40AF").pack(anchor=tk.W)
        ttk.Label(
            header_frame,
            text="Select Input & Output folders. Then a sample PDF will open in the viewer to select your signature area.",
            font=get_sys_font(8), foreground="#475569", wraplength=560, justify="left"
        ).pack(anchor=tk.W, pady=(2, 0))

        # 1. Input Folder
        ttk.Label(f, text="1. Select Input Folder (containing PDF files to sign):", font=get_sys_font(9, "bold")).pack(anchor=tk.W, pady=(6, 2))
        in_row = ttk.Frame(f)
        in_row.pack(fill=tk.X, pady=(0, 2))
        ttk.Entry(in_row, textvariable=self.in_dir_var, font=get_sys_font(8)).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        ttk.Button(in_row, text="Browse...", style="Tool3D.TButton", command=self._browse_in).pack(side=tk.RIGHT)

        self.lbl_found = tk.Label(f, text="No folder selected yet.", font=get_sys_font(8, "italic"), fg="#64748B", anchor="w")
        self.lbl_found.pack(fill=tk.X, pady=(0, 10))

        # 2. Output Folder
        ttk.Label(f, text="2. Select Output Folder (where signed PDFs will be saved):", font=get_sys_font(9, "bold")).pack(anchor=tk.W, pady=(2, 2))
        out_row = ttk.Frame(f)
        out_row.pack(fill=tk.X, pady=(0, 6))
        ttk.Entry(out_row, textvariable=self.out_dir_var, font=get_sys_font(8)).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        ttk.Button(out_row, text="Browse...", style="Tool3D.TButton", command=self._browse_out).pack(side=tk.RIGHT)

        # Active Certificate Badge
        cert_info_frame = tk.Frame(f, bg="#F8FAFC", relief=tk.SOLID, bd=1, padx=10, pady=8)
        cert_info_frame.pack(fill=tk.X, pady=(6, 12))

        mode = self.main_app.cert_source_var.get()
        if mode == "pfx":
            pfx_file = os.path.basename(self.main_app.pfx_path_var.get()) or "None selected"
            info_text = f"🔐 Active Signing Source: Software Certificate (.pfx/.p12)\n📄 File: {pfx_file}"
            info_color = "#0369A1"
        else:
            driver_name = self.main_app.driver_var.get() or "No Driver"
            cert_val = self.main_app.cert_combo.get() or "None loaded"
            info_text = f"🔐 Active Signing Source: USB Token ({driver_name})\n📜 Certificate: {cert_val}"
            info_color = "#047857"

        tk.Label(
            cert_info_frame, text=info_text, bg="#F8FAFC", fg=info_color,
            font=get_sys_font(8, "bold"), justify="left", anchor="w"
        ).pack(fill=tk.X)

        # Action Buttons
        btn_row = ttk.Frame(f)
        btn_row.pack(side=tk.BOTTOM, fill=tk.X, pady=(10, 0))

        ttk.Button(btn_row, text="Cancel", style="Tool3D.TButton", command=self.dialog.destroy).pack(side=tk.RIGHT, padx=4)
        self.btn_next = ttk.Button(
            btn_row, text="📂 Open PDF in Viewer to Select Area ➔", style="Batch.TButton",
            command=self._proceed_to_viewer
        )
        self.btn_next.pack(side=tk.RIGHT, padx=4)

        if self.in_dir_var.get():
            self._update_found_count(self.in_dir_var.get())

    def _browse_in(self):
        d = filedialog.askdirectory(title="Select Input Folder Containing PDFs", parent=self.dialog)
        if d:
            self.in_dir_var.set(d)
            if not self.out_dir_var.get() or self.out_dir_var.get().endswith("Signed_PDFs"):
                self.out_dir_var.set(os.path.join(d, "Signed_PDFs"))
            self._update_found_count(d)

    def _update_found_count(self, d):
        try:
            if os.path.isdir(d):
                pdf_files = [f for f in sorted(os.listdir(d)) if f.lower().endswith('.pdf')]
                self.lbl_found.config(text=f"✓ Found {len(pdf_files)} PDF file(s) in selected folder.", fg="#047857")
            else:
                self.lbl_found.config(text="Folder does not exist.", fg="#DC2626")
        except Exception as e:
            self.lbl_found.config(text=f"Error reading folder: {e}", fg="#DC2626")

    def _browse_out(self):
        d = filedialog.askdirectory(title="Select Output Folder", parent=self.dialog)
        if d:
            self.out_dir_var.set(d)

    def _proceed_to_viewer(self):
        in_dir = self.in_dir_var.get().strip()
        out_dir = self.out_dir_var.get().strip()

        if not in_dir or not os.path.isdir(in_dir):
            return messagebox.showerror("Input Folder Error", "Please select a valid input directory.", parent=self.dialog)
        if not out_dir:
            return messagebox.showerror("Output Folder Error", "Please specify an output directory.", parent=self.dialog)

        pdf_files = [f for f in sorted(os.listdir(in_dir)) if f.lower().endswith('.pdf')]
        if not pdf_files:
            return messagebox.showerror("No PDFs Found", "No PDF files (*.pdf) were found in the selected input directory.", parent=self.dialog)

        self.dialog.destroy()
        self.main_app.enter_bulk_mode(in_dir, out_dir, pdf_files)


class BulkProgressDialog:
    """
    Step 2: Progress dialog displayed while signing all PDFs across all pages.
    """
    def __init__(self, parent, main_app, in_dir, out_dir, pdf_files, selected_area):
        self.dialog = tk.Toplevel(parent)
        self.dialog.title(f"🚀 Bulk Signing All PDFs [{CURRENT_OS}]")
        self.dialog.geometry("560x280")
        self.dialog.resizable(False, False)
        self.dialog.transient(parent)
        self.dialog.grab_set()

        self.main_app = main_app
        self.in_dir = in_dir
        self.out_dir = out_dir
        self.pdf_files = pdf_files
        self.selected_area = selected_area

        if hasattr(self.main_app, '_apply_window_icon'):
            self.main_app._apply_window_icon(self.dialog)

        self.is_running = True
        self.is_cancelled = False

        self.dialog.protocol("WM_DELETE_WINDOW", self._on_close)
        self._build_ui()
        self._start_thread()

    def _build_ui(self):
        f = ttk.Frame(self.dialog, padding="18")
        f.pack(fill=tk.BOTH, expand=True)

        ttk.Label(
            f, text="🚀 Bulk Signing In Progress...",
            font=get_sys_font(12, "bold"), foreground="#1E40AF"
        ).pack(anchor=tk.W, pady=(0, 4))

        self.lbl_sub = tk.Label(
            f, text=f"Signing all pages of {len(self.pdf_files)} PDF file(s) with selected area...",
            font=get_sys_font(8), fg="#475569", anchor="w"
        )
        self.lbl_sub.pack(fill=tk.X, pady=(0, 10))

        self.prog_var = tk.DoubleVar(value=0.0)
        self.prog_bar = ttk.Progressbar(f, variable=self.prog_var, maximum=100)
        self.prog_bar.pack(fill=tk.X, pady=(0, 8))

        self.lbl_current = tk.Label(
            f, text="Initializing cryptographic engine...",
            font=get_sys_font(8, "bold"), fg="#1E40AF", anchor="w"
        )
        self.lbl_current.pack(fill=tk.X, pady=(0, 4))

        self.lbl_stats = tk.Label(
            f, text="✓ Signed: 0 | ❌ Failed: 0",
            font=get_sys_font(8), fg="#64748B", anchor="w"
        )
        self.lbl_stats.pack(fill=tk.X, pady=(0, 10))

        btn_row = ttk.Frame(f)
        btn_row.pack(side=tk.BOTTOM, fill=tk.X)
        self.btn_stop = ttk.Button(btn_row, text="⏹️ Cancel / Stop", style="Tool3D.TButton", command=self._stop)
        self.btn_stop.pack(side=tk.RIGHT)

    def _on_close(self):
        if self.is_running:
            if messagebox.askyesno("Stop Signing", "Bulk signing is currently in progress.\nDo you want to stop?", parent=self.dialog):
                self.is_cancelled = True
                self.dialog.destroy()
        else:
            self.dialog.destroy()

    def _stop(self):
        if self.is_running:
            self.is_cancelled = True
            self.lbl_current.config(text="Stopping process after current file...", fg="#DC2626")
            self.btn_stop.config(state=tk.DISABLED)

    def _start_thread(self):
        t = threading.Thread(target=self._worker)
        t.daemon = True
        t.start()

    def _worker(self):
        total = len(self.pdf_files)
        success_cnt, fail_cnt = 0, 0
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
                    try:
                        token = slot.get_token()
                        break
                    except Exception:
                        continue
                if not token:
                    raise ValueError("Hardware USB Token not detected. Please ensure your USB Token is attached.")

                session = token.open(user_pin=pin)
                cert_id = None
                for obj in session.get_objects({pkcs11.Attribute.CLASS: pkcs11.ObjectClass.CERTIFICATE}):
                    c = x509.load_der_x509_certificate(obj[pkcs11.Attribute.VALUE])
                    if str(c.serial_number) == str(serial_number):
                        cert_id, cert_parsed = obj[pkcs11.Attribute.ID], c
                        break
                if not cert_id:
                    raise ValueError("Hardware Certificate not found on Token.")
                signer = PKCS11Signer(pkcs11_session=session, cert_id=cert_id)

            try:
                tst_client = HTTPTimeStamper(TSA_URL)
            except Exception:
                tst_client = None

            for i, fname in enumerate(self.pdf_files):
                if self.is_cancelled:
                    logger.info("Bulk signing stopped by user.")
                    break

                in_path = os.path.join(self.in_dir, fname)
                out_path = os.path.join(self.out_dir, fname)

                self.dialog.after(0, lambda f=fname, n=i+1: self.lbl_current.config(
                    text=f"Signing ({n}/{total}): {f} (All Pages)...", fg="#1E40AF"
                ))

                try:
                    with open(in_path, 'rb') as f_in:
                        pdf_data = f_in.read()

                    doc = fitz.open(stream=pdf_data, filetype="pdf")
                    n_pages = len(doc)
                    if n_pages <= 0:
                        raise ValueError("PDF has 0 pages.")

                    # Build area for ALL pages of this PDF using user-selected coordinates
                    tmpl = self.selected_area
                    areas = []
                    for p in range(n_pages):
                        pw = float(doc[p].rect.width)
                        ph = float(doc[p].rect.height)

                        ax = float(tmpl['x'])
                        ay = float(tmpl['y'])
                        aw = float(tmpl['width'])
                        ah = float(tmpl['height'])

                        ax_c = max(0.0, min(ax, max(0.0, pw - 20.0)))
                        ay_c = max(0.0, min(ay, max(0.0, ph - 20.0)))
                        aw_c = min(aw, max(10.0, pw - ax_c))
                        ah_c = min(ah, max(10.0, ph - ay_c))

                        areas.append({'page': p, 'x': ax_c, 'y': ay_c, 'width': aw_c, 'height': ah_c})

                    doc.close()

                    signed_bytes = sign_pdf_stream(pdf_data, areas, signer, cert_parsed, tst_client)
                    with open(out_path, 'wb') as f_out:
                        f_out.write(signed_bytes)
                    success_cnt += 1
                except Exception as e:
                    logger.error(f"Error signing {fname}: {e}")
                    fail_cnt += 1

                prog = ((i + 1) / total) * 100.0
                self.dialog.after(0, lambda p=prog, s=success_cnt, fl=fail_cnt: (
                    self.prog_var.set(p),
                    self.lbl_stats.config(text=f"✓ Signed: {s} | ❌ Failed: {fl}")
                ))

            if session:
                try:
                    session.close()
                except Exception:
                    pass

            status_note = "Stopped by user" if self.is_cancelled else "Complete"
            summary_msg = f"Bulk Signing {status_note}!\n\n✓ Successfully Signed: {success_cnt} PDF(s) (All Pages)\n❌ Failed / Skipped: {fail_cnt}\n\nSaved in:\n{self.out_dir}"

            def _finish():
                if messagebox.askyesno("Bulk Status", summary_msg + "\n\nWould you like to open the output folder now?", parent=self.dialog):
                    try:
                        if CURRENT_OS == 'Windows':
                            os.startfile(self.out_dir)
                        elif CURRENT_OS == 'Darwin':
                            subprocess.Popen(['open', self.out_dir])
                        else:
                            subprocess.Popen(['xdg-open', self.out_dir])
                    except Exception:
                        pass
                self.dialog.destroy()
                self.main_app.exit_bulk_mode(confirm=False)

            self.dialog.after(0, _finish)

        except Exception as err:
            logger.error(f"Bulk signer setup error: {err}")
            self.dialog.after(0, lambda e=str(err): messagebox.showerror("Bulk Error", e, parent=self.dialog))
            self.dialog.after(0, self.dialog.destroy)
        finally:
            self.is_running = False

# Backward-compatibility alias
BatchSignDialog = BatchFolderDialog

# ==========================================
# 4.6. SETTINGS & CUSTOM DRIVERS DIALOG
# ==========================================
class SettingsDialog:
    def __init__(self, parent, main_app, initial_tab=None):
        self.dialog = tk.Toplevel(parent)
        self.dialog.title(f"⚙️ Settings - License & Hardware Token Drivers [{CURRENT_OS}]")
        self.dialog.geometry("700x560")
        self.dialog.minsize(620, 500)
        self.dialog.transient(parent)
        self.dialog.grab_set()
        self.main_app = main_app

        if hasattr(self.main_app, '_apply_window_icon'):
            self.main_app._apply_window_icon(self.dialog)

        self.custom_drivers = load_custom_drivers()
        self.driver_name_var = tk.StringVar()
        self.driver_path_var = tk.StringVar()

        self._build_ui()
        if initial_tab == "drivers":
            self.notebook.select(self.tab_drivers)

    def _build_ui(self):
        root_f = ttk.Frame(self.dialog, padding="12")
        root_f.pack(fill=tk.BOTH, expand=True)

        header_lbl = ttk.Label(
            root_f, text="A B INFOTECH - System Settings & Token Manager",
            font=get_sys_font(12, "bold"), foreground="#1E40AF"
        )
        header_lbl.pack(anchor=tk.W, pady=(0, 8))

        self.notebook = ttk.Notebook(root_f)
        self.notebook.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        # Tab 1: License & Validity
        self.tab_license = ttk.Frame(self.notebook, padding="12")
        self.notebook.add(self.tab_license, text="🛡️ License & Validity")
        self._build_license_tab(self.tab_license)

        # Tab 2: Custom Token Drivers
        self.tab_drivers = ttk.Frame(self.notebook, padding="12")
        self.notebook.add(self.tab_drivers, text="🔌 Token Drivers (.dll / .dylib / .so)")
        self._build_drivers_tab(self.tab_drivers)

        # Bottom Close Button
        btn_row = ttk.Frame(root_f)
        btn_row.pack(fill=tk.X, side=tk.BOTTOM)
        ttk.Button(btn_row, text="Close Settings", style="Tool3D.TButton", command=self.dialog.destroy).pack(side=tk.RIGHT)

    def _build_license_tab(self, parent):
        import uuid
        mac_node = uuid.getnode()
        formatted_mac = ":".join(f"{(mac_node >> (i * 8)) & 0xff:02X}" for i in reversed(range(6)))

        key, expiredate = LicenseManager.get_license_data()
        status = LicenseManager.check_expiration_local()

        # License Info Card
        card = tk.Frame(parent, bg="#FFFFFF", relief=tk.RAISED, bd=1, padx=12, pady=10)
        card.pack(fill=tk.X, pady=(0, 10))

        tk.Label(card, text="Hardware & License Status", font=get_sys_font(10, "bold"), fg="#1E3A8A", bg="#FFFFFF").pack(anchor=tk.W, pady=(0, 6))

        info_grid = tk.Frame(card, bg="#FFFFFF")
        info_grid.pack(fill=tk.X, pady=4)

        # Hardware ID
        tk.Label(info_grid, text="Hardware / Machine ID:", font=get_sys_font(9, "bold"), bg="#FFFFFF", fg="#475569").grid(row=0, column=0, sticky="w", pady=4)
        hw_row = tk.Frame(info_grid, bg="#FFFFFF")
        hw_row.grid(row=0, column=1, sticky="w", padx=8, pady=4)
        hw_lbl = tk.Label(hw_row, text=formatted_mac, font=get_sys_font(9, "bold"), fg="#0E7490", bg="#E0F2FE", padx=6, pady=2, relief=tk.SOLID, bd=1)
        hw_lbl.pack(side=tk.LEFT)
        def copy_hw():
            self.dialog.clipboard_clear()
            self.dialog.clipboard_append(formatted_mac)
            messagebox.showinfo("Copied", "Hardware ID copied to clipboard!", parent=self.dialog)
        ttk.Button(hw_row, text="📋 Copy", style="Tool3D.TButton", command=copy_hw).pack(side=tk.LEFT, padx=6)

        # License Key
        tk.Label(info_grid, text="Current License Key:", font=get_sys_font(9, "bold"), bg="#FFFFFF", fg="#475569").grid(row=1, column=0, sticky="w", pady=4)
        disp_key = key if key else "Not Activated"
        tk.Label(info_grid, text=disp_key, font=get_sys_font(9, "bold"), fg="#1E293B", bg="#F1F5F9", padx=6, pady=2, relief=tk.SOLID, bd=1).grid(row=1, column=1, sticky="w", padx=8, pady=4)

        # Validity
        tk.Label(info_grid, text="License Validity:", font=get_sys_font(9, "bold"), bg="#FFFFFF", fg="#475569").grid(row=2, column=0, sticky="w", pady=4)
        if status == "active":
            validity_text = "🟢 Active (Lifetime)" if expiredate == "never" else f"🟢 Active (Expires: {expiredate})"
            val_color = "#047857"
        elif status == "expired":
            validity_text = f"🔴 Expired on {expiredate}"
            val_color = "#DC2626"
        else:
            validity_text = "🟡 Inactive / Demo Mode"
            val_color = "#D97706"

        tk.Label(info_grid, text=validity_text, font=get_sys_font(9, "bold"), fg=val_color, bg="#FFFFFF").grid(row=2, column=1, sticky="w", padx=8, pady=4)

        # License Renewal Card
        renew_card = tk.Frame(parent, bg="#F0FDF4", relief=tk.GROOVE, bd=1, padx=12, pady=10)
        renew_card.pack(fill=tk.X, pady=(5, 10))

        tk.Label(renew_card, text="🔄 Renew or Upgrade License", font=get_sys_font(10, "bold"), fg="#065F46", bg="#F0FDF4").pack(anchor=tk.W)
        tk.Label(
            renew_card,
            text="If your license is nearing expiry or expired, you can renew it immediately via our official payment portal:\nhttps://pay.a-b-infotech.in/license.php",
            font=get_sys_font(8), fg="#047857", bg="#F0FDF4", justify="left"
        ).pack(anchor=tk.W, pady=(4, 8))

        btn_box = tk.Frame(renew_card, bg="#F0FDF4")
        btn_box.pack(anchor=tk.W)

        def open_renew():
            webbrowser.open("https://pay.a-b-infotech.in/license.php")

        btn_renew = ttk.Button(btn_box, text="🌐 RENEW LICENSE ONLINE", style="Primary3D.TButton", command=open_renew)
        btn_renew.pack(side=tk.LEFT, padx=(0, 8), ipady=2)

        def prompt_activate():
            self.dialog.destroy()
            self.main_app._show_activation_screen()

        btn_activate = ttk.Button(btn_box, text="🔑 Enter New License Key", style="Tool3D.TButton", command=prompt_activate)
        btn_activate.pack(side=tk.LEFT)

    def _build_drivers_tab(self, parent):
        ext_hint = ".dll" if CURRENT_OS == 'Windows' else (".dylib/.so" if CURRENT_OS == 'Darwin' else ".so")

        # Top Add Form
        add_card = tk.Frame(parent, bg="#FFFFFF", relief=tk.RAISED, bd=1, padx=10, pady=10)
        add_card.pack(fill=tk.X, pady=(0, 10))

        tk.Label(add_card, text="Register Custom Hardware Token Driver (PKCS#11)", font=get_sys_font(10, "bold"), fg="#1E3A8A", bg="#FFFFFF").pack(anchor=tk.W)
        tk.Label(
            add_card,
            text=f"If your USB token is not in the default list, browse and select its PKCS#11 library ({ext_hint}):",
            font=get_sys_font(8), fg="#64748B", bg="#FFFFFF"
        ).pack(anchor=tk.W, pady=(2, 6))

        form_f = tk.Frame(add_card, bg="#FFFFFF")
        form_f.pack(fill=tk.X)

        tk.Label(form_f, text="Token Name / Label:", font=get_sys_font(8, "bold"), bg="#FFFFFF", fg="#334155").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Entry(form_f, textvariable=self.driver_name_var, width=32, font=get_sys_font(8)).grid(row=0, column=1, sticky="w", padx=6, pady=3)

        tk.Label(form_f, text=f"Driver Library File ({ext_hint}):", font=get_sys_font(8, "bold"), bg="#FFFFFF", fg="#334155").grid(row=1, column=0, sticky="w", pady=3)
        path_box = tk.Frame(form_f, bg="#FFFFFF")
        path_box.grid(row=1, column=1, sticky="we", padx=6, pady=3)
        ttk.Entry(path_box, textvariable=self.driver_path_var, width=38, font=get_sys_font(8)).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(path_box, text="Browse...", style="Tool3D.TButton", command=self._browse_driver_file).pack(side=tk.LEFT, padx=(4, 0))

        # Action Buttons
        btn_f = tk.Frame(add_card, bg="#FFFFFF")
        btn_f.pack(anchor=tk.W, pady=(8, 2))
        ttk.Button(btn_f, text="🧪 Test Driver Library", style="Tool3D.TButton", command=self._test_driver).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(btn_f, text="➕ Add & Save Driver", style="Primary3D.TButton", command=self._add_driver).pack(side=tk.LEFT)

        # Existing Custom Drivers List
        list_card = tk.Frame(parent, bg="#FFFFFF", relief=tk.RAISED, bd=1, padx=10, pady=8)
        list_card.pack(fill=tk.BOTH, expand=True)

        tk.Label(list_card, text="Active Custom Token Drivers:", font=get_sys_font(9, "bold"), fg="#1E3A8A", bg="#FFFFFF").pack(anchor=tk.W)

        tree_f = tk.Frame(list_card, bg="#FFFFFF")
        tree_f.pack(fill=tk.BOTH, expand=True, pady=4)

        self.tree = ttk.Treeview(tree_f, columns=("Name", "Path"), show="headings", height=5)
        self.tree.heading("Name", text="Token Name")
        self.tree.heading("Path", text="Driver File Path")
        self.tree.column("Name", width=160)
        self.tree.column("Path", width=380)

        tree_scroll = ttk.Scrollbar(tree_f, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self._populate_tree()

        # Delete Button
        ttk.Button(list_card, text="🗑️ Remove Selected Driver", style="Tool3D.TButton", command=self._remove_driver).pack(anchor=tk.E, pady=(4, 0))

    def _browse_driver_file(self):
        if CURRENT_OS == 'Windows':
            ftypes = [("Dynamic Link Library (*.dll)", "*.dll"), ("All Files", "*.*")]
        elif CURRENT_OS == 'Darwin':
            ftypes = [("Dynamic Library (*.dylib;*.so)", "*.dylib;*.so"), ("All Files", "*.*")]
        else:
            ftypes = [("Shared Object (*.so)", "*.so*"), ("All Files", "*.*")]

        p = filedialog.askopenfilename(title=f"Select PKCS#11 Driver Library ({CURRENT_OS})", filetypes=ftypes, parent=self.dialog)
        if p:
            self.driver_path_var.set(p)
            if not self.driver_name_var.get():
                base = os.path.basename(p)
                name_guess = os.path.splitext(base)[0].upper()
                self.driver_name_var.set(f"Token ({name_guess})")

    def _test_driver(self):
        p = self.driver_path_var.get().strip()
        if not p or not os.path.exists(p):
            return messagebox.showerror("File Error", "Please select a valid driver library file first.", parent=self.dialog)

        try:
            lib = pkcs11.lib(p)
            slots = lib.get_slots(token_present=False)
            messagebox.showinfo(
                "Driver Test Successful",
                f"✓ Successfully initialized PKCS#11 Library!\nPath: {p}\nAvailable Slots: {len(slots)}",
                parent=self.dialog
            )
        except Exception as e:
            messagebox.showerror(
                "Driver Test Failed",
                f"Could not initialize PKCS#11 driver:\n{str(e)}\n\nMake sure the driver architecture (32/64-bit) matches your OS.",
                parent=self.dialog
            )

    def _add_driver(self):
        name = self.driver_name_var.get().strip()
        path = self.driver_path_var.get().strip()
        if not name:
            return messagebox.showerror("Input Error", "Please enter a Token Name.", parent=self.dialog)
        if not path or not os.path.exists(path):
            return messagebox.showerror("Input Error", "Please select a valid existing driver file.", parent=self.dialog)

        self.custom_drivers[name] = path
        save_custom_drivers(self.custom_drivers)
        self._populate_tree()

        # Update main app combobox and select it
        driver_label = f"⭐ {name}"
        self.main_app.reload_drivers(select_driver=driver_label)

        messagebox.showinfo("Driver Added", f"Custom token driver '{name}' saved successfully and activated!", parent=self.dialog)
        self.driver_name_var.set("")
        self.driver_path_var.set("")

    def _remove_driver(self):
        sel = self.tree.selection()
        if not sel:
            return messagebox.showwarning("Select Driver", "Please select a driver from the list to remove.", parent=self.dialog)

        name = self.tree.item(sel[0])['values'][0]
        if name in self.custom_drivers:
            del self.custom_drivers[name]
            save_custom_drivers(self.custom_drivers)
            self._populate_tree()
            self.main_app.reload_drivers()
            messagebox.showinfo("Removed", f"Driver '{name}' removed successfully.", parent=self.dialog)

    def _populate_tree(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        for name, path in self.custom_drivers.items():
            self.tree.insert("", tk.END, values=(name, path))

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
    for f in ["image_546aa0.png", LicenseManager.LICENSE_FILE, "app_icon.png", "app_icon.ico", "icon.ico"]:
        if f and os.path.exists(f):
            try: return Image.open(f)
            except Exception: pass

    if hasattr(sys, '_MEIPASS'):
        for icon_name in ['image_546aa0.png', 'app_icon.ico', 'app_icon.png', 'app_icon.icns']:
            fallback = os.path.join(sys._MEIPASS, icon_name)
            if os.path.exists(fallback):
                try: return Image.open(fallback)
                except Exception: pass

    return Image.new('RGB', (64, 64), color=(0, 120, 215))

def on_open_app(icon, item):
    gui_root.after(0, gui_app.show_window)

def on_quick_sign(icon, item):
    def _do():
        gui_app.show_window()
        gui_app.open_pdf()
    gui_root.after(0, _do)

def on_bulk_sign(icon, item):
    def _do():
        gui_app.show_window()
        gui_app.open_batch_dialog()
    gui_root.after(0, _do)

def on_settings_dialog(icon, item):
    def _do():
        gui_app.show_window()
        gui_app.open_settings_dialog()
    gui_root.after(0, _do)

def on_start_clicked(icon, item):
    start_flask_server()

def on_stop_clicked(icon, item):
    stop_flask_server()

def on_quit_clicked(icon, item):
    stop_flask_server()
    icon.stop()
    gui_root.after(0, gui_root.destroy)

def setup_tray():
    global tray_icon
    menu = pystray.Menu(
        item('📂 Open Application (Bring to Front)', on_open_app, default=True),
        pystray.Menu.SEPARATOR,
        item('✍️ Quick Sign Single PDF...', on_quick_sign),
        item('📁 Bulk Sign Folder...', on_bulk_sign),
        item('⚙️ Settings & License', on_settings_dialog),
        pystray.Menu.SEPARATOR,
        item('🌐 Start Web Bridge', on_start_clicked),
        item('🛑 Stop Web Bridge', on_stop_clicked),
        pystray.Menu.SEPARATOR,
        item('❌ Quit Entirely', on_quit_clicked)
    )
    tray_icon = pystray.Icon("PDF_Signer", load_icon(), f"A B INFOTECH Signer [{CURRENT_OS}]", menu)
    threading.Thread(target=tray_icon.run, daemon=True).start()

# ==========================================
# 6. APPLICATION ENTRY POINT
# ==========================================
if __name__ == '__main__':
    # Detect background startup arguments
    is_background = any(arg.lower() in ['--background', '--tray', '--minimized', '--autostart', '-b'] for arg in sys.argv[1:])

    # Single-Instance Enforcement:
    # If another instance is running:
    # - If launched by user (desktop / taskbar), wake_up=True brings the existing window to front!
    # - If background autostart, wake_up=False keeps it running silently.
    # Secondary process terminates immediately (sys.exit(0)).
    if check_single_instance_and_bring_to_front(wake_up=(not is_background)):
        logger.info("Existing instance detected and requested to front. Exiting duplicate process.")
        sys.exit(0)

    # Automatically register system autostart in silent background mode by default
    try:
        set_autostart(True)
    except Exception as e:
        logger.warning(f"Default autostart setup note: {e}")

    # Start Flask Web Bridge server
    start_flask_server()

    # Initialize Tkinter GUI
    gui_root = tk.Tk()
    # CRITICAL: Withdraw window immediately if starting in background to prevent any flash on screen
    if is_background:
        gui_root.withdraw()

    gui_app = VisualPdfSignerApp(gui_root)

    # Initialize System Tray
    setup_tray()

    if is_background:
        gui_app.hide_window()
        logger.info("A B INFOTECH Signer started silently in background / system tray.")
    else:
        gui_root.after(150, gui_app.show_window)

    gui_root.mainloop()
