"""
dashboard/start.py — OpenClaw HQ launcher (LAN URL + QR code).

Detects this machine's LAN IP, prints a phone-scannable QR code in the terminal,
saves it to dashboard/static/qr.png, then serves the FastAPI dashboard on
0.0.0.0:8000 so any device on the same Wi-Fi can reach it.

Run:
    .venv\\Scripts\\python.exe dashboard\\start.py
"""
from __future__ import annotations

import os
import socket
import sys
from pathlib import Path

# Make Unicode (QR blocks) safe on Windows cp1252 consoles.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = Path(__file__).resolve().parent / "static"
PORT = int(os.getenv("PORT", "8000"))

# Ensure the repo root is importable so `dashboard.api.server` resolves when this
# script is launched directly (python dashboard/start.py).
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def detect_lan_ip() -> str:
    """Return the machine's primary LAN IPv4 (e.g. 192.168.x.x), not 127.0.0.1.

    Uses a UDP socket to a public address to discover the default-route
    interface IP. No packets are actually sent (UDP connect is local)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except OSError:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


def make_qr(url: str) -> None:
    """Print an ASCII QR to the terminal and save dashboard/static/qr.png."""
    import qrcode

    qr = qrcode.QRCode(border=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(url)
    qr.make(fit=True)

    # Terminal QR (block characters; readable by a phone camera).
    qr.print_ascii(invert=True)

    # Save PNG for the dashboard "Scan to share" card.
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    img = qr.make_image(fill_color="black", back_color="white")
    img.save(STATIC_DIR / "qr.png")


def main() -> None:
    ip = detect_lan_ip()
    url = f"http://{ip}:{PORT}"

    bar = "=" * 52
    print("\n" + bar)
    print("  OpenClaw HQ — dashboard launcher")
    print(bar)
    print(f"  LAN URL : {url}")
    print(f"  Local   : http://127.0.0.1:{PORT}")
    print(bar + "\n")

    try:
        make_qr(url)
        print(f"\n  QR saved -> {STATIC_DIR / 'qr.png'}")
    except Exception as exc:  # QR is a convenience, never block serving on it
        print(f"  (QR generation skipped: {exc})")

    print(f"\n  Scan the QR above or open {url} on your phone.")
    print("  Press Ctrl+C to stop.\n" + bar + "\n")

    import uvicorn

    uvicorn.run("dashboard.api.server:app", host="0.0.0.0", port=PORT, log_level="info")


if __name__ == "__main__":
    main()
