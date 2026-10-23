#!/usr/bin/env python3
"""Inspect a candidate module without installing or loading it."""
import hashlib
import json
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parent
MODULE = ROOT / "artifacts/slcan.ko"


def output(*args):
    return subprocess.check_output(args, text=True)


def module_size(path):
    sections = output("readelf", "-SW", str(path))
    match = re.search(
        r"\.gnu\.linkonce\.this_module\s+PROGBITS\s+\S+\s+\S+\s+([0-9a-fA-F]+)",
        sections,
    )
    return int(match.group(1), 16) if match else None


imports = [line.split()[-1] for line in output("nm", "-u", str(MODULE)).splitlines()]
runtime_symbols = {}
for line in (ROOT / "running-kernel.kallsyms").read_text().splitlines():
    fields = line.split()
    if len(fields) >= 3:
        runtime_symbols[fields[2]] = fields[1]

# Use only modules whose on-disk srcversion matches the currently loaded one.
# Importing the same symbol provides supporting evidence of availability, but
# does not validate the ABI of this new module.
loaded_imports = {}
references = []
for name in ("aic8800_bsp", "aic8800_fdrv", "aic8800_btlpm"):
    path = Path("/lib/modules") / (name + ".ko")
    live_version = Path("/sys/module") / name / "srcversion"
    if not path.is_file() or not live_version.is_file():
        continue
    if output("modinfo", "-F", "srcversion", str(path)).strip() != live_version.read_text().strip():
        continue
    references.append({"name": name, "module_struct_bytes": module_size(path)})
    for line in output("nm", "-u", str(path)).splitlines():
        loaded_imports.setdefault(line.split()[-1], []).append(name)

symbol_rows = []
for name in imports:
    symbol_rows.append({
        "name": name,
        "visible_in_running_kallsyms": name in runtime_symbols,
        "kallsyms_type": runtime_symbols.get(name),
        "also_imported_by_matching_loaded_modules": loaded_imports.get(name, []),
    })

header = output("readelf", "-h", str(MODULE))
vermagic = output("modinfo", "-F", "vermagic", str(MODULE)).strip()
expected_vermagic = "6.1.141 SMP mod_unload aarch64"
size = module_size(MODULE)
checks = {
    "elf_aarch64": "AArch64" in header and "ELF64" in header,
    "vermagic_matches_expected": vermagic == expected_vermagic,
    "module_struct_size_matches_loaded_references": bool(references) and all(
        row["module_struct_bytes"] == size for row in references
    ),
}
report = {
    "status": "BUILD_COMPLETE_RUNTIME_UNVERIFIED",
    "module": str(MODULE),
    "sha256": hashlib.sha256(MODULE.read_bytes()).hexdigest(),
    "source": json.loads((ROOT / "source.json").read_text()),
    "vermagic": vermagic,
    "module_struct_bytes": size,
    "checks": checks,
    "loaded_reference_modules": references,
    "import_count": len(imports),
    "imports_visible_in_kallsyms": sum(row["visible_in_running_kallsyms"] for row in symbol_rows),
    "not_visible_but_imported_by_loaded_modules": [
        row["name"] for row in symbol_rows
        if not row["visible_in_running_kallsyms"]
        and row["also_imported_by_matching_loaded_modules"]
    ],
    "imports_without_runtime_evidence": [
        row["name"] for row in symbol_rows
        if not row["visible_in_running_kallsyms"]
        and not row["also_imported_by_matching_loaded_modules"]
    ],
    "imports": symbol_rows,
    "limitations": [
        "The public source is not confirmed as the exact vendor kernel source.",
        "The vendor Module.symvers is unavailable. KBUILD_MODPOST_WARN=1 was used; unresolved-symbol warnings are retained in logs/module-build.log.",
        "CONFIG_KALLSYMS_ALL is disabled, so absence of a data symbol from /proc/kallsyms does not prove it is absent from the kernel.",
        "Symbol presence, vermagic and struct module size do not prove ABI compatibility of net_device, tty_struct, sk_buff or other kernel interfaces.",
        "No module installation, module load, USB attachment, CAN frame reception or robot control test has been performed.",
    ],
}
(ROOT / "artifacts/verification.json").write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps({key: report[key] for key in (
    "status", "vermagic", "checks", "import_count", "imports_visible_in_kallsyms",
    "not_visible_but_imported_by_loaded_modules", "imports_without_runtime_evidence",
)}, indent=2))
if not all(checks.values()):
    raise SystemExit("A static compatibility check failed; inspect verification.json")
