#!/bin/bash

TODAY=`date +%Y%m%d%H%M%S`
TODAY_DATE=`date +%Y%m%d`
FILES_NAME=${1}
DES_FOLDER=${2}

COMPANY=`pwd | cut -d'/' -f6`
BOS_IP="172.33.10.21"

#The root git folder
GIT_ROOT_DIR=`echo ~/src/eqt/$COMPANY`
CURRENT_BR=`git branch | grep \* | cut -d ' ' -f2`

echo ""
echo ""
echo "****************************Make deploy****************************"
echo "***********$GIT_ROOT_DIR and $FILES_NAME and ${CURRENT_BR} branch***********"

# Only allow deploying on REAL when you are in mater branch
if [[ "$CURRENT_BR" != "master" ]]; then
    echo "***********Sorry, you are not in MASTER branch, so can not deploy on REAL.***********"
    exit
fi

IS_CONTINUE="n"
echo "***********We are deploying on ${COMPANY} TEST server IP: ${BOS_IP}"
echo "Please enter y to continue doing on ${COMPANY} TEST server?"
read IS_CONTINUE
if [[ "$IS_CONTINUE" == "y" || "$IS_CONTINUE" == "Y" || "$IS_CONTINUE" == "yes" || "$IS_CONTINUE" == "YES" ]]; then
    IS_CONTINUE="y"
else
	echo "***********Exit without deploying..........."
	echo ""
	echo ""
    exit
fi

CMD_ALL_BK=""
for FILE_NAME in $FILES_NAME; 
do 
    CMD_ALL_BK="$CMD_ALL_BK cp $DES_FOLDER/$FILE_NAME $DES_FOLDER/back/$TODAY_DATE/$FILE_NAME.$TODAY; ";
done

#First: Backup.
ssh -t vnst@$BOS_IP << EOF
    . ~/.bash_profile;
    echo ""
    echo "Start deploying $FILES_NAME at $TODAY"  >> $DES_FOLDER/lib_deploy_log.log
    echo "Backup files: [$CMD_ALL_BK]" >> $DES_FOLDER/lib_deploy_log.log;
    
    mkdir -p $DES_FOLDER/back/$TODAY_DATE
    
    eval "$CMD_ALL_BK"
    
EOF

#Second: copy to folder on Remote server
echo "The files $FILES_NAME are coping to vnst@$BOS_IP:$DES_FOLDER"
scp $FILES_NAME vnst@$BOS_IP:$DES_FOLDER

echo "[Finished deploying $FILES_NAME]"
echo "***********************************************************************"
echo ""
echo ""

exit
