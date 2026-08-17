import sys


print("booting fixture", flush=True)
print(
    "[    0.100000] Memory: 900000K/1000000K available "
    "(100K kernel code, 20K rwdata, 30K rodata, 10K init, "
    "40K bss, 100000K reserved, 0K cma-reserved)",
    flush=True,
)
print(
    "LINUX_MODULARIZER_METRICS uptime_seconds=1.50 "
    "mem_available_kb=900000 mem_free_kb=800000 "
    "slab_kb=12000 sreclaimable_kb=7000 sunreclaim_kb=5000",
    flush=True,
)
print("LINUX_MODULARIZER_READY", flush=True)
for line in sys.stdin:
    command = line.strip()
    if command == "trigger":
        print("RESULT_OK", flush=True)
    elif command == "check-module":
        print("AUTOLOAD_OK", flush=True)
    elif command == "poweroff -f":
        print("POWERDOWN", flush=True)
        raise SystemExit(0)
    else:
        print(f"UNKNOWN:{command}", flush=True)
