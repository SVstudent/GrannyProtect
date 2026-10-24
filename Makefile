.PHONY: install dev backend frontend build test replay tunnel lan lan-cert voices e2e

install:
	cd backend && uv sync --extra mcp
	cd frontend && npm install

# Backend with the built frontend on one origin (what you demo and tunnel)
backend:
	cd backend && PYTHONUNBUFFERED=1 uv run uvicorn app.main:app --host 0.0.0.0 --port 8000

# Hot-reload frontend on :5173 (proxies /api and /ws to :8000)
frontend:
	cd frontend && npm run dev -- --host

build:
	cd frontend && npm run build

dev: build backend

test:
	cd backend && uv run pytest -q

# Print the decision trace for one script, e.g. make replay SCRIPT=scam_irs
SCRIPT ?= scam_bank
replay:
	cd backend && PYTHONUNBUFFERED=1 uv run python -m app.replay $(SCRIPT)

# HTTPS for the phones (mic access needs a secure context)
tunnel:
	cloudflared tunnel --url http://localhost:8000

# No-internet-needed audio: serve HTTPS straight to the phones on the same Wi-Fi/hotspot.
# Phones open https://<laptop-ip>:8443/granny (tap "Advanced → Proceed" once on the self-signed cert).
LAN_IP := $(shell ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || hostname -I 2>/dev/null | cut -d' ' -f1)
lan-cert:
	mkdir -p backend/.cert && openssl req -x509 -newkey rsa:2048 -nodes -days 30 -subj "/CN=granny-protect" \
	  -addext "subjectAltName=IP:$(LAN_IP),DNS:localhost,IP:127.0.0.1" \
	  -keyout backend/.cert/key.pem -out backend/.cert/cert.pem 2>/dev/null
lan: lan-cert
	@echo "\n  Granny phone:  https://$(LAN_IP):8443/granny\n  Scammer phone: https://$(LAN_IP):8443/caller\n  Panel:         https://$(LAN_IP):8443/panel\n"
	cd backend && PYTHONUNBUFFERED=1 uv run uvicorn app.main:app --host 0.0.0.0 --port 8443 \
	  --ssl-keyfile .cert/key.pem --ssl-certfile .cert/cert.pem

# Pre-generate every whisper in the natural voice (cached; the server also does this at startup)
voices:
	cd backend && PYTHONUNBUFFERED=1 uv run python -m app.tts.whispers

# Real-browser end-to-end checks against a running server (two Chrome phones, live audio)
E2E_BASE ?= http://localhost:8000
e2e:
	cd backend && PYTHONPATH=../e2e uv run --with playwright python ../e2e/audio_e2e.py $(E2E_BASE)
	cd backend && PYTHONPATH=../e2e uv run --with playwright python ../e2e/demo_flow_e2e.py $(E2E_BASE)
