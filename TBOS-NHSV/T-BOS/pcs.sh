#!/bin/bash

find TUX_SRC -name *.pc | egrep '.+[A-Za-z0-9]+\.pc' > pcs.txt

tar -cvpf pcs.tar -L pcs.txt


