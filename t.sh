#!/bin/bash

EPD=/home/ischnell/epd

pushd $EPD/lib/python2.5/site-packages
rm -rf ensetuptools* easy* setuptools pkg_resources* [Ee]nstaller* site*
popd

pushd $EPD/bin
rm -rf egginst enpkg easy*
popd

exit 0

BOOT=/home/ischnell/enicab/enicab/inst_files/boot-enst.py
ENSTALLER_EGG=/home/ischnell/Enstaller/dist/Enstaller-4.0.1-1.egg

#$EPD/bin/python $BOOT $ENSTALLER_EGG
#$EPD/bin/enpkg -h || exit 1

# --------------------------------------------------------------------

exit 0

if true; then
#if false; then
    EGG=dist/ensetuptools-1.0.0-1.egg

    rm -rf build dist
    python setup.py bdist_egg
    cp depend.txt depend
    mv dist/ensetuptools-*.egg $EGG
    egginfo -u spec/depend $EGG
    egginfo --sd $EGG

    $EPD/bin/egginst $EGG || exit 1
else
    EGG=Enstaller-4.0.0-2.egg
    pushd /home/ischnell/foo
    rm -f *.egg
    zip -r $EGG *
    $EPD/bin/egginst $EGG || exit 1
    popd

    #$EPD/bin/egginst ~/Enstaller-4.0.0-1.egg || exit 1
fi

# --------------------------------------------------------------------

$EPD/bin/easy_install --help || exit 1
