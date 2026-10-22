"""The store form's server (`python3 -m ohsatv --server`).

BETA-PLAN D8, "API first for Qt apps": the engine runs headless in a
container on an OpenHamShack controller, and its API - the engine's own
(docs/ENGINE-API.md) over HTTP and WebSocket, with the transmit lease of
REMOTE-API section 7 - is what OpenHamShack Remote shows. The desktop
window never imports this package: no servers inside the desktop apps
(GUIDE section 1), kept by process (REPO-LAYOUT 4.1), and a test says so.
docs/SERVER-API.md is the wire contract.
"""
