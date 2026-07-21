# AI Infra Gateway — Troubleshooting Log

> **Last Updated**: 2026-06-21 | **Coverage**: Full project lifecycle, 5 critical incidents

---

## Issue Index

| ID | Priority | Issue | Root Cause | Status |
|----|----------|-------|------------|--------|
| T-001 | P0 | Ollama Registry blocked by GFW | GFW blocks `registry.ollama.ai` DNS + HTTPS SNI | ✅ Resolved |
| T-002 | P0 | Gateway 8000 port not listening after startup | CWD path issue + relative-path config loading | ✅ Resolved |
| T-003 | P1 | PowerShell terminal stdout swallowed by IDE | IDE terminal buffer issue | 🟡 Workaround |
| T-004 | P2 | requirements.txt encoding error | Windows pip defaults to GBK decoder | ✅ Resolved |
| T-005 | P1 | WSL2 / Hyper-V cannot be enabled | BIOS-level VT-x disabled (OEM firmware bug) | 🔴 Hardware limit |

---

## T-005: WSL2 & Hyper-V Unavailable — Hardware Virtualization Layer Diagnosis

### Diagnosis Time
2026-06-20 23:24

### Diagnostic Commands & Results

| Check | Command | Result |
|-------|---------|--------|
| CPU virtualization firmware | `wmic cpu get VirtualizationFirmwareEnabled` | **FALSE** |
| Second Level Address Translation (SLAT) | `wmic cpu get SecondLevelAddressTranslationExtensions` | **FALSE** |
| CPU model | `wmic cpu get Name` | i5-12450H (supports VT-x) |
| Windows version | `wmic os get Caption,Version` | Windows 11 Pro 26200 |
| Hypervisor present | `wmic path Win32_ComputerSystem get HypervisorPresent` | **TRUE** |

### Root Cause

Intel VT-x is disabled at the BIOS/UEFI firmware layer. Despite CPU hardware support (12th Gen i5):

1. `VirtualizationFirmwareEnabled=FALSE` — virtualization firmware not enabled
2. `SecondLevelAddressTranslationExtensions=FALSE` — EPT/SLAT unavailable
3. `HypervisorPresent=TRUE` yet ACPI flag is FALSE → **OEM BIOS firmware bug** on Mechrevo GM6AR0Q (AMI N.1.13MRO14)
4. Enabling Hyper-V / Virtual Machine Platform in Windows Settings fails with a loop error ("settings cannot be updated, will restore original")

### Resolution

**Only fix**: Enter BIOS Setup → Advanced → CPU Configuration → Intel Virtualization Technology → **Enabled**. Some laptop OEMs (especially consumer models for China market) hide or lock this option — if so, WSL2/Docker/Hyper-V are permanently unavailable.

### Workaround

Since vLLM requires Linux (CUDA driver, FlashInfer native libs), it cannot run on Windows bare metal. Use **sglang** (pure Python, `pip install`) as an alternative — provides Continuous Batching + Prefix Cache for GPU scheduling.

---

## T-001: Ollama Registry Blocked — Model Download Completely Stalled

### ⚠️ Lessons Learned: Blind Spot in Environment Diagnostics

**If a network connectivity check had been added to the initial environment diagnostic script, this P0 blocker would have been identified in the first minute, not after two hours of repeated attempts.**

The original diagnostic script `env_diag.py` covered:
- ✅ GPU (nvidia-smi)
- ✅ Python/pip versions
- ✅ Ollama binary path + process
- ✅ Git Bash path
- ❌ **Ollama Registry network connectivity** ← Blind spot

**Supplemental diagnostic rule**: Any service that depends on an external Registry/API must include TCP connectivity probing in the environment check:
```python
socket.connect_ex(('registry.ollama.ai', 443))
```

### Discovery Time
2026-06-20 22:10

### Diagnostic Commands & Results

| Check | Command | Result |
|-------|---------|--------|
| Registry TCP | `socket.connect_ex(('registry.ollama.ai', 443))` | ❌ errno=11001 (WSAHOST_NOT_FOUND) |
| 1.5b blob size | `dir .ollama/models/blobs/` | 986,048,512 bytes (stuck at 22:25) |
| 0.5b blob size | Same | 397,807,936 bytes (stuck at 22:39) |
| `ollama list` | — | Always empty |
| 3x delete + re-pull | — | Identical result, zero progress |

### Root Cause

`registry.ollama.ai` is blocked in mainland China by GFW — DNS resolution fails (`WSAHOST_NOT_FOUND`). DNS pollution + SNI blocking cause HTTPS handshake failure. Blob data downloads to full size but Ollama cannot retrieve the manifest from Registry for checksum verification → files permanently stuck with `-partial` suffix.

### Breakthrough: GGUF Header Inspection

```python
>>> f = open('sha256-1837...-partial', 'rb')
>>> f.read(8)
b'GGUF\x03\x00\x00\x00'
```

**The partial blob IS a complete, valid GGUF format model.** The download completed — only Registry manifest verification was blocking.

### Resolution

1. Copy partial blob → `.gguf` file
2. Write local Modelfile (`FROM ./model.gguf` + parameters)
3. `ollama create qwen2.5:1.5b -f Modelfile` → local import, **completely bypasses Registry**

```bash
# Example
copy sha256-1837...-partial qwen2.5-1.5b-q4_k_m.gguf

# Modelfile
FROM ./qwen2.5-1.5b-q4_k_m.gguf
TEMPLATE """{{ .Prompt }}"""
PARAMETER temperature 0.7
PARAMETER num_ctx 2048

# Import
ollama create qwen2.5:1.5b -f Modelfile
```

### Result

```
NAME            ID              SIZE      MODIFIED
qwen2.5:1.5b    c4c4becaaac7    986 MB    ...
qwen2.5:0.5b    f38fbb75b5b3    397 MB    ...
```

Both models successfully imported.

---

## T-002: Gateway 8000 Port Not Listening After Startup (Resolved)

### Root Cause & Fix

| Root Cause | Fix |
|------------|-----|
| `Start-Process` default CWD is not the project directory | Created `start_gateway.py` launcher, forces `os.chdir(PROJECT_DIR)` |
| `load_config("config/gateway_config.yaml")` relative path not found | Changed to `os.path.dirname(os.path.abspath(__file__))` absolute path |
| `uvicorn.run("gateway_server:app")` module not found | `sys.path.insert(0, PROJECT_DIR)` |

### Resolution Summary

1. Created `start_gateway.py` launcher: forces `os.chdir(PROJECT_DIR)` + `sys.path.insert(0, PROJECT_DIR)`
2. `load_config()` now uses absolute path: `os.path.join(os.path.dirname(os.path.abspath(__file__)), "config", "gateway_config.yaml")`
3. Launch with full Python executable path: `C:\Users\admin\AppData\Local\Programs\Python\Python311\python.exe`

---

## T-003: PowerShell Terminal stdout Swallowed (Ongoing)

### Workaround Summary

- `cmd /c "command > file.txt"` → `read_file(file.txt)`
- Use `curl.exe` full path (avoids PowerShell alias: curl → Invoke-WebRequest)
- `cmd /c` with `&` separator is specially handled by PowerShell → split into multiple `cmd /c` calls when using `&&`
- `for /L %i` not supported in PowerShell → use Python one-liner instead

---

## T-004: requirements.txt Encoding (Resolved)

Removed all Chinese comments, migrated to pure ASCII. This is a known issue with Windows pip defaulting to the GBK decoder.

---

## Environment Baseline — Final State (2026-06-21)

| Item | Value | Notes |
|------|-------|-------|
| GPU | RTX 4060 Laptop, 8188 MiB, CC 8.9 | Normal |
| Driver | 596.36 | Normal |
| CPU | Intel i5-12450H (8 cores, 12th Gen) | Normal |
| Python | 3.11.9 @ Python311 | Normal |
| Ollama | 0.30.9, port 11434 LISTENING | Normal |
| Gateway | port 8000 LISTENING | Normal |
| Dashboard v2 | port 9090 LISTENING | Normal |
| Installed Models | qwen2.5:1.5b (986 MB), qwen2.5:0.5b (397 MB) | ✅ Resolved (T-001) |
| WSL2 | Unavailable | 🔴 T-005 |
| Virtualization | VT-x disabled in BIOS | 🔴 T-005 |

---

*Every log entry represents a real pitfall encountered — the project's most valuable operational knowledge.*