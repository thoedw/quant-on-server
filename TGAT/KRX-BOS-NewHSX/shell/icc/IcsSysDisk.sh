# this script is for LINUX
#!/bin/bash

if [[ $# -eq 1 ]] ; then

df -m 2> /dev/null | awk -v disk=$1 '{ if(NF==6) { if($6 == disk) {print $6 " " $5} } else if(NF==5) {if($5 == disk) {print $5 " " $4}} }' | sed 's/%//g'
#df -m | awk -v disk=$1 '{ if($6 == disk) {print $6 " " $5} }' | sed 's/%//g'
#df -m | grep -w $1 | grep -v grep | awk '{ print $6 " " $5}' | sed 's/%//g'
fi

exit
