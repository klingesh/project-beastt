#!/usr/bin/env python3
"""Entry point for BEASTT.

Usage:
    python main.py               # text chat
    python main.py --voice       # voice chat (needs the voice extras)
    python main.py --model qwen3 # use a different local model
"""

from beastt.cli import run

if __name__ == "__main__":
    run()
