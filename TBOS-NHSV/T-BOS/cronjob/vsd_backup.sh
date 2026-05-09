#!/bin/sh
TODAY=`date +%Y%m%d`
# Log directory for VSD
VSD_LOGDIR=/VNST/vnst/bin/vsd/log
# Backup directory for VSD
VSD_BK_DIR=/Backup1/Log_Data/vsd_log
#LogFile=/VNST/vnst/bin/vsd/backup/vsd_backup.log
#DIR_BK=/VNST/vnst/bin/vsd/backup/$TODAY
LogFile=$VSD_BK_DIR/vsd_backup.log
DIR_BK=$VSD_BK_DIR/$TODAY

mkdir $DIR_BK

# Backup VSD log
echo "VSD backup date = $TODAY" >> $LogFile
#cd /VNST/vnst/bin/vsd/log
cd $VSD_LOGDIR
mv * $DIR_BK
echo "Finish backup date = $TODAY" >> $LogFile
