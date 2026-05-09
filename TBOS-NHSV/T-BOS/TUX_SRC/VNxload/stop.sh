
for ID in `ps -ef | grep -v grep | grep OrderProcessL | awk '{print $2}' | sort -r`
    do
        echo OrderProcessL
        kill $ID
    done

