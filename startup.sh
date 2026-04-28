#!/usr/bin/env bash
# Azure App Service: set Startup Command to "bash startup.sh" (or this file's path).
# Also enable Configuration → General settings → Web sockets = On.
set -euo pipefail
PORT="${PORT:-8000}"
exec python -m daphne -b 0.0.0.0 -p "$PORT" config.asgi:application
