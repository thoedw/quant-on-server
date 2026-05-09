#!/bin/bash
. /VNST/vnst/.vnst_profile
cd /VNST//vnst/cronjob
RUN_PATH=/VNST/vnst/bin/vsd/bin/
cd ${RUN_PATH}

if [[ $1 != "" ]]
then
	case $1 in
	start)
		echo "Process def_vsd_dr is starting"
		process=$(ps -ef | grep -v 'grep' | grep -c 'def_vsd_dr')
		if [[ "$process" -eq 2 ]]
		then
			echo "Process def_vsd_dr is already running"
			exit 0
		elif [[ "$process" -gt 2 ]] || [[ "$process" -eq 1 ]]
		then
			echo "Process def_vsd_dr problem need restart"
			echo "Check DB, which record causes shut-down process"
			exit 1
		else
			def_vsd_dr -s N >> /VNST/vnst/bin/vsd/log/all_dr.log &
			def_vsd_dr -s E >> /VNST/vnst/bin/vsd/log/all_dr.log &
			echo -e "\e[0;32mProcess def_vsd_dr start successfully\e[0;0m"
		fi
		;;
	status)
		for i in `ps -ef | grep "def_vsd_dr" | grep -v 'grep' | awk '{print $2}'`
		do
			echo "Process def_vsd_dr running on PID:$i"
		done
		process=$(ps -ef | grep -v 'grep'| grep -c 'def_vsd_dr')
		if [[ "$process" -eq 2 ]]
		then
			echo -e "\e[0;32mprocess def_vsd_dr is already running\e[0;0m"
			exit 0
		else
			echo -e "\e[0;31mprocess def_vsd_dr problem need restart\e[0;0m"
			exit 1
		fi
		;;
	restart)
		echo "Process def_vsd_dr is restarting"
		for i in `ps -ef | grep "def_vsd_dr" | grep -v 'grep' | awk '{print $2}'`
		do
			echo "Kill process $i of def_vsd_dr"
			kill -9 $i
		done
		process=$(ps -ef | grep -v 'grep' | grep -c 'def_vsd_dr')
		if [[ "$process" -eq 0 ]]
		then
			def_vsd_dr -s N >> /VNST/vnst/bin/vsd/log/all_dr.log &
			def_vsd_dr -s E >> /VNST/vnst/bin/vsd/log/all_dr.log &
			echo "Process def_vsd_dr restart successfully"
		fi
		;;
	stop)
		for i in `ps -ef | grep -v 'grep' |grep 'def_vsd_dr' | awk '{print $2}'`
		do
			echo "Kill process $i of def_vsd_dr"
			kill -9 $i
		done
		;;
	esac
else
	echo 'choice "start|restart|stop"'
fi
