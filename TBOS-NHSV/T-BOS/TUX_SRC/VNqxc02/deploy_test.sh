#!/bin/bash

TODAY=`date +%m%d%H%M`
SRC_NAME=${PWD##*/}

echo "$TODAY - Directory = $SRC_NAME"

scp $SRC_NAME vntuxman@172.16.21.30:/VNST/tuxs
ssh vntuxman@172.16.21.30 << EOF
	 
echo "$TODAY"  >> /VNST/tuxs/svr.log
	. ~/.bash_profile;
	echo "start shutdown $SRC_NAME .." >> /VNST/tuxs/svr.log
	/Tuxedo/bin/tmshutdown -s $SRC_NAME >> /VNST/tuxs/svr.log
	echo "start boot $SRC_NAME .." >> /VNST/tuxs/svr.log
	/Tuxedo/bin/tmboot -s $SRC_NAME >> /VNST/tuxs/svr.log
EOF

echo "$TODAY"

exit

