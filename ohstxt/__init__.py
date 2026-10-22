"""OpenHamShack Teletext: a broadcast teletext page editor (WST / ETS 300 706
Level 1) for the pages OpenHamShack ATV transmits. DESIGN.md is the contract."""
from pathlib import Path

__version__ = (Path(__file__).with_name("VERSION").read_text().strip()
               if Path(__file__).with_name("VERSION").is_file() else "0.0.0")
