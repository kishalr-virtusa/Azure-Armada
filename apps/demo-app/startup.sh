#!/bin/bash
# Startup script for Azure App Service
gunicorn -k uvicorn.workers.UvicornWorker app.main:app --bind 0.0.0.0:8000
