# ABInfo Digital PDF Signer (Cross-Platform)

Professional, cross-platform digital signature solution for PDF documents supporting Windows, macOS, and Linux.

## Features
- **Hardware Token (PKCS#11)**: Auto-detects ePass2003, WatchData, ProxKey, mToken, TrustKey, SafeNet, and custom PKCS#11 libraries across Windows (`.dll`), macOS (`.dylib`), and Linux (`.so`).
- **Software Certificate (.PFX / .P12)**: Sign documents using direct software certificates without a physical USB token.
- **3D Visual Signature Stamp**: Custom designer with gradient styling, 3D border effects, organization details, reason, location, and timestamp.
- **Batch & Multi-Page Signing**: Sign all pages, first page, last page, or custom page ranges in one click.
- **Cross-Platform Compatibility**: Full feature parity across Windows, macOS, and Linux.
- **Local Web API**: Built-in HTTP service allowing local web applications (PHP, React, Node.js) to trigger signing operations seamlessly.

## Quick Start (Run from Source)
1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Run the application:
   ```bash
   python server2.py
   ```

## Automated Multi-OS Builds (GitHub Actions)
This repository includes a GitHub Actions workflow (`.github/workflows/build.yml`) that automatically builds standalone release packages for:
- **Windows**: `Abinfo_Signer_Windows.exe` (Digitally signed with Authenticode)
- **Linux**: `Abinfo_Signer_Linux.tar.gz` (ELF binary)
- **macOS**: `Abinfo_Signer_macOS.zip` (Universal application bundle)
