"""
Single entry point for the py2app-packaged ``Corenous.app``.

A bundled macOS app is one binary. We dispatch on argv:
  * ``Corenous``                — menu bar + overlay (the user's default)
  * ``Corenous --daemon``       — capture daemon (spawned by the menu bar)
  * ``Corenous --cli <args>...`` — the existing ``corenous-ai`` Click CLI

This module is the value of ``OPTIONS['py2app']['plist']['CFBundleExecutable']``
*and* of the ``app=[...]`` setup() arg. Keep it tiny so import time is fast —
heavy work is deferred to the dispatch target."""
from __future__ import annotations

import sys


def main() -> int:
    # py2app's ``__boot__.py`` runs this file with ``exec``, which means
    # ``__package__`` is unset and relative imports fail. Use absolute
    # imports throughout — at runtime the ``src`` package is on sys.path
    # because we put it under ``Resources/lib/python3.X/`` via py2app.
    argv = list(sys.argv[1:])

    # Strip macOS's auto-injected ``-psn_…`` argument from Finder launches.
    argv = [a for a in argv if not a.startswith("-psn_")]

    if argv and argv[0] == "--daemon":
        from src.monitor.daemon import main as daemon_main
        sys.argv = [sys.argv[0]] + argv[1:]
        return daemon_main(standalone_mode=False) or 0

    if argv and argv[0] == "--cli":
        from src.cli.main import cli as cli_main
        sys.argv = [sys.argv[0]] + argv[1:]
        return cli_main(standalone_mode=False) or 0

    from src.paths import default_data_dir, default_config_path
    from src.app.main import launch

    launch(default_data_dir(), default_config_path())
    return 0


if __name__ == "__main__":
    sys.exit(main())
