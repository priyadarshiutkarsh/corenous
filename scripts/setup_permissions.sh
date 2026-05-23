#!/usr/bin/env bash
# Corenous — macOS permission setup helper
set -euo pipefail

PYTHON=$(command -v python3 2>/dev/null || echo "python3")
if [ -f ".venv/bin/python" ]; then
    PYTHON="$(pwd)/.venv/bin/python"
fi

echo ""
echo "=== Corenous Permission Setup ==="
echo ""
echo "Python binary that needs Accessibility permission:"
echo "  $PYTHON"
echo ""
echo "Steps:"
echo "  1. Open: System Settings → Privacy & Security → Accessibility"
echo "  2. Click '+' and add the Python binary above"
echo "  3. You may need to quit and reopen your terminal first"
echo ""

# Attempt to trigger the system prompt
"$PYTHON" -c "
try:
    from ApplicationServices import AXIsProcessTrustedWithOptions
    from Foundation import NSDictionary
    opts = NSDictionary.dictionaryWithObject_forKey_(True, 'AXTrustedCheckOptionPrompt')
    trusted = AXIsProcessTrustedWithOptions(opts)
    print('Accessibility permission:', 'GRANTED' if trusted else 'NOT GRANTED (see instructions above)')
except ImportError:
    print('pyobjc not installed yet. Run: pip install pyobjc-framework-ApplicationServices')
"

echo ""
echo "To reset a stuck permission (run as sudo if needed):"
echo "  sudo tccutil reset Accessibility"
echo ""
