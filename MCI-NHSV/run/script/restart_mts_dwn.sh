#!/bin/sh

. /user/stock/.bashrc

#Kill process download MTS port 9090
ps -ef | grep -v grep | grep "\./server" | awk '{print $2}' | xargs kill -9

#Start process download MTS port 9090
cd /user/stock/dist
./server
