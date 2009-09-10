The ensetuptools project is a replacement for setuptools that builds on
top of it and adds significant features.

It is based on setuptools 0.6c9

It starts from the setuptools source and adds the ensetuptools entry
point as well as specific improvements.

Improvements added:
-------------------

 * added support for removing a package
 * added support for post-install and pre-uninstall scripts
 * improved dependency resolution with enpkg.
 * easy_install can now work through a proxy for both http and https urls.
 * setup.py develop also runs post-install scripts and --uninstall runs
   pre-uninstall scripts
 * easy_install and enpkg now prefer final releases of distributions over dev
   builds.

Installation:
-------------

 * Remove setuptools from your system.
 * If your are not on a Windows platform, execute the egg:
   ``./ensetuptools-1.0.0-py2.5.egg``
 * If you are on Windows, download the installation script for ensetuptools:
   `ez_ensetuptools.py <http://code.enthought.com/src/ez_ensetuptools.py>`_
   and then un the script at a command prompt: ``python ez_ensetuptools.py``
 * Once the script completes, you will have the scripts
   enpkg and easy_install installed on your system.

To ensure that you are running ensetuptools's easy_install, type at the
command prompt: ``easy_install --version``
This will print the ensetuptools version number.
There is also an option ``-debug`` which gives various information about the
install location.

Remarks:
--------

 * While setuptools 0.6c9 still supports Python 2.3, ensetuptools only
   supports Python 2.4 and higher (except 3.X).  Since much of the code is
   setuptools code old setuptools features may still work with Python 2.3,
   but added features may not.  No attempts are being made to maintain
   compatibility with Python 2.3.
 * post-install and pre-uninstall scripts are located in the EGG-INFO
   folder.  If a file named ``post_install.py`` is present in this folder,
   it is executed after the install of the actual package.
   Likewise, if a file named ``pre_uninstall.py`` is present in this folder,
   it is executed before the package is removed.
