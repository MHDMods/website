#!/bin/bash
echo "----------------------------------------"
echo "Starting NAS Browser BROKER"
echo "----------------------------------------"

# 1. CLEANUP (The Nuclear Option)
echo "Stopping existing processes..."
pkill -9 -f "session_broker.py"
pkill -9 -f "Xvfb"
pkill -9 -f "firefox"
pkill -9 -f "x11vnc"
pkill -9 -f "websockify"
pkill -9 -f "novnc_proxy"

echo "Scrubbing ports 6100-6200..."
for port in {6100..6200}; do
    fuser -k -n tcp $port > /dev/null 2>&1
done
sleep 2 # Let sockets release

echo "Starting Broker..."
nohup python3 session_broker.py > broker.log 2>&1 &
BROKER_PID=$!

echo "Broker running (PID: $BROKER_PID) on Port 6081"
echo "----------------------------------------"
echo "Service Ready at http://192.168.10.10/browser/index.html"
echo "----------------------------------------"
