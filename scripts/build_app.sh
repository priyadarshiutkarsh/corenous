#!/usr/bin/env bash
# Build Corenous.app from the source tree.
#
#   ./scripts/build_app.sh            # full release build (slow, standalone)
#   ./scripts/build_app.sh --alias    # dev build (fast, symlinks back to source)
#
# Result: ./dist/Corenous.app  — double-click to run.
#
# Important caveats:
#   * The app is NOT code-signed. macOS will refuse to open it by double-click
#     the first time; right-click → Open works, or run:
#         xattr -cr dist/Corenous.app
#   * The Gemma 3 4B GGUF model is NOT bundled (it is ~2.5 GB). The bundled
#     app will download it to ~/.corenous/models/ on first AI use, exactly
#     like the dev runtime.
#
set -euo pipefail

cd "$(dirname "$0")/.."

ALIAS_FLAG=""
if [[ "${1:-}" == "--alias" || "${1:-}" == "-A" ]]; then
    ALIAS_FLAG="-A"
    echo ">> Alias (dev) build — code edits will be picked up live."
fi

VENV_PY=".venv/bin/python"
if [[ ! -x "$VENV_PY" ]]; then
    echo "!! .venv/bin/python not found. Run:  python3 -m venv .venv && source .venv/bin/activate && pip install -e ."
    exit 1
fi

# 1. Ensure py2app is installed in the venv. py2app 0.28.x predates
#    setuptools 80 and explicitly rejects builds when ``install_requires``
#    is set on the Distribution. Pin setuptools <80 so the build path
#    works; the runtime ``pip install -e .`` does not care.
if ! "$VENV_PY" -c "import py2app" 2>/dev/null; then
    echo ">> Installing py2app + pinning setuptools <80 into the venv …"
    "$VENV_PY" -m pip install --upgrade pip wheel
    "$VENV_PY" -m pip install "py2app>=0.28" "setuptools<80"
fi
# Belt-and-suspenders: re-pin if the user upgraded setuptools later.
"$VENV_PY" -c "import setuptools, sys; sys.exit(0 if int(setuptools.__version__.split('.')[0]) < 80 else 1)" \
    || "$VENV_PY" -m pip install --quiet "setuptools<80"

# 2. Build the .icns icon if it is missing or older than the source PNG.
SRC_PNG="assets/corenous-1024.png"
ICNS="assets/Corenous.icns"
if [[ -f "$SRC_PNG" && ( ! -f "$ICNS" || "$ICNS" -ot "$SRC_PNG" ) ]]; then
    echo ">> Generating $ICNS from $SRC_PNG …"
    "$(dirname "$0")/build_icon.sh"
fi

# 3. Clean previous artefacts (Resources are stale-cached otherwise).
echo ">> Cleaning ./build and ./dist …"
rm -rf build dist

# 3. Run the build.
echo ">> Building Corenous.app — this can take 5-10 min on a clean machine."
"$VENV_PY" setup_app.py py2app $ALIAS_FLAG

APP_PATH="dist/Corenous.app"

if [[ ! -d "$APP_PATH" ]]; then
    echo "!! Build did not produce $APP_PATH. Check the log above."
    exit 1
fi

# 4. Strip any quarantine xattrs added by the OS so the unsigned bundle
#    can be launched by double-click without 'unidentified developer' modal.
xattr -cr "$APP_PATH" 2>/dev/null || true

echo
echo ">> Done. Bundle path: $APP_PATH"
echo "   Size: $(du -sh "$APP_PATH" | cut -f1)"
echo
echo "   First launch tips:"
echo "     • Right-click the app in Finder → Open (since the bundle is unsigned)."
echo "     • The menu bar gets a ● glyph. Press ⌥Space to summon the overlay."
echo "     • The background capture daemon is started automatically by the app."
echo "     • User data lives in ~/Library/Application Support/Corenous"
echo
echo "   To move it to /Applications:"
echo "       mv \"$APP_PATH\" /Applications/"
