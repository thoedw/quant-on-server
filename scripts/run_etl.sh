#!/bin/bash
# Wrapper script to run ETL Pipeline

# Ensure we are in project directory
cd "$(dirname "$0")/.." || exit 1

# Activate virtual environment
source venv/bin/activate

# Execute the daily pipeline
python scripts/batch_daily.py
