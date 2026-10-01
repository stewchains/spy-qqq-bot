"""Shows which key NAMES are in .env (never prints the keys themselves)."""
from pathlib import Path

p = Path(__file__).parent / ".env"
print(f".env found: {p.exists()}  ({p})")
for other in Path(__file__).parent.glob(".env*"):
    if other.name != ".env":
        print(f"  note: also found {other.name}")
if p.exists():
    for n, raw in enumerate(p.read_text(encoding="utf-8-sig", errors="replace").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            print(f"  line {n}: NO '=' sign -> not readable")
            continue
        name, val = line.split("=", 1)
        val = val.strip().strip('"').strip("'")
        state = "EMPTY" if not val else ("placeholder" if val.startswith("your_") else f"set ({len(val)} chars)")
        print(f"  line {n}: {name!r:32} {state}")
input("\nPress Enter to close...")
