#!/bin/bash

DOMAIN="rb3pixel.ddns.net"
EMAIL="admin@rb3pixel.ddns.net"
TARGET_CERT_DIR="/home/server/filetransfer/certs" # Updated to match Apache config error

echo "--- Installing Certbot ---"
sudo apt-get update
sudo apt-get install -y certbot python3-certbot-apache openssl

# FIX: Apache fails validation if these files don't exist yet.
# We generate temporary self-signed certs just to make 'apache2ctl configtest' happy.
if [ ! -f "$TARGET_CERT_DIR/server.crt" ]; then
    echo "--- Generating Temporary Dummy Certs (Bootstrap) ---"
    mkdir -p "$TARGET_CERT_DIR"
    openssl req -x509 -nodes -days 1 -newkey rsa:2048 \
        -keyout "$TARGET_CERT_DIR/server.key" \
        -out "$TARGET_CERT_DIR/server.crt" \
        -subj "/C=US/ST=Fix/L=Temp/O=Bootstrap/CN=$DOMAIN"
    
    # Permission fix
    sudo chown -R server:server "$TARGET_CERT_DIR"
    sudo chmod 644 "$TARGET_CERT_DIR/server.crt"
    sudo chmod 600 "$TARGET_CERT_DIR/server.key"
fi

echo "--- Requesting Certificate for $DOMAIN ---"
# Since Apache is running on Port 80, we use the --apache plugin instead of --standalone
# This avoids the "Port 80 already in use" error
sudo certbot certonly --apache -d $DOMAIN --non-interactive --agree-tos -m $EMAIL

if [ -d "/etc/letsencrypt/live/$DOMAIN" ]; then
    echo "--- Certificate Obtained! ---"
    
    # Copy to our app directory so Python can find it
    echo "Updating Session Broker Certs..."
    sudo cp /etc/letsencrypt/live/$DOMAIN/fullchain.pem "$TARGET_CERT_DIR/server.crt"
    sudo cp /etc/letsencrypt/live/$DOMAIN/privkey.pem "$TARGET_CERT_DIR/server.key"
    
    # Fix permissions so our user can read them
    sudo chown -R server:server "$TARGET_CERT_DIR"
    sudo chmod 644 "$TARGET_CERT_DIR/server.crt"
    sudo chmod 600 "$TARGET_CERT_DIR/server.key"

    echo "--- Configuring Apache ---"
    # We need to point Apache to these new real certs too
    # Assuming default-ssl.conf or similar, but easiest is to let certbot handle apache or just symlink
    # For now, let's just make sure our Broker has them.
    
    echo "--- Restarting Broker ---"
    pkill -f session_broker.py
    # Re-run start script (assuming we are in the dir)
    bash ./start_nas_browser.sh
    
    echo "SUCCESS: Valid SSL Installed."
    echo "1. Port 80 MUST be forwarded for renewal."
    echo "2. Port 443 MUST be forwarded for HTTPS access."
else
    echo "ERROR: Certbot failed. Make sure Port 80 is open and forwarded to this server!"
fi
