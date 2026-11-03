# -*- coding: utf-8 -*-
"""Shared test setup: tests never talk to the live license portal."""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def _no_live_license_server(monkeypatch):
    import license_client
    monkeypatch.setattr(license_client, "SERVER_URL", "")
    monkeypatch.delenv(license_client.ENV_SERVER, raising=False)
