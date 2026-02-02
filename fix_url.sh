#!/bin/bash

echo "--- Updating Apache Configuration ---"

# 1. Overwrite the existing config with our new ROOT config
sudo cp y:/nas-browser-root.conf /etc/apache2/sites-available/nas-browser-ssl.conf

# 2. Enable it (just in case)
sudo a2ensite nas-browser-ssl.conf

# 3. Reload Apache
sudo systemctl reload apache2

echo "--- SUCCESS ---"
echo "You can now access: https://rb3pixel.ddns.net/"
echo "(No more /browser/ needed!)"
