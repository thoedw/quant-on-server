#!/bin/sh

. /VNST/vnst/.vnst_profile

cd /VNST/vnst/cronjob

RUN_PATH=/VNST/vnst/xif/bin

#main
	
	${RUN_PATH}/xishmctl ap d &
	sleep 2
	${RUN_PATH}/xishmctl ap c &
	sleep 2
	${RUN_PATH}/xishmctl fp d &
	sleep 2
	${RUN_PATH}/xishmctl fp c &
	sleep 2
	${RUN_PATH}/xishmctl sm d &
	sleep 2
	${RUN_PATH}/xishmctl sm c &
