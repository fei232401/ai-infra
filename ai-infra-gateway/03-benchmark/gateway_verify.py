"""Gateway full verification script"""
import urllib.request
import urllib.error
import subprocess
import json
import os
import sys

API_KEY = "sk-infra-gateway-dev-key-2026"
GATEWAY = "http://localhost:8000"
ORANGE = "\033[33m"
GREEN = "\033[32m"
RED = "\033[31m"
RESET = "\033[0m"

def test(name, url, headers=None, expect_status=200):
    try:
        req = urllib.request.Request(url, headers=headers or {})
        resp = urllib.request.urlopen(req, timeout=10)
        body = resp.read().decode()[:200]
        status = resp.status
        if status == expect_status:
            print(f"  {GREEN}PASS{RESET} {name}: HTTP {status} -> {body}")
            return True
        else:
            print(f"  {RED}FAIL{RESET} {name}: expected {expect_status}, got {status} -> {body}")
            return False
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:200]
        if e.code == expect_status:
            print(f"  {GREEN}PASS{RESET} {name}: HTTP {e.code} -> {body}")
            return True
        else:
            print(f"  {RED}FAIL{RESET} {name}: expected {expect_status}, got {e.code} -> {body}")
            return False
    except Exception as e:
        print(f"  {RED}FAIL{RESET} {name}: {e}")
        return False

print("=" * 60)
print("  Gateway Verification Suite")
print("=" * 60)

# 1. Health check
print("\n[1] Health Check:")
test("health", f"{GATEWAY}/health")

# 2. Auth: no key -> 401
print("\n[2] Auth: No API Key -> Expect 401:")
test("no-key", f"{GATEWAY}/api/models", expect_status=401)

# 3. Auth: wrong key -> 401
print("\n[3] Auth: Wrong API Key -> Expect 401:")
test("wrong-key", f"{GATEWAY}/api/models",
     headers={"Authorization": "Bearer wrong-key-123"},
     expect_status=401)

# 4. Auth: correct key -> forward to Ollama
print("\n[4] Auth: Correct Key -> Forward to Ollama:")
test("correct-key", f"{GATEWAY}/api/models",
     headers={"Authorization": f"Bearer {API_KEY}"})

# 5. Rate limiting: rapid requests -> 429
print("\n[5] Rate Limit: 12 rapid requests -> Expect 429:")
for i in range(12):
    try:
        req = urllib.request.Request(f"{GATEWAY}/api/models",
            headers={"Authorization": f"Bearer {API_KEY}"})
        resp = urllib.request.urlopen(req, timeout=5)
        print(f"   Request {i+1}: HTTP {resp.status}")
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:100]
        print(f"   Request {i+1}: HTTP {e.code} -> {body}")
        if e.code == 429:
            print(f"  {GREEN}PASS{RESET}: Rate limit triggered at request {i+1}")
            break

# 6. Model download status
print("\n[6] Model Download Status:")
r = subprocess.run([
    r"C:\Users\admin\AppData\Local\Programs\Ollama\ollama.exe", "list"
], capture_output=True, text=True)
print(f"  {r.stdout.strip()}")

# 7. GPU
print("\n[7] GPU Memory:")
r = subprocess.run(
    "nvidia-smi --query-gpu=memory.used,memory.free,temperature.gpu --format=csv,noheader",
    shell=True, capture_output=True, text=True
)
print(f"  {r.stdout.strip()}")

# 8. Blob files
print("\n[8] Blob Download Progress:")
blob_dir = r"C:\Users\admin\.ollama\models\blobs"
for f in sorted(os.listdir(blob_dir)):
    if "-partial" in f and not any(f.endswith(f"-{i}") for i in range(10)):
        fpath = os.path.join(blob_dir, f)
        size_mb = os.path.getsize(fpath) / (1024*1024)
        mtime = os.path.getmtime(fpath)
        import time
        print(f"  {f.split('-')[1][:12]}...  {size_mb:.1f} MB  mtime={time.ctime(mtime)}")

print("\n" + "=" * 60)
print("  Verification Complete")
print("=" * 60)