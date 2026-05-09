#!/bin/bash
# Persistent SSH tunnel: w530 → Mac (100.96.92.65) → Confluence (10.179.104.112:8090)
# Local port 18090 → Confluence:8090

MAC_HOST="100.96.92.65"
MAC_USER="tuanho"
LOCAL_PORT="18090"
REMOTE_HOST="10.179.104.112"
REMOTE_PORT="8090"
LOG="$HOME/twins/confluence/tunnel.log"

mkdir -p "$(dirname "$LOG")"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

log "=== Confluence tunnel started (PID $$) ==="
log "    $LOCAL_PORT -> $MAC_HOST -> $REMOTE_HOST:$REMOTE_PORT"

while true; do
    ssh -N \
        -o ServerAliveInterval=30 \
        -o ServerAliveCountMax=3 \
        -o ExitOnForwardFailure=yes \
        -o StrictHostKeyChecking=no \
        -o ConnectTimeout=15 \
        -L "${LOCAL_PORT}:${REMOTE_HOST}:${REMOTE_PORT}" \
        "${MAC_USER}@${MAC_HOST}" \
        >> "$LOG" 2>&1
    EXIT=$?
    log "Tunnel exited (code $EXIT), restart in 15s..."
    sleep 15
done
