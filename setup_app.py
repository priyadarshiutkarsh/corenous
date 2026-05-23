"""
py2app build configuration for Corenous.app.

Usage:
    pip install py2app
    python setup_app.py py2app            # release build (~slow, full bundling)
    python setup_app.py py2app -A         # alias build (dev — links to source)

The alias build is the right way to iterate: ``-A`` symlinks the bundle's
``Resources`` into your source tree so code edits show up on the next launch
without re-running setup. Use the full build for shipping.
"""
from __future__ import annotations

import os
from pathlib import Path

import atexit
import shutil

REPO = Path(__file__).resolve().parent

# py2app's ``build_app`` refuses to run when ``install_requires`` is set on
# the Distribution. Modern setuptools auto-populates it from
# ``pyproject.toml``'s ``[project].dependencies`` table the moment any
# ``setup()`` call sees the pyproject sitting next to it. The cleanest
# workaround that does not require touching the runtime project metadata
# is to hide pyproject.toml for the duration of the build — setuptools
# falls back to the values we pass directly to ``setup()`` (which omit
# install_requires entirely).
_pyproj = REPO / "pyproject.toml"
_pyproj_bak = REPO / "pyproject.toml.bundle-build.bak"
if _pyproj.exists() and not _pyproj_bak.exists():
    shutil.move(str(_pyproj), str(_pyproj_bak))


def _restore_pyproject() -> None:
    if _pyproj_bak.exists() and not _pyproj.exists():
        shutil.move(str(_pyproj_bak), str(_pyproj))


atexit.register(_restore_pyproject)

from setuptools import setup  # noqa: E402

APP = [str(REPO / "src" / "bundle_entry.py")]

# Pull settings.yaml and any other static data into Resources/.
DATA_FILES: list[tuple[str, list[str]]] = [
    ("config", [str(REPO / "config" / "settings.yaml")]),
]

# Modules py2app's static analysis misses. We force them in so the bundle
# does not crash on first-use of an indirectly-imported dep.
INCLUDES = [
    # Click submodules
    "click", "click._compat", "click._termui_impl",
    # PyObjC core (most frameworks are imported lazily by our code)
    "objc", "AppKit", "Foundation", "Vision", "Quartz", "CoreFoundation",
    "PyObjCTools.AppHelper",
    # YAML
    "yaml",
    # Crypto
    "cryptography", "cryptography.hazmat",
    # Numerical
    "numpy",
    # LLM runtime — llama-cpp-python ships a compiled extension + Metal shaders
    "llama_cpp",
    # Embedder
    "sentence_transformers", "transformers", "tokenizers", "huggingface_hub",
    # CLI surface
    "src.cli.main", "src.cli.daemon_ctl", "src.cli.query", "src.cli.vault_cli",
    # Daemon path (so --daemon dispatch resolves)
    "src.monitor.daemon", "src.app.main", "src.app.app_controller",
]

# torch carries CUDA/CPU vendor folders we never use; trim them aggressively
# but be conservative — sentence-transformers needs torch.nn.functional + a
# couple of torch.cuda stubs even on Apple Silicon.
EXCLUDES = [
    "tkinter", "PyQt5", "PyQt6", "PySide2", "PySide6",
    "matplotlib", "scipy.io.matlab", "IPython", "jupyter",
    "wx", "test", "tests", "torch.test", "torchvision",
]

# Files & directories that py2app should drop into the bundle's Resources.
# We deliberately do NOT bundle the Gemma GGUF — it's ~2.5 GB and lives in
# ~/.corenous/models which the runtime downloads on first launch.
RESOURCES: list[str] = []

PLIST = {
    "CFBundleName":               "Corenous",
    "CFBundleDisplayName":        "Corenous AI",
    "CFBundleIdentifier":         "com.corenous.menubar",
    "CFBundleShortVersionString": "0.1.0",
    "CFBundleVersion":            "0.1.0",
    "LSMinimumSystemVersion":     "12.0",
    # Regular Dock app — visible in Dock, Cmd-Tab, Force-Quit list.
    # The menu bar status item still lights up; macOS happily lets a
    # ``Regular`` app keep an NSStatusItem alongside its Dock entry.
    "LSUIElement":                False,
    # User-facing purpose strings for the privacy prompts macOS shows.
    "NSAppleEventsUsageDescription": (
        "Corenous reads the active browser tab title and URL to capture what "
        "you were looking at, so you can find it later."
    ),
    "NSAccessibilityUsageDescription": (
        "Corenous watches the focused window's title so it can label each "
        "captured moment with the right app and topic."
    ),
    # Screen capture is for the on-device OCR that turns visible text into
    # searchable memory. Nothing leaves the machine.
    "NSScreenCaptureUsageDescription": (
        "Corenous takes occasional screenshots so its on-device OCR can index "
        "what you saw. Captures stay local; sensitive text is encrypted."
    ),
    # Tell the App Sandbox we are not in it (we need raw window queries).
    "NSHighResolutionCapable":    True,
}

ICON_PATH = REPO / "assets" / "Corenous.icns"

OPTIONS = {
    "argv_emulation":  False,  # we dispatch on argv ourselves
    "iconfile":        str(ICON_PATH) if ICON_PATH.exists() else None,
    "plist":           PLIST,
    "packages": [
        "src",
        "click",
        "yaml",
        "numpy",
        "cryptography",
        # NOTE: leaving `torch` + `sentence_transformers` out of `packages`
        # lets py2app's *recipes* trim them. Listing them in `includes`
        # is enough to force their import-time entry points.
    ],
    "includes":        INCLUDES,
    "excludes":        EXCLUDES,
    "resources":       RESOURCES,
    "frameworks":      [],
    "site_packages":   True,
    "strip":           False,   # llama_cpp's dylibs break if stripped
    "optimize":        0,       # keep docstrings — Click introspects them
    "semi_standalone": False,   # ship a fully standalone Python
}

setup(
    app=APP,
    name="Corenous",
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app>=0.28"],
)
