#!/bin/bash
# App Service startup. ONE worker on purpose: the exercise engine runs in-process (background thread)
# and sessions are in memory. UvicornWorker serves requests on one event loop and runs sync endpoints in its
# thread pool (gunicorn's --threads does not apply to it). gunicorn.conf.py starts telemetry in the worker.
gunicorn web.main:app -c gunicorn.conf.py -k uvicorn.workers.UvicornWorker --workers 1 --timeout 600 --bind 0.0.0.0:8000
