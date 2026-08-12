# start_ngrok.py
# Exposes this app (port 8091) publicly for a demo -- a temporary stand-in until
# real deployment (see thesis future-work notes). NOT for production use: this
# app has no auth of its own in front of the tunnel.
#
# Set your own token via:
#   Windows (cmd.exe):    set NGROK_AUTHTOKEN=your_token
#   Windows (PowerShell): $env:NGROK_AUTHTOKEN = "your_token"
#   macOS/Linux:          export NGROK_AUTHTOKEN=your_token
# Get one from https://dashboard.ngrok.com/tunnels/authtokens -- never hardcode
# it in this file (see rag-legal-assistant/start_ngrok.py's own history for why:
# a hardcoded token there leaked into git history and had to be rotated).
import os
import sys
import time

import ngrok

AUTHTOKEN = os.getenv("NGROK_AUTHTOKEN")
if not AUTHTOKEN:
    print("NGROK_AUTHTOKEN environment variable is not set.", file=sys.stderr)
    print("Get a token from https://dashboard.ngrok.com/tunnels/authtokens and set it first:", file=sys.stderr)
    print("  Windows (cmd.exe):    set NGROK_AUTHTOKEN=your_token", file=sys.stderr)
    print('  Windows (PowerShell): $env:NGROK_AUTHTOKEN = "your_token"', file=sys.stderr)
    print("  macOS/Linux:          export NGROK_AUTHTOKEN=your_token", file=sys.stderr)
    sys.exit(1)

listener = ngrok.forward(8091, authtoken=AUTHTOKEN)
print(f"\n✅ Public URL: {listener.url()}\n")
print("Press Ctrl+C to stop.")

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("Stopped.")
