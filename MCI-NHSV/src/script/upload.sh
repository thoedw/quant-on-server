#!/bin/bash
trap "exit" INT

SERVERS=("172.33.30.11" "172.33.30.12")

if [ $# -ne 1 ]; then
    echo "Usage >> $0 FILENAME"
    exit
fi

for SVR in "${SERVERS[@]}"; do
    scp $1 stock@$SVR:/user/stock/deploy/
done
