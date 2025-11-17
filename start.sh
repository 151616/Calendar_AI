#!/bin/bash
# start.sh - start the Flask app with Gunicorn

# Make sure the script fails on error
set -e

# Optional: activate virtual environment if you use one
# source venv/bin/activate

# Start Gunicorn
gunicorn server:app --workers 1 --bind 0.0.0.0:$PORT
