#!/usr/bin/env python3
"""
main.py – Entry point for the PDF Form Filler web application.
"""

import webbrowser
from app import app


def main() -> None:
    port = 5000
    print(f"\n  📄 PDF Form Filler")
    print(f"  ──────────────────────────────")
    print(f"  Open in browser: http://127.0.0.1:{port}")
    print(f"  Press Ctrl+C to stop\n")

    webbrowser.open(f"http://127.0.0.1:{port}")
    app.run(host="127.0.0.1", port=port, debug=False)


if __name__ == "__main__":
    main()
