#!/usr/bin/env bash
# =============================================================================
# Azure App Service (Linux) — REQUIRED portal configuration or this file is ignored:
#
#   Configuration → General settings:
#     • Web sockets = On
#     • Startup Command =  bash startup.sh
#
# If "App Command Line not configured" appears in Log stream, Oryx will run
# Gunicorn + config.wsgi → WebSocket paths return 404. Redis does not fix that;
# you must run an ASGI server (this script uses Daphne).
#
# Optional: use Azure Cache for Redis only if you configure CHANNEL_LAYERS to
# Redis and run multiple instances; InMemoryChannelLayer works on a single VM.
# Do not apt-get install redis in App Service — use the managed service instead.
# =============================================================================
set -euo pipefail

if [ -d /home/site/wwwroot ]; then
  cd /home/site/wwwroot
fi

# Azure sets PORT; some stacks use WEBSITES_PORT
PORT="${WEBSITES_PORT:-${PORT:-8000}}"

echo "*** instruction_presenter: starting Daphne ASGI on 0.0.0.0:${PORT} ***"
exec python -m daphne -b 0.0.0.0 -p "$PORT" config.asgi:application
