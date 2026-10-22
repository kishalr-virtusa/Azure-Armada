"""Gunicorn hooks (startup.sh passes -c gunicorn.conf.py; the command-line flags there set everything else).

Telemetry starts in each worker after the fork: the exporter's background threads do not survive a fork, and
the FastAPI class must be instrumented before web.main is imported (the worker loads the app after post_fork).
"""
import os
import sys


def post_fork(server, worker):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        from dr_agent import telemetry
        telemetry.configure()
    except Exception:  # noqa: BLE001 - telemetry must never keep the console from starting
        server.log.exception("telemetry setup failed; continuing without it")


def worker_exit(server, worker):
    try:
        from dr_agent import telemetry
        telemetry.shutdown()
    except Exception:  # noqa: BLE001
        pass
