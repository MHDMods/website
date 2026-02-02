import http.server
import socketserver
import os
import subprocess
import time

PORT = 6081
DOWNLOADS_DIR = "downloads"

class SessionHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/reset':
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b"Session Reset Initiated")
            
            print("Reset signal received!")
            self.reset_session()
        else:
            # serve downloads or other files if needed, but mainly for API
            super().do_GET()

    def reset_session(self):
        # 1. Kill Firefox
        print("Killing Firefox...")
        os.system("pkill firefox")
        os.system("pkill firefox-bin")
        
        # 2. Clear Downloads
        print("Clearing downloads...")
        os.system(f"rm -rf {DOWNLOADS_DIR}/*")
        
        # 3. Restart Firefox (give it a moment to die)
        time.sleep(1)
        print("Restarting Firefox...")
        # We re-use the profile defined in the start script env
        # But since this is python, we just run the command
        # Note: We rely on the DISPLAY env var being inherited or set
        cmd = "export DISPLAY=:99; export MOZ_FAKE_NO_SANDBOX=1; firefox --display=:99 --profile ./firefox_profile --new-window https://google.com &"
        subprocess.Popen(cmd, shell=True)

print(f"Session Manager listening on port {PORT}")
with socketserver.TCPServer(("", PORT), SessionHandler) as httpd:
    httpd.serve_forever()
