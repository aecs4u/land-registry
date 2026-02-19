#!/bin/bash

source .venv/bin/activate
uv run uvicorn app.land_registry_app:app --reload
