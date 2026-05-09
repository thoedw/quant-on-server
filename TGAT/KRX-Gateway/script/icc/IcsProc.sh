# this script is for Linux
#!/bin/bash

if [[ $# -eq 1 ]] ; then
ps -eopid,pcpu,pmem,start|awk -v pid=$1 '{if($1 == pid) print $2" "$3;}'
fi

exit
