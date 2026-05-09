#!/bin/bash
readonly PROC_NAME="ConvertToPDF"
readonly JAVA="/usr/lib/jvm/java-1.8.0-openjdk-1.8.0.275.b01-0.el6_10.x86_64/jre/bin/java"
readonly OFFICE_HOME="/VNST/vnst/LibreOffice/opt/libreoffice4.3"
readonly DAEMON="/VNST/vnst/java/convert_to_pdf/*:$OFFICE_HOME/program:$OFFICE_HOME/ure/lib"
readonly PID_PATH="/VNST/vnst/java/convert_to_pdf/"
readonly PROC_PID="${PID_PATH}${PROC_NAME}.pid"
readonly SPRING_COMMANDLINE="ConvertToPDF"
cd /VNST/vnst/java/convert_to_pdf
start()
{
 	echo "Starting  ${PROC_NAME}..."
	local PID=$(get_status)
	if [ -n "${PID}" ]; then
		echo "${PROC_NAME} is already running"
		exit 0
	fi
	
	 $JAVA -XX:MaxPermSize=128m -Xms512m -Xmx1024m -cp $DAEMON $SPRING_COMMANDLINE &
#    nohup java -XX:MaxPermSize=128m -Xms512m -Xmx1024m -Dspring.profiles.active=${SPRING_PROFILES_ACTIVE} ${DAEMON} ${SPRING_COMMANDLINE} > /dev/null 2>&1 &
    
	local PID=${!}

	if [ -n ${PID} ]; then
		echo " - Starting..."
		echo " - Created Process ID in ${PROC_PID}"
		echo ${PID} > ${PROC_PID}
	else
		echo " - failed to start."
	fi
}
stop()
{
	echo "Stopping ${PROC_NAME}..."
	local DAEMON_PID=`cat "${PROC_PID}"`

	if [ "$DAEMON_PID" -lt 3 ]; then
		echo "${PROC_NAME} was not  running."
	else
		kill $DAEMON_PID
		rm -f $PROC_PID
		echo " - Shutdown ...."
	fi
}
status()
{
	local PID=$(get_status)
	if [ -n "${PID}" ]; then
		echo "${PROC_NAME} is running"
	else
		echo "${PROC_NAME} is stopped"
		# start daemon
		#nohup java -jar "${DAEMON}" > /dev/null 2>&1 &
	fi
}

get_status()
{
	ps ux | grep ${PROC_NAME} | grep -v grep | awk '{print $2}'
}

case "$1" in
    start)
        start
        sleep 1
        ;;
    stop)
        stop
        sleep 1
        ;;
    status)
    status "${PROC_NAME}"
	;;
	*)
	echo "Usage: $0 {start | stop | status }"
esac
exit 0

#REF_URL : https://github.com/Gavinkim/springboot-execute-with-shell/blob/master/springboot.sh
# - spring boot execute script
#		chmod +x bosnoti_process.sh
#		> start 	./bosnoti_process.sh start
#		> stop 		./bosnoti_process.sh stop
#		> status 	./bosnoti_process.sh status





