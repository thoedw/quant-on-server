#!/bin/bash

today=$(date +"%Y%m%d")
echo "$today"

if [ "$(date -d "$today" +%u)" -eq 1 ]; then
	prevday=$(date -d "$today - 3 days" +"%Y%m%d")
else
	prevday=$(date -d "$today - 1 day" +"%Y%m%d")
fi

/user/stock/run/bin/sftpbatch $prevday

echo "$prevday"
