#!/bin/bash

. ${HOME}/.bash_profile

if [[ -z $DFIFHOME ]] ; then
	BASE=$HOME
else
	BASE=$DFIFHOME
fi

BINDIR=${BASE}/bin
ETCBIN=${BINDIR}/etc

today=$(date +"%Y%m%d")
echo "Today: $today"

${ETCBIN}/procFdtHsx -d$today
sleep 1
${ETCBIN}/procFdtHnx -d$today
