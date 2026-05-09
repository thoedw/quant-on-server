#!/bin/sh

. /user/stock/.bashrc
cd /user/stock/run/3rd/kafka
./bin/kafka-server-stop.sh config/server.properties

/user/stock/svrprc_java/sotp_process/sotp_process.sh stop
/user/stock/svrprc_java/pushsvr_process/pushserver.sh stop
/user/stock/svrprc_java/bosnoti_process/bosnoti_process.sh stop

