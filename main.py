"""Punkt wejścia skryptu dla ScriptPlatformFB."""
import traceback

import spfb

from otomoto import watcher

ctx = spfb.connect()
ctx.install_logging()

try:
    watcher.run(ctx)
except ValueError as exc:
    ctx.error(str(exc))
    raise SystemExit(2)
except Exception as exc:
    ctx.warn(traceback.format_exc())
    ctx.error(f'{type(exc).__name__}: {str(exc).splitlines()[0][:300] if str(exc) else ""}')
    raise SystemExit(1)
