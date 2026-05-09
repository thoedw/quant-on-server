# this script is for AIX
#!/bin/ksh

mpstat 1 1 | awk '{ if( $1 =="ALL" ) print 100 - $15 }'

exit
