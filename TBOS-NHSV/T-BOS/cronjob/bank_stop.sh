#!/bin/sh

. /VNST/vnst/.vnst_profile

for ID in `ps -ef | grep -v grep | grep BankMiddleware | awk '{print $2}' | sort -r`
    do
        echo kill BankMiddleware
        kill $ID
    done

for ID in `ps -ef | grep -v grep | grep BankProcessor | awk '{print $2}' | sort -r`
    do
        echo kill BankProcessor
        kill $ID
    done

for ID in `ps -ef | grep -v grep | grep Bank_ | awk '{print $2}' | sort -r`
    do
        echo kill BankProcessor
        kill $ID
    done
