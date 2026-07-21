# 04 — Infrastructure & Environment Diagnostics

Environment baseline checks, virtualization diagnostics, WSL2 enablement, model import, and infrastructure tools.

## Files

| File | Purpose |
|------|---------|
| `01_check_env.py` | Environment baseline check (GPU, Python, pip, Ollama) |
| `deep_hw_diag.py` | 5-layer hardware virtualization deep diagnostic (WMIC → CPUID → Registry → VBS → MSR) |
| `enable_wsl2.ps1` | WSL2 enable script (requires Admin PowerShell) |
| `troubleshoot_diag.py` | T-001/T-002 joint diagnostic script |
| `import_models.py` | ModelScope GGUF download + Ollama local import |
| `01_ollama_start.sh` | Ollama one-click launch (Git Bash) |
| `metrics_exporter.py` | Prometheus Exporter — GPU + Ollama metrics |
| `setup_monitoring.py` | Local monitoring stack deployment (Prometheus + Grafana, fallback) |
| `install_grafana.py` | Grafana native Windows installation |
| `sglang_server.py` | sglang inference server launch (fallback / incomplete) |
| `alerting_rules.yml` | Prometheus alert rules (GPU temperature, circuit breaker, rate limiting) |

## Key Diagnostic Findings

- ✅ `HypervisorPresent=TRUE` — VT-x is actually running at the silicon level
- ❌ `VirtualizationFirmwareEnabled=FALSE` — OEM BIOS ACPI flag bug (Mechrevo GM6AR0Q, AMI N.1.13MRO14)
- ❌ `registry.ollama.ai:443` — GFW block (WSAHOST_NOT_FOUND, DNS + SNI)

## Design Decisions

### Why 5-Layer Virtualization Diagnostic?

A simple "is WSL2 working? → no → enable in BIOS" approach fails when the failure has multiple possible causes. The 5-layer approach systematically rules out each layer:

| Layer | Check | Confirms |
|-------|-------|----------|
| 1. ACPI (WMIC) | `VirtualizationFirmwareEnabled` | BIOS has set the flag |
| 2. Silicon (CPUID) | Intel ARK lookup | CPU hardware supports VT-x/VMX |
| 3. Hypervisor | `HypervisorPresent` | A hypervisor is currently running |
| 4. VBS/HVCI | Registry `DeviceGuard` | Windows security virtualization layer state |
| 5. MSR 0x3A | Inferred | VT-x lock bit + VMX enable bit at register level |

The key insight for this project: Layer 1 says FALSE but Layer 3 says TRUE — the OEM BIOS incorrectly set the ACPI flag, even though VT-x is operational at the hardware level. This is a firmware bug, not a simple "VT-x not enabled" scenario.

### Why the Model Import Tool Exists

`import_models.py` addresses T-001's root cause directly: GFW blocks Ollama Registry, so the tool downloads GGUF files from ModelScope (alternative CDN) and imports them locally via `ollama create`. The tool encapsulates: HTTP download with retry, GGUF file header validation, Modelfile generation, and `ollama create` invocation. This is infrastructure-as-code for the model provisioning step.

### Why Prometheus Exporter is "Fallback"

The primary monitoring solution is the pynvml-based dashboard (02-dashboard). The Prometheus exporter (`metrics_exporter.py`) and Grafana installer (`install_grafana.py`) are provided as optional additions for teams that want to integrate with existing observability stacks. They are not required for the core monitoring functionality.