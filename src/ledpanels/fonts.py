"""Font lookup: OpenCV Hershey fonts plus TrueType files from a user folder and system folders."""

from pathlib import Path

import cv2

HERSHEY = {
    "hershey-simplex": cv2.FONT_HERSHEY_SIMPLEX,
    "hershey-duplex": cv2.FONT_HERSHEY_DUPLEX,
    "hershey-complex": cv2.FONT_HERSHEY_COMPLEX,
    "hershey-triplex": cv2.FONT_HERSHEY_TRIPLEX,
    "hershey-script": cv2.FONT_HERSHEY_SCRIPT_COMPLEX,
}
DEFAULT = "hershey-duplex"
_SYSTEM_DIRS = [Path("/usr/share/fonts"), Path.home() / ".local/share/fonts"]
_extra_dir: Path | None = None
_cache: dict[str, Path] | None = None


def set_extra_dir(path) -> None:
    global _extra_dir, _cache
    _extra_dir, _cache = Path(path), None


def truetype_fonts() -> dict[str, Path]:
    """Map file name to path; the user folder wins over system fonts."""
    global _cache
    if _cache is None:
        found: dict[str, Path] = {}
        for base in ([_extra_dir] if _extra_dir else []) + _SYSTEM_DIRS:
            if base.is_dir():
                for p in sorted(base.rglob("*")):
                    if p.suffix.lower() in (".ttf", ".otf") and p.name not in found:
                        found[p.name] = p
        _cache = found
    return _cache


def names() -> list[str]:
    return list(HERSHEY) + sorted(truetype_fonts())


def is_valid(name: str) -> bool:
    return name in HERSHEY or name in truetype_fonts()
