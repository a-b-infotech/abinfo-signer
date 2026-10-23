#!/bin/bash
set -e

echo "=== Installing A B INFOTECH SIGNER OFFLINE ==="

if [ "$EUID" -ne 0 ]; then
  echo "Please run as root or with sudo: sudo ./install.sh"
  exit 1
fi

APP_DIR="/opt/abinfo-signer"
mkdir -p "$APP_DIR"
cp Abinfo_Signer_Offline "$APP_DIR/" 2>/dev/null || cp ../Abinfo_Signer_Offline "$APP_DIR/" 2>/dev/null || true
cp ab_infotech_ca_cert.cer "$APP_DIR/" 2>/dev/null || cp ../ab_infotech_ca_cert.cer "$APP_DIR/" 2>/dev/null || true
cp app_icon.png "$APP_DIR/icon.png" 2>/dev/null || cp icon.png "$APP_DIR/icon.png" 2>/dev/null || true
chmod +x "$APP_DIR/Abinfo_Signer_Offline"

# 1. Install CA Root Certificate
echo "Installing Security Certificate to System Trust Store..."
if [ -d "/usr/local/share/ca-certificates" ]; then
  cp "$APP_DIR/ab_infotech_ca_cert.cer" /usr/local/share/ca-certificates/ab_infotech_ca_cert.crt 2>/dev/null || true
  update-ca-certificates || true
elif [ -d "/etc/pki/ca-trust/source/anchors" ]; then
  cp "$APP_DIR/ab_infotech_ca_cert.cer" /etc/pki/ca-trust/source/anchors/ab_infotech_ca_cert.crt 2>/dev/null || true
  update-ca-trust || true
fi

# 2. Create Symlink
ln -sf "$APP_DIR/Abinfo_Signer_Offline" /usr/local/bin/abinfo-signer

# 3. Create Desktop Launcher
cat <<EOF > /usr/share/applications/abinfo-signer.desktop
[Desktop Entry]
Name=A B INFOTECH SIGNER OFFLINE
Comment=Offline PDF Digital Signer with USB Token & Certificate Support
Exec=/opt/abinfo-signer/Abinfo_Signer_Offline
Icon=/opt/abinfo-signer/icon.png
Terminal=false
Type=Application
Categories=Office;Utility;
EOF
chmod +x /usr/share/applications/abinfo-signer.desktop

# 4. Copy to user desktop if directory exists
SUDO_USER_HOME=$(eval echo ~${SUDO_USER:-$USER})
if [ -d "$SUDO_USER_HOME/Desktop" ]; then
  cp /usr/share/applications/abinfo-signer.desktop "$SUDO_USER_HOME/Desktop/"
  chown ${SUDO_USER:-$USER} "$SUDO_USER_HOME/Desktop/abinfo-signer.desktop" || true
  chmod +x "$SUDO_USER_HOME/Desktop/abinfo-signer.desktop" || true
fi

# 5. Autostart
AUTOSTART_DIR="$SUDO_USER_HOME/.config/autostart"
mkdir -p "$AUTOSTART_DIR"
cp /usr/share/applications/abinfo-signer.desktop "$AUTOSTART_DIR/"
chown ${SUDO_USER:-$USER} "$AUTOSTART_DIR/abinfo-signer.desktop" || true

echo "✓ A B INFOTECH SIGNER OFFLINE installed successfully!"
echo "Launch from Applications menu or type 'abinfo-signer'."
