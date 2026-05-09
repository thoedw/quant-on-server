#!/bin/sh

TODAY=`date +%Y%m%d`
LogFile=/VNST/vnst/bin/bank2/backup/bank_backup.log
DIR_BK=/VNST/vnst/bin/bank2/backup/$TODAY
mkdir $DIR_BK
# Backup bank log
echo "Bank backup date = $TODAY" >> $LogFile
cd /VNST/vnst/bin/bank2/log
mv * $DIR_BK
echo "Finish backup date = $TODAY" >> $LogFile

