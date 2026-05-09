#!/bin/sh

TODAY=`date +%Y%m%d`
# Log directory for Bank
BANK_LOGDIR=/VNST/vnst/bin/bank/log
# Backup directory for VSD
BANK_BK_DIR=/Backup1/Log_Data/bank_log

LogFile=$BANK_BK_DIR/bank_backup.log
DIR_BK=$BANK_BK_DIR/$TODAY

mkdir $DIR_BK

# Backup bank log
echo "Bank backup date = $TODAY" >> $LogFile
cd $BANK_LOGDIR
mv * $DIR_BK
echo "Finish backup date = $TODAY" >> $LogFile
