#!/bin/sh

. /user/stock/.bashrc

cd /user/stock/run/3rd/kafka
nohup ./bin/zookeeper-server-start.sh config/zookeeper.properties &
sleep 10
nohup ./bin/kafka-server-start.sh config/server.properties &

sleep 1
cd /user/stock/svrprc_java/sotp_process/
/user/stock/svrprc_java/sotp_process/sotp_process.sh start

sleep 1
cd /user/stock/svrprc_java/pushsvr_process/
/user/stock/svrprc_java/pushsvr_process/pushserver.sh start

sleep 1
cd /user/stock/svrprc_java/bosnoti_process/
/user/stock/svrprc_java/bosnoti_process/bosnoti_process.sh start

