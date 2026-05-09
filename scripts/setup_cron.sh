#!/bin/bash
# Setup Cron Job for ETL Pipeline

# Get the absolute path to run_etl.sh
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
RUN_SCRIPT="$SCRIPT_DIR/run_etl.sh"

# Define the cron schedule: 19:00 Mon-Fri (Local VN time) = "0 19 * * 1-5"
# Note that cron typically uses the system's timezone.
CRON_JOB="0 19 * * 1-5 \"$RUN_SCRIPT\" >> \"$SCRIPT_DIR/../data/cron.log\" 2>&1"

# Check if job already exists
(crontab -l 2>/dev/null | grep -F "$RUN_SCRIPT") >/dev/null 2>&1

if [ $? -eq 0 ]; then
    echo "Cron job for ETL Pipeline already exists."
else
    # Append the cron job
    (crontab -l 2>/dev/null; echo "$CRON_JOB") | crontab -
    echo "Successfully installed cron job:"
    echo "$CRON_JOB"
fi
