#!/bin/bash
LOG="/tmp/wshoto-mcp.log"
TUNNEL_LOG="/tmp/cloudflared.log"
NODE="/Users/huangjinfang/.workbuddy/binaries/node/versions/22.22.2/bin/node"
CLOUDFLARED="/opt/homebrew/bin/cloudflared"
CURL="/usr/bin/curl"
PROJECT="/Users/huangjinfang/.workbuddy/skills/wshoto-mcp"

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
export HOME="/Users/huangjinfang"

echo "[$(date)] === Starting wshoto-mcp ===" >> "$LOG"

# Stop existing
/usr/sbin/lsof -ti:3030 | xargs kill -9 2>/dev/null
sleep 1

# Start SSE server
cd "$PROJECT"
$NODE src/sse.mjs >> "$LOG" 2>&1 &
NODE_PID=$!
sleep 3

# Verify
if $CURL -sf http://localhost:3030/health > /dev/null 2>&1; then
  echo "[$(date)] SSE server PID=$NODE_PID running" >> "$LOG"
else
  echo "[$(date)] SSE failed, node PID=$NODE_PID, checking..." >> "$LOG"
  kill $NODE_PID 2>/dev/null
  # Retry once
  $NODE src/sse.mjs >> "$LOG" 2>&1 &
  sleep 5
  if $CURL -sf http://localhost:3030/health > /dev/null 2>&1; then
    echo "[$(date)] SSE server OK after retry" >> "$LOG"
  else
    echo "[$(date)] FATAL: SSE server failed twice" >> "$LOG"
    exit 1
  fi
fi

# Tunnel
$CLOUDFLARED tunnel --url http://localhost:3030 >> "$TUNNEL_LOG" 2>&1 &
sleep 5

URL=$(grep -o 'https://[a-z0-9.-]*\.trycloudflare\.com' "$TUNNEL_LOG" | tail -1)
echo "[$(date)] Tunnel: $URL/mcp" >> "$LOG"

wait
