#/bin/sh
ctags -R --c++-kinds=+p --fields=+iaS --extra=+q --exclude="*.mk" --exclude="Makefile" --exclude="*.lis" --exclude="*.sh" LIB_SRC xif BIN_SRC 
cd /VNST/dev/include
ctags -R --c++-kinds=+p --fields=+iaS --extra=+q --exclude="*.mk" --exclude="Makefile" --exclude="*.lis" --exclude="*.sh" * 
