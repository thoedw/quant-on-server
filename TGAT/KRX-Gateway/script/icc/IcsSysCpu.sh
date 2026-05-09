#!/bin/bash

# For Linux.
cat /proc/stat | awk 'BEGIN{ OFMT = "%3.1f";tot = 0; free = 0; } { if( $1 == "cpu"){tot = $2 + $3 + $4 + $5 + $6 + $7 + $8 ; free = $5;} } END{print  (1 - free/tot) * 100}'

exit
