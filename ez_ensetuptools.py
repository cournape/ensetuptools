#!python
"""Bootstrap ensetuptools installation

If you want to use ensetuptools in your package's setup.py, just include this
file in the same directory with it, and add this to the top of your setup.py::

    from ez_setup import use_setuptools
    use_setuptools()

If you want to require a specific version of ensetuptools, set a download
mirror, or use an alternate download directory, you can do so by supplying
the appropriate options to ``use_setuptools()``.

This file can also be run as a script to install or upgrade ensetuptools.
"""


import os
import sys

try: from hashlib import md5
except ImportError: from md5 import md5


# Setup some global vars
DEFAULT_VERSION = "1.0.0"
DEFAULT_URL = "http://code.enthought.com/src"
md5_data = {
    'ensetuptools-1.0.0-py2.5.egg': 'e036fb78ae852d0f4dd417cc93784568',
}


def _validate_md5(egg_name, data):
    if egg_name in md5_data:
        digest = md5(data).hexdigest()
        if digest != md5_data[egg_name]:
            print >>sys.stderr, (
                "md5 validation of %s failed!  (Possible download problem?)"
                % egg_name
            )
            sys.exit(2)
    return data


def use_setuptools(
    version=DEFAULT_VERSION, download_base=DEFAULT_URL, to_dir=os.curdir,
    download_delay=15
):
    """Automatically find/download ensetuptools and make it available on sys.path

    `version` should be a valid ensetuptools version number that is available
    as an egg for download under the `download_base` URL (which should end with
    a '/').  `to_dir` is the directory where ensetuptools will be downloaded, if
    it is not already available.  If `download_delay` is specified, it should
    be the number of seconds that will be paused before initiating a download,
    should one be required.  If an older version of ensetuptools is installed,
    this routine will print a message to ``sys.stderr`` and raise SystemExit in
    an attempt to abort the calling script.
    """
    was_imported = 'pkg_resources' in sys.modules or 'setuptools' in sys.modules
    def do_download():
        egg = download_setuptools(version, download_base, to_dir, download_delay)
        sys.path.insert(0, egg)
        import setuptools
        setuptools.bootstrap_install_from = egg
    try:
        import pkg_resources
    except ImportError:
        return do_download()
    try:
        pkg_resources.require("ensetuptools>="+version)
        return
    except pkg_resources.VersionConflict, e:
        if was_imported:
            print >>sys.stderr, (
            "The required version of ensetuptools (>=%s) is not available, and\n"
            "can't be installed while this script is running. Please install\n"
            " a more recent version first, using 'easy_install -U ensetuptools'."
            "\n\n(Currently using %r)"
            ) % (version, e.args[0])
            sys.exit(2)
        else:
            del pkg_resources, sys.modules['pkg_resources']    # reload ok
            return do_download()
    except pkg_resources.DistributionNotFound:
        return do_download()


def download_setuptools(
    version=DEFAULT_VERSION, download_base=DEFAULT_URL, to_dir=os.curdir,
    delay = 15
):
    """Download setuptools from a specified location and return its filename

    `version` should be a valid setuptools version number that is available
    as an egg for download under the `download_base` URL (which should end
    with a '/'). `to_dir` is the directory where the egg will be downloaded.
    `delay` is the number of seconds to pause before an actual download attempt.
    """
    import urllib2, shutil
    egg_name = "ensetuptools-%s-py%s.egg" % (version,sys.version[:3])
    url = download_base + egg_name
    saveto = os.path.join(to_dir, egg_name)
    src = dst = None
    if not os.path.exists(saveto):  # Avoid repeated downloads
        try:
            from distutils import log
            if delay:
                log.warn("""
---------------------------------------------------------------------------
This script requires ensetuptools version %s to run (even to display
help).  I will attempt to download it for you (from
%s), but
you may need to enable firewall access for this script first.
I will start the download in %d seconds.

(Note: if this machine does not have network access, please obtain the file

   %s

and place it in this directory before rerunning this script.)
---------------------------------------------------------------------------""",
                    version, download_base, delay, url
                )
                from time import sleep
                sleep(delay)
            log.warn("Downloading %s", url)
            src = urllib2.urlopen(url)
            # Read/write all in one block, so we don't create a corrupt file
            # if the download is interrupted.
            data = _validate_md5(egg_name, src.read())
            dst = open(saveto,"wb")
            dst.write(data)
        finally:
            if src: src.close()
            if dst: dst.close()
    return os.path.realpath(saveto)


def main(argv, version=DEFAULT_VERSION):
    """Install or upgrade ensetuptools"""
    try:
        import setuptools
    except ImportError:
        egg = None
        try:
            egg = download_setuptools(version, delay=0)
            sys.path.insert(0,egg)
            from setuptools.command.easy_install import main
            return main(list(argv)+[egg])   # we're done here
        finally:
            if egg and os.path.exists(egg):
                os.unlink(egg)
    else:
        res = setuptools.__version__.split('-')
        if len(res) < 2 or res[1][0] != 's':
            print >>sys.stderr, (
            "You have an obsolete version of setuptools installed.  Please\n"
            "remove it from your system entirely before running this script."
            )
            sys.exit(2)

    req = "ensetuptools>="+version
    import pkg_resources
    try:
        pkg_resources.require(req)
    except pkg_resources.VersionConflict:
        try:
            from setuptools.command.easy_install import main
        except ImportError:
            from easy_install import main
        main(list(argv)+[download_setuptools(delay=0)])
        sys.exit(0) # try to force an exit
    else:
        if argv:
            from setuptools.command.easy_install import main
            main(argv)
        else:
            print "ensetuptools version %s or greater has been installed." % version
            print '(Run "ez_ensetuptools.py -U ensetuptools" to reinstall or upgrade.)'


def update_md5(filenames):
    """Update our built-in md5 registry"""

    import re

    for name in filenames:
        base = os.path.basename(name)
        f = open(name,'rb')
        md5_data[base] = md5(f.read()).hexdigest()
        f.close()

    data = ["    %r: %r,\n" % it for it in md5_data.items()]
    data.sort()
    repl = "".join(data)

    import inspect
    srcfile = inspect.getsourcefile(sys.modules[__name__])
    f = open(srcfile, 'rb')
    src = f.read()
    f.close()

    match = re.search("\nmd5_data = {\n([^}]+)}", src)
    if not match:
        print >>sys.stderr, "Internal error!"
        sys.exit(2)

    src = src[:match.start(1)] + repl + src[match.end(1):]
    f = open(srcfile, 'w')
    f.write(src)
    f.close()


if __name__=='__main__':
    if len(sys.argv)>2 and sys.argv[1]=='--md5update':
        update_md5(sys.argv[2:])
    else:
        main(sys.argv[1:])

