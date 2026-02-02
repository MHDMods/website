import http.server
import socketserver
import os
import subprocess
import time
import uuid
import shutil
import json
import threading
import socket
import sys
import ssl # Added for HTTPS

BROKER_PORT = 6081
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOADS_ROOT = os.path.join(BASE_DIR, "downloads")
PROFILE_TEMPLATE = os.path.join(BASE_DIR, "firefox_template")
START_PORT_RANGE = 6100

# PERSISTENT DEBUG LOGGING V2
DEBUG_LOG_FILE = os.path.join(BASE_DIR, "broker_debug_v2.log")
def log(msg):
    try:
        with open(DEBUG_LOG_FILE, "a") as f:
            f.write(f"[{time.strftime('%X')}] {msg}\n")
    except:
        pass
    print(msg) 
    sys.stdout.flush()

# Store active sessions: { session_id: { 'pid_list': [], 'download_path': '', 'ws_port': 0 } }
sessions = {}
sessions_lock = threading.Lock()

def find_available_slot(start_port, end_port, count=3):
    """
    Scans for 'count' consecutive free ports in [start_port, end_port].
    Returns list of ports or None.
    """
    for base in range(start_port, end_port - count + 1):
        # Optimization: We usually want aligned slots (e.g. 6100, 6103, 6106)
        # but pure sequential scan is safer for packing.
        # Let's try to verify a block of 3
        found_block = []
        for offset in range(count):
            target = base + offset
            if is_port_free(target):
                found_block.append(target)
            else:
                break # Block broken, move base
        
        if len(found_block) == count:
            return found_block
            
    return None

def is_port_free(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.1)
        # connect_ex returns 0 if successful (BUSY), error code if failed (FREE for us)
        result = s.connect_ex(('127.0.0.1', port))
        return result != 0

# Garbage Collector
def cleanup_orphans():
    while True:
        time.sleep(10) # Run every 10 seconds
        try:
            # Get active session IDs - Create a copy to iterate safely
            with sessions_lock:
                active_ids = list(sessions.keys())
            
            # 1. Monitor Active Sessions (Auto-Close if Firefox dies)
            for sid in active_ids:
                try:
                    if sid in sessions:
                        # Firefox is always index 1 in our pids list
                        # [Xvfb, Firefox, x11vnc, Websockify]
                        ff_proc = sessions[sid]['pids'][1] 
                        if ff_proc.poll() is not None:
                            log(f"[{sid}] Firefox exited (Code {ff_proc.poll()}). Cleaning up...")
                            
                            info = sessions[sid]
                            
                            # Valid Kill using Process Group
                            for p in info['pids']:
                                try: os.killpg(os.getpgid(p.pid), signal.SIGKILL)
                                except: pass
                            
                            # Fallback cleanup
                            subprocess.run(f"pkill -9 -f {sid}", shell=True)
                            
                            # NUCLEAR: Force Release Ports (The "Double Tap")
                            try:
                                subprocess.run(f"fuser -k -n tcp {info['ports'][1]}", shell=True) # VNC
                                subprocess.run(f"fuser -k -n tcp {info['ports'][2]}", shell=True) # WS
                            except: pass
                            
                            # Remove files
                            for d in info['dirs']:
                                shutil.rmtree(d, ignore_errors=True)
                                
                            # Close log
                            if 'log' in info and info['log']:
                                try: info['log'].close()
                                except: pass
                                
                            with sessions_lock:
                                if sid in sessions: del sessions[sid]
                except Exception as e:
                    log(f"[{sid}] Monitor error: {e}")

            # 2. Scan Profiles (Orphans)
            profiles_root = os.path.join(BASE_DIR, "profiles")
            if os.path.exists(profiles_root):
                with sessions_lock: # Re-acquire lock to be sure
                    current_active = list(sessions.keys())
                    
                for item in os.listdir(profiles_root):
                    if item.startswith("profile_"):
                        sid = item.replace("profile_", "")
                        path = os.path.join(profiles_root, item)
                        
                        # If profile exists but session is NOT active -> DELETE
                        if sid not in current_active:
                            log(f"[GC] Found orphan profile: {sid} (Cleaning...)")
                            try: shutil.rmtree(path, ignore_errors=True)
                            except Exception as e: log(f"[GC] Error cleaning profile {sid}: {e}")
                            
            # 3. Scan Downloads (Orphans)
            if os.path.exists(DOWNLOADS_ROOT):
                with sessions_lock:
                    current_active = list(sessions.keys())

                for item in os.listdir(DOWNLOADS_ROOT):
                    # Ignore .DS_Store or other common junk if needed, assuming dirs are session IDs
                    path = os.path.join(DOWNLOADS_ROOT, item)
                    if os.path.isdir(path):
                        if item not in current_active:
                            log(f"[GC] Found orphan downloads: {item} (Cleaning...)")
                            try: shutil.rmtree(path, ignore_errors=True)
                            except Exception as e: log(f"[GC] Error cleaning download {item}: {e}")

        except Exception as e:
            log(f"[GC] Loop Error: {e}")

# Start GC Thread
threading.Thread(target=cleanup_orphans, daemon=True).start()

# Global cursor for Round-Robin allocation
NEXT_SEARCH_START = START_PORT_RANGE
STATUS_PASSWORD = "admin"  # Simple protection

class SessionBroker(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        # Basic Auth for Status Page - OPEN
        if self.path.startswith('/status'):
            # (Password removed by request)
            
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            
            with sessions_lock:
                active = []
                for sid, info in sessions.items():
                    # Check if PIDs are alive
                    pids_status = []
                    for p in info['pids']:
                        status = "Running" if p.poll() is None else f"Exited({p.poll()})"
                        pids_status.append(status)

                    active.append({
                        'id': sid,
                        'display': info['ports'][0],
                        'vnc_port': info['ports'][1],
                        'ws_port': info['ports'][2],
                        'status': pids_status
                    })
            
            output = {
                'count': len(active),
                'info': "Round-Robin Allocation Active",
                'sessions': active,
                'port_range': f"{START_PORT_RANGE}-6200"
            }
            self.wfile.write(json.dumps(output, indent=2).encode())
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == '/session/start':
            self.start_session()
        elif self.path == '/session/stop':
            self.stop_session()
        else:
            self.send_error(404)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def start_session(self):
        global NEXT_SEARCH_START
        try:
            # 0. Parse Viewport Size (if sent)
            width = 1920
            height = 1080
            try:
                content_len = int(self.headers.get('Content-Length', 0))
                if content_len > 0:
                    body = self.rfile.read(content_len)
                    data = json.loads(body)
                    width = data.get('width', 1920)
                    height = data.get('height', 1080)
                    # Round specific odd sizes if needed, but Xvfb is usually okay
                    width = int(width)
                    height = int(height)
            except: pass # Fallback to default

            session_id = str(uuid.uuid4())[:8]
            log(f"Requesting Session: {session_id} ({width}x{height})")
            
            user_download_dir = os.path.join(DOWNLOADS_ROOT, session_id)
            user_profile_dir = os.path.join(BASE_DIR, f"profiles/profile_{session_id}")
            
            # 1. Prepare Directories
            os.makedirs(user_download_dir, exist_ok=True)
            try:
                os.chmod(user_download_dir, 0o777)
            except: pass
            
            # Copy template profile
            os.makedirs(os.path.dirname(user_profile_dir), exist_ok=True)
            if os.path.exists(PROFILE_TEMPLATE):
                shutil.copytree(PROFILE_TEMPLATE, user_profile_dir)
            else:
                os.makedirs(user_profile_dir, exist_ok=True)
            
            # 2. Configure user.js
            user_js = os.path.join(user_profile_dir, "user.js")
            with open(user_js, "a") as f:
                f.write(f'\nuser_pref("browser.download.dir", "{user_download_dir}");\n')
                f.write('user_pref("browser.download.folderList", 2);\n')
                f.write('user_pref("browser.download.useDownloadDir", true);\n')
                f.write('user_pref("browser.download.manager.showWhenStarting", false);\n')
                # Suppress First Run / Updates / Privacy Tabs
                f.write('user_pref("browser.startup.homepage_override.mstone", "ignore");\n')
                f.write('user_pref("startup.homepage_welcome_url", "");\n')
                f.write('user_pref("startup.homepage_welcome_url.additional", "");\n')
                f.write('user_pref("datareporting.policy.firstRunURL", "");\n')
                f.write('user_pref("datareporting.policy.dataSubmissionEnabled", false);\n')
                f.write('user_pref("datareporting.healthreport.uploadEnabled", false);\n')
                f.write('user_pref("toolkit.telemetry.enabled", false);\n')
                f.write('user_pref("toolkit.telemetry.reportingpolicy.firstRun", false);\n')
                f.write('user_pref("browser.messaging-system.whatsNewPanel.enabled", false);\n')
                f.write('user_pref("app.update.auto", false);\n')
                f.write('user_pref("app.update.enabled", false);\n')
                f.write('user_pref("app.update.silent", true);\n')
                f.write('user_pref("browser.tabs.warnOnClose", false);\n')
                # Disable Annoyances (Onboarding, Tours, Popups)
                f.write('user_pref("browser.onboarding.enabled", false);\n')
                f.write('user_pref("browser.uitour.enabled", false);\n')
                f.write('user_pref("browser.shell.checkDefaultBrowser", false);\n')
                f.write('user_pref("browser.newtabpage.activity-stream.asrouter.userprefs.cfr.addons", false);\n')
                f.write('user_pref("browser.newtabpage.activity-stream.asrouter.userprefs.cfr.features", false);\n')
                f.write('user_pref("browser.translations.enable", false);\n')
                f.write('user_pref("browser.translations.panelShown", true);\n')
                f.write('user_pref("browser.messaging-system.whatsNewPanel.enabled", false);\n')
                f.write('user_pref("extensions.pocket.enabled", false);\n')
                f.write('user_pref("identity.fxaccounts.enabled", false);\n')
                f.write('user_pref("reader.parse-on-load.enabled", false);\n')
                # Mark all tours as complete
                f.write('user_pref("browser.onboarding.tour.onboarding-tour-addons.completed", true);\n')
                f.write('user_pref("browser.onboarding.tour.onboarding-tour-customize.completed", true);\n')
                f.write('user_pref("browser.onboarding.tour.onboarding-tour-default-browser.completed", true);\n')
                f.write('user_pref("browser.onboarding.tour.onboarding-tour-private-browsing.completed", true);\n')
                f.write('user_pref("browser.onboarding.tour.onboarding-tour-search.completed", true);\n')
                f.write('user_pref("browser.onboarding.tour.onboarding-tour-sync.completed", true);\n')

            # 3. Alloc Ports (Round-Robin with Wrap-Around)
            with sessions_lock:
                ports = None
                
                # Attempt 1: Search from cursor to End
                log(f"[{session_id}] Scanning from {NEXT_SEARCH_START} to 6200...")
                ports = find_available_slot(NEXT_SEARCH_START, 6200, 3)
                
                # Attempt 2: Wrap around (Search from Start to cursor)
                if not ports:
                     log(f"[{session_id}] Wrapping scan: {START_PORT_RANGE} to {NEXT_SEARCH_START}...")
                     ports = find_available_slot(START_PORT_RANGE, NEXT_SEARCH_START, 3)
                
                if not ports:
                    # Explicit 503 for "Server Full"
                    self.send_error(503, "No ports available (Server Full)")
                    # Cleanup the dirs we just made since we failed
                    shutil.rmtree(user_download_dir, ignore_errors=True)
                    shutil.rmtree(user_profile_dir, ignore_errors=True)
                    return 
                
                # Update Cursor for next time (Use the port AFTER this block)
                # Found [p, p+1, p+2], next start is p+3
                NEXT_SEARCH_START = ports[2] + 1
                if NEXT_SEARCH_START >= 6200: 
                    NEXT_SEARCH_START = START_PORT_RANGE
                
            port_display_num = ports[0] - 6000 
            if port_display_num < 1: port_display_num += 100
            
            display_str = f":{port_display_num}"
            port_vnc = ports[1]
            port_ws = ports[2]

            log(f"[{session_id}] Assigning: Display {display_str}, VNC {port_vnc}, WS {port_ws}")

            # 4. Spawn Processes
            # Session log file
            session_log_path = os.path.join(user_download_dir, "debug_log.txt")
            session_log = open(session_log_path, "w")
            
            procs = []
            
            # Helper to spawn with Process Group (for clean killing)
            def spawn(cmd):
                return subprocess.Popen(cmd, shell=True, stdout=session_log, stderr=session_log, preexec_fn=os.setsid)

            try:
                # Xvfb (Dynamic Resolution)
                # Align width to 2 (sometimes Xvfb complains about odd widths)
                if width % 2 != 0: width += 1
                
                cmd_xvfb = f"Xvfb {display_str} -screen 0 {width}x{height}x24"
                session_log.write(f"CMD: {cmd_xvfb}\n")
                procs.append(spawn(cmd_xvfb))
                time.sleep(2) 
                
                # Firefox (CRITICAL: --no-remote)
                # Force geometry to match Xvfb exactly so it fills the screen (since we have no Window Manager)
                cmd_ff = f"export DISPLAY={display_str}; export MOZ_FAKE_NO_SANDBOX=1; firefox --no-remote --profile '{user_profile_dir}' --width {width} --height {height} --new-window https://google.com"
                session_log.write(f"CMD: {cmd_ff}\n")
                procs.append(spawn(cmd_ff))
                
                # x11vnc (REMOVED -bg so we can track and kill it)
                cmd_vnc = f"x11vnc -display {display_str} -rfbport {port_vnc} -forever -shared -nopw"
                session_log.write(f"CMD: {cmd_vnc}\n")
                procs.append(spawn(cmd_vnc)) # Added to tracking list
                
                # websockify
                cmd_ws = f"{BASE_DIR}/noVNC/utils/novnc_proxy --vnc localhost:{port_vnc} --listen {port_ws}"
                
                # Enable WSS (Secure WebSockets) if certs exist
                cert_path = os.path.join(BASE_DIR, "certs/server.crt")
                key_path = os.path.join(BASE_DIR, "certs/server.key")
                if os.path.exists(cert_path) and os.path.exists(key_path):
                    cmd_ws += f" --cert '{cert_path}' --key '{key_path}'" # novnc_proxy/websockify supports this
                    
                session_log.write(f"CMD: {cmd_ws}\n")
                procs.append(spawn(cmd_ws))

            except Exception as ex:
                log(f"[{session_id}] Process launch failed: {ex}")
                session_log.write(f"ERROR: {ex}\n")
                raise ex

            # Store session info
            with sessions_lock:
                sessions[session_id] = {
                    'pids': procs,
                    'dirs': [user_profile_dir, user_download_dir],
                    'ports': [port_display_num, port_vnc, port_ws],
                    'log': session_log
                }

            # Return Info
            response = {
                'status': 'ok',
                'session_id': session_id,
                'ws_port': port_ws,
                'download_url': f"downloads/{session_id}/"
            }
            
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(response).encode())

        except Exception as e:
            log(f"Critical Start Error: {e}")
            self.send_error(500, str(e))

    def stop_session(self):
        try:
            content_len = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_len)
            data = json.loads(body)
            session_id = data.get('session_id')
            
            if session_id and session_id in sessions:
                log(f"[{session_id}] Stopping session...")
                info = sessions[session_id]
                
                # 1. Precise Kill (Process Groups)
                for p in info['pids']:
                    try: os.killpg(os.getpgid(p.pid), signal.SIGKILL)
                    except: pass
                
                # 2. Fallback Kill (just in case)
                subprocess.run(f"pkill -9 -f {session_id}", shell=True)
                
                # 3. NUCLEAR: Force Release Ports (The "Double Tap")
                # This ensures even zombies or TIME_WAIT sockets are nuked.
                # info['ports'] = [display, vnc, ws]
                try:
                    subprocess.run(f"fuser -k -n tcp {info['ports'][1]}", shell=True) # VNC
                    subprocess.run(f"fuser -k -n tcp {info['ports'][2]}", shell=True) # WS
                except: pass

                # 4. Wait for locks to release (slightly longer for OS to reap)
                time.sleep(2)
                
                # 3. Close Log
                if 'log' in info and info['log']:
                    try: info['log'].close()
                    except: pass

                # 4. Robust File Cleanup with Retry
                print(f"[{session_id}] Cleaning files...")
                for d in info['dirs']:
                    if os.path.exists(d):
                        for attempt in range(5): # Try 5 times
                            try:
                                shutil.rmtree(d) # Don't ignore errors, catch them
                                log(f"[{session_id}] Deleted {d}")
                                break
                            except Exception as e:
                                log(f"[{session_id}] Cleanup failed (Attempt {attempt+1}): {e}")
                                time.sleep(1) # Wait 1s and retry
                
                del sessions[session_id]
                
                self.send_response(200)
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(b"Stopped")
            else:
                self.send_error(404, "Session not found")
        except Exception as e:
            log(f"Stop Error: {e}")

print(f"Session Broker running on port {BROKER_PORT}")
log("Broker Started.")
# Setup base dirs
os.makedirs(DOWNLOADS_ROOT, exist_ok=True)
os.makedirs(os.path.join(BASE_DIR, "profiles"), exist_ok=True)

# Create Server
# Create Server
with socketserver.TCPServer(("", BROKER_PORT), SessionBroker) as httpd:
    # Enable SSL
    cert_path = os.path.join(BASE_DIR, "certs/server.crt")
    key_path = os.path.join(BASE_DIR, "certs/server.key")
    
    if os.path.exists(cert_path) and os.path.exists(key_path):
        log(f"Enabling SSL with {cert_path}")
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile=cert_path, keyfile=key_path)
        httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
    else:
        log("WARNING: SSL Certs not found. Running in HTTP mode.")

    httpd.serve_forever()
