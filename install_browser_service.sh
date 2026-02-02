#!/bin/bash

# Stop on error
set -e

APP_DIR="/home/server/filetransfer"

echo "Starting NAS Browser Setup..."
echo "Target Directory: $APP_DIR"

# 0. Check we are in the right place or can access it
if [ ! -d "$APP_DIR" ]; then
    echo "Error: Directory $APP_DIR does not exist on this machine."
    echo "Are you running this on the NAS?"
    exit 1
fi

# 1. Install Dependencies
echo "Installing Firefox, Xvfb, x11vnc, Apache2, and basic tools..."
sudo apt-get update
sudo apt-get install -y firefox xvfb x11vnc apache2 git python3 python3-numpy net-tools

# 2. Setup noVNC
echo "Installing noVNC..."
if [ ! -d "$APP_DIR/noVNC" ]; then
    git clone https://github.com/novnc/noVNC.git "$APP_DIR/noVNC"
    git clone https://github.com/novnc/websockify "$APP_DIR/noVNC/utils/websockify"
else
    echo "noVNC already exists in $APP_DIR/noVNC, skipping clone."
fi

# 2.1 Setup Directories
echo "Setting up directories..."
mkdir -p "$APP_DIR/downloads"
mkdir -p "$APP_DIR/profiles"

# 2.2 Create Firefox Template
echo "Creating Firefox Template..."
TEMPLATE_DIR="$APP_DIR/firefox_template"
mkdir -p "$TEMPLATE_DIR"
cat << PREFS > "$TEMPLATE_DIR/user.js"
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("browser.startup.homepage", "https://google.com");
PREFS

# 3. Configure Apache
echo "Configuring Apache to serve $APP_DIR at /browser..."
CONF_FILE="/etc/apache2/conf-available/nas-browser.conf"
sudo bash -c "cat > $CONF_FILE" << EOF
Alias /browser "$APP_DIR"
<Directory "$APP_DIR">
    Options Indexes FollowSymLinks
    AllowOverride None
    Require all granted
</Directory>
EOF

sudo a2enconf nas-browser
sudo systemctl reload apache2

# 4. Fix Permissions
echo "Adjusting permissions..."
# Ensure all these are writable by the user running the broker (likely 'server')
sudo chown -R $USER:$USER "$APP_DIR"
sudo chmod -R 777 "$APP_DIR/downloads"
sudo chmod -R 777 "$APP_DIR/profiles"

# Apache needs read access
sudo chmod o+x /home /home/server /home/server/filetransfer

# 5. Create Startup Script
echo "Creating startup script..."
START_SCRIPT="$APP_DIR/start_nas_browser.sh"
cat << EOF > "$START_SCRIPT"
#!/bin/bash
echo "----------------------------------------"
echo "Starting NAS Browser BROKER"
echo "----------------------------------------"

# Cleanup from previous runs
pkill -f session_broker.py
# Aggressive cleanup of orphaned sessions? 
# pkill Xvfb
# pkill firefox
# rm -rf $APP_DIR/profiles/*
# rm -rf $APP_DIR/downloads/*

echo "Starting Broker..."
nohup python3 session_broker.py > broker.log 2>&1 &
BROKER_PID=\$!

echo "Broker running (PID: \$BROKER_PID) on Port 6081"
echo "----------------------------------------"
echo "Service Ready at http://192.168.10.10/browser/index.html"
echo "----------------------------------------"
EOF

chmod +x "$START_SCRIPT"

echo "----------------------------------------"
echo "SETUP SUCCESSFUL!"
echo "----------------------------------------"
echo "IMPORTANT: You must open ports 6081-6200 on firewall!"
echo "Run: sudo ufw allow 6081:6200/tcp"
echo "Then start: bash $START_SCRIPT"

chmod +x "$START_SCRIPT"

echo "----------------------------------------"
echo "SETUP SUCCESSFUL!"
echo "----------------------------------------"
echo "Now run the browser service with:"
echo "bash $START_SCRIPT"
