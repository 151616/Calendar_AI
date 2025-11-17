#!/bin/bash
# start.sh - start the Flask app with Gunicorn for Render deployment

# Exit immediately if a command exits with a non-zero status
set -e

echo "🚀 Starting Calendar AI Web Service..."

# Optional: activate a virtual environment if you have one
# source venv/bin/activate

# Ensure GOOGLE_API_KEY and GOOGLE_SERVICE_ACCOUNT_JSON are set
if [[ -z "$GOOGLE_API_KEY" ]]; then
  echo "❌ GOOGLE_API_KEY is not set!"
  exit 1
fi

if [[ -z "$GOOGLE_SERVICE_ACCOUNT_JSON" ]]; then
  echo "❌ GOOGLE_SERVICE_ACCOUNT_JSON is not set!"
  exit 1
fi

# Install dependencies just in case
echo "📦 Installing dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

# Start Gunicorn serving the Flask app
echo "🔥 Launching Gunicorn..."
exec gunicorn server:app \
  --workers 1 \
  --bind 0.0.0.0:$PORT \
  --log-level info
