#!/bin/bash

# Root directory of your Django project
PROJECT_ROOT="$(pwd)"

echo "Removing Django migration files..."

find "$PROJECT_ROOT" -path "*/migrations/*.py" \
    ! -name "__init__.py" \
    -delete

find "$PROJECT_ROOT" -path "*/migrations/*.pyc" -delete

echo "All migration files removed (except __init__.py)."
