#!/bin/bash
# App Service startup. ONE worker on purpose: the exercise engine runs in-process (background thread)
# and sessions are in memory. Threads handle concurrent requests.
gunicorn web.main:app -k uvicorn.workers.UvicornWorker --workers 1 --threads 8 --timeout 600 --bind 0.0.0.0:8000
