# this script is for LINUX
#!/bin/bash

cat /proc/meminfo | awk 'BEGIN{ OFMT = "%3.1f"; tot = 0; free = 0; } { if( $1 == "MemTotal:") tot = $2; else if ( $1 == "MemFree:") free = $2; } END { print (1 - free/tot) * 100}'

exit
