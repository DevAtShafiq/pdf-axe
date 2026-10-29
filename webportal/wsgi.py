# -*- coding: utf-8 -*-
"""WSGI entry for any host: gunicorn / waitress ``wsgi:application``, or
cPanel "Setup Python App" (point it at this file, entry point ``application``)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from web import make_app  # noqa: E402

application = make_app()
