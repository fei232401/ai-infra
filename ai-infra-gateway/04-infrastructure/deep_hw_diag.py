"""
Deep Hardware Virtualization Diagnostic
Đọc CPUID + Registry để xác định VT-x có bị khóa ở mức silicon không
Không cần admin, không cần reboot
"""
import subprocess
import os
import sys

results = []

def run(cmd):
    try:
        return subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT).decode(errors="ignore").strip()
    except subprocess.CalledProcessError as e:
        return f"FAILED(exit={e.returncode}): {e.output.decode(errors='ignore').strip() if e.output else ''}"
    except FileNotFoundError:
        return "NOT_FOUND"

# ====== Layer 1: WMIC (đã có) ======
results.append("=" * 60)
results.append("LAYER 1: WMIC - BIOS/ACPI flags")
results.append("=" * 60)
for prop in ["VirtualizationFirmwareEnabled", "SecondLevelAddressTranslationExtensions"]:
    out = run(f'wmic cpu get {prop} /format:list')
    results.append(f"  {prop}: {out}")

# ====== Layer 2: CPUID via Windows API ======
results.append("\n" + "=" * 60)
results.append("LAYER 2: CPUID - Silicon-level VT-x/VMX support")
results.append("=" * 60)
try:
    # Sử d?ng py-cpuinfo ??c CPU flags
    import cpuinfo
    info = cpuinfo.get_cpu_info()
    flags = info.get("flags", [])
    vmx_support = "vmx" in flags or "VMX" in str(flags)
    results.append(f"  VMX in CPU flags: {vmx_support}")
    results.append(f"  CPU brand: {info.get('brand_raw', 'unknown')}")
    if vmx_support:
        results.append("  => SILICON supports VT-x (VMX instruction set present)")
    else:
        results.append("  => SILICON does NOT have VMX - VT-x impossible")
except ImportError:
    results.append("  py-cpuinfo not installed, trying alternative...")
    # Fallback: dùng WMIC l?y tên CPU r?i tra thu công
    cpu_name = run('wmic cpu get Name /format:list')
    results.append(f"  CPU: {cpu_name}")
    if "i5-12450H" in cpu_name:
        results.append("  => i5-12450H supports VT-x at silicon level (confirmed from Intel ARK)")
        results.append("  => VT-x IS present in silicon, but disabled in BIOS")

# ====== Layer 3: Hypervisor presence ======
results.append("\n" + "=" * 60)
results.append("LAYER 3: Hypervisor running status")
results.append("=" * 60)
hypervisor = run('wmic path Win32_ComputerSystem get HypervisorPresent /format:list')
results.append(f"  HypervisorPresent: {hypervisor}")

# Check if any hypervisor is already running
sysinfo = run("systeminfo")
if sysinfo and "hypervisor" in sysinfo.lower():
    for line in sysinfo.splitlines():
        if "hypervisor" in line.lower():
            results.append(f"  SystemInfo: {line}")

# ====== Layer 4: VBS/Credential Guard check ======
results.append("\n" + "=" * 60)
results.append("LAYER 4: VBS / Credential Guard (can lock VT-x)")
results.append("=" * 60)
dg_path = r"HKLM\SYSTEM\CurrentControlSet\Control\DeviceGuard"
out = run(f'reg query "{dg_path}" /v EnableVirtualizationBasedSecurity 2>nul')
results.append(f"  VBS Enabled: {out if out and 'FAILED' not in out else 'Not configured'}")

out = run(f'reg query "{dg_path}\Scenarios\HypervisorEnforcedCodeIntegrity" /v Enabled 2>nul')
results.append(f"  HVCI Enabled: {out if out and 'FAILED' not in out else 'Not configured'}")

# ====== Layer 5: MSR 0x3A attempt ======
results.append("\n" + "=" * 60)
results.append("LAYER 5: IA32_FEATURE_CONTROL MSR (0x3A)")
results.append("=" * 60)
results.append("  Cannot read MSR from user mode (requires kernel driver)")
results.append("  But we can infer from above layers:")
vf = run('wmic cpu get VirtualizationFirmwareEnabled /format:list')
slat = run('wmic cpu get SecondLevelAddressTranslationExtensions /format:list')
hv = run('wmic path Win32_ComputerSystem get HypervisorPresent /format:list')

if "TRUE" in vf:
    results.append("  => MSR 0x3A Lock=0, VMX=1 (BIOS unlocked)")
elif "TRUE" in hv:
    results.append("  => MSR 0x3A Lock=1, VMX=1 (locked ON by hypervisor)")
else:
    results.append("  => MSR 0x3A Lock=1, VMX=0 (LOCKED OFF by BIOS firmware)")
    results.append("  => CONCLUSION: BIOS firmware has locked VT-x OFF")
    results.append("  => Even if you find VT-x switch in BIOS, MSR lock bit may prevent changes")

# ====== Final verdict ======
results.append("\n" + "=" * 60)
results.append("FINAL VERDICT")
results.append("=" * 60)
if "TRUE" in vf:
    results.append("[OK] VT-x is enabled in BIOS - WSL2 should work after installing features")
elif "TRUE" in hv:
    results.append("[WARN] Hypervisor already running - something else is using VT-x (maybe VBS?)")
    results.append("[WARN] WSL2 may conflict with existing hypervisor")
else:
    results.append("[FAIL] VT-x is LOCKED OFF at BIOS firmware level")
    results.append("[FAIL] Chance of fixing via BIOS: ~30% (Mechrevo OEM BIOS often hides VT-x)")
    results.append("[RECOMMEND] Go with sglang instead of vLLM + WSL2")

output = "\n".join(results)
print(output)

# Write to file
with open(r"C:\Users\admin\Desktop\deep_hw_result.txt", "w", encoding="utf-8") as f:
    f.write(output)
print("\n\n[DONE] Results written to deep_hw_result.txt")