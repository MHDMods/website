import requests
import time
import json
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BROKER_URL = "https://localhost:6081"
ITERATIONS = 20

print(f"--- STARTING STRESS TEST ({ITERATIONS} Cycles) ---")
print("Goal: Verify ports are allocated and then FREED for reuse.")

for i in range(1, ITERATIONS + 1):
    try:
        # 1. Start Session
        print(f"\n[Cycle {i}] Requesting Session...")
        start_t = time.time()
        res = requests.post(f"{BROKER_URL}/session/start", json={"width": 800, "height": 600}, timeout=10, verify=False)
        
        if res.status_code == 200:
            data = res.json()
            sid = data['session_id']
            ws_port = data['ws_port']
            vnc_port = ws_port - 1 # Assumption based on logic
            print(f"  -> GRANTED: Session {sid} | Ports: VNC={vnc_port}, WS={ws_port}")
            
            # 2. Short Sleep (Simulate usage)
            time.sleep(1)
            
            # 3. Stop Session
            print(f"  -> STOPPING Session {sid}...")
            stop_res = requests.post(f"{BROKER_URL}/session/stop", json={"session_id": sid}, timeout=5, verify=False)
            
            if stop_res.status_code == 200:
                print("  -> STOPPED. (Ports should be free now)")
            else:
                print(f"  -> STOP FAILED: {stop_res.status_code} {stop_res.text}")
                
        else:
            print(f"  -> FAILED TO START: {res.status_code} {res.text}")
            # If server is full, we stop
            if res.status_code == 503:
                print("!!! SERVER FULL DETECTED - TEST FAILED !!!")
                break

    except Exception as e:
        print(f"  -> ERROR: {e}")
        
    time.sleep(1) # Give OS a moment to reap sockets

print("\n--- TEST COMPLETE ---")
