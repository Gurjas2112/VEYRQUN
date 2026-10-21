#!/bin/bash
# VEYRQUN 1-command startup script (Linux/macOS/Git Bash)

echo "Starting VEYRQUN Governance Control Tower..."

# Start FastAPI backend in background
python3 -m uvicorn --app-dir control-plane app:app --host 0.0.0.0 --port 8000 &
BACKEND_PID=$!

# Start Vite React frontend in background
(cd frontend && npm run dev) &
FRONTEND_PID=$!

# Kill both processes on exit / Ctrl+C
trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null" EXIT INT TERM

wait
