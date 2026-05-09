#!/bin/bash

TODAY=`date +%Y%m%d%H%M%S`
TODAY_DATE=`date +%Y%m%d`
SRC_NAME=${PWD##*/}
COMPANY=`pwd | cut -d'/' -f6`
BOS_IP="172.33.10.26"

#The root git folder
GIT_ROOT_DIR=`echo ~/src/eqt/$COMPANY`
CURRENT_BR=`git branch | grep \* | cut -d ' ' -f2`

echo ""
echo ""
echo "****************************Make deploy****************************"
echo "***********$GIT_ROOT_DIR and $SRC_NAME and ${CURRENT_BR} branch***********"

# Only allow deploying on REAL when you are in mater branch
if [[ "$CURRENT_BR" != "master" ]]; then
    echo "***********Sorry, you are not in MASTER branch, so can not deploy on REAL.***********"
    exit
fi

IS_CONTINUE="n"
echo "***********We are deploying on ${COMPANY} REAL server IP: ${BOS_IP}"
echo "Please enter y to continue on ${COMPANY} REAL server?"
read IS_CONTINUE
if [[ "$IS_CONTINUE" == "y" || "$IS_CONTINUE" == "Y" || "$IS_CONTINUE" == "yes" || "$IS_CONTINUE" == "YES" ]]; then
    IS_CONTINUE="y"
else
	echo "***********Exit without deploying..........."
	echo ""
	echo ""
    exit
fi
		
#First: copy to temp folder on Remote server
scp -C $SRC_NAME vnst@$BOS_IP:/VNST/tuxs/temp/

#Second: Backup, Shutdown, Copy then Start.
ssh -t vnst@$BOS_IP << EOF
    . ~/.bash_profile;
    echo "\n\nStart deploying at $TODAY"  >> /VNST/tuxs/svr.log
    
	
    echo "start shutdown $SRC_NAME .." >> /VNST/tuxs/svr.log
    sudo -iu vntuxman tmshutdown -s $SRC_NAME >> /VNST/tuxs/svr.log
	
    mkdir -p /VNST/tuxs/backup/$TODAY_DATE
    cp /VNST/tuxs/$SRC_NAME /VNST/tuxs/backup/$TODAY_DATE/$SRC_NAME.$TODAY
	echo "Backup $SRC_NAME to /VNST/tuxs/backup/$SRC_NAME.$TODAY.." >> /VNST/tuxs/svr.log
	
    sudo -iu vntuxman cp /VNST/tuxs/temp/$SRC_NAME /VNST/tuxs/$SRC_NAME
    
    echo "start boot $SRC_NAME .." >> /VNST/tuxs/svr.log
    sudo -iu vntuxman tmboot -s $SRC_NAME >> /VNST/tuxs/svr.log
    
EOF

echo "Finished deploying $SRC_NAME"
echo "***********************************************************************"
echo ""
echo ""

exit
