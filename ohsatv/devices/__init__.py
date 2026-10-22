"""Radios behind one interface (DESIGN.md "Devices")."""
from __future__ import annotations

from .base import Device, DeviceError


def make_device(cfg: dict, fs: float) -> Device:
    """The configured device, not yet opened, at sample rate `fs`. Optional
    drivers are imported here, lazily, so a missing python3-libiio disables
    the Pluto with a sentence instead of stopping the program."""
    kind = cfg.get("device", "sim")
    if kind == "sim":
        from .sim import SimDevice
        return SimDevice(cfg, fs)
    if kind == "pluto":
        from .pluto import PlutoDevice
        return PlutoDevice(cfg.get("plutoUri", "usb:"), fs, cfg)
    if kind == "file":
        from .filedev import FileDevice
        return FileDevice(cfg, fs)
    raise DeviceError(f"Unknown device {kind!r}")


__all__ = ["Device", "DeviceError", "make_device"]
