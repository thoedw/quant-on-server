# this script is for AIX
#!/bin/ksh

vmstat -v| awk 'BEGIN{ OFMT = "%3.1f";tot = 0; free = 0; } { if( $2 == "memory" && $3 == "pages") tot = $1; else if ($2 == "free" && $3 == "pages") free = $1; }END { print (1 - free/tot) * 100}'

 exit

