#!python
"""\
Easy Install
------------

A tool for doing automatic download/extract/build of distutils-based Python
packages.  For detailed documentation, see the accompanying EasyInstall.txt
file, or visit the `EasyInstall home page`__.

__ http://peak.telecommunity.com/DevCenter/EasyInstall

This is a patched version for Enstaller.
"""

# Patched with easy_install.patch and prefer_released.patch from Enthought
# starting with setuptools 0.6c9

import sys, os, os.path, shutil, zipimport, tempfile, re, stat, random
import zipfile
from os import path
from glob import glob

from setuptools import Command
from setuptools.sandbox import run_setup
from distutils import log, dir_util
from distutils.sysconfig import get_python_lib
from distutils.errors import DistutilsArgError, DistutilsOptionError, \
    DistutilsError
from setuptools.utils import rm_rf, chmod, execute_script
from setuptools.archive_util import unpack_archive
from setuptools.package_index import PackageIndex, parse_bdist_wininst
from setuptools.package_index import URL_SCHEME
from setuptools.command import bdist_egg, egg_info
from pkg_resources import *
sys_executable = os.path.normpath(sys.executable)

__all__ = [
    'samefile', 'easy_install', 'PthDistributions', 'extract_wininst_cfg',
    'main', 'get_exe_prefixes',
]


def samefile(p1, p2):
    if hasattr(os.path, 'samefile') and (
        os.path.exists(p1) and os.path.exists(p2)
    ):
        return os.path.samefile(p1,p2)
    return (
        os.path.normpath(os.path.normcase(p1)) ==
        os.path.normpath(os.path.normcase(p2))
    )


class easy_install(Command):
    """Manage a download/build/install process"""
    description = "Find/get/install Python packages"
    command_consumes_arguments = True

    user_options = [
        ('prefix=', None, "installation prefix"),
        ("zip-ok", "z", "install package as a zipfile"),
        ("multi-version", "m", "make apps have to require() a version"),
        ("upgrade", "U", "force upgrade (searches PyPI for latest versions)"),
        ("remove", "r", "remove (uninstall) package"),
        ("install-dir=", "d", "install package to DIR"),
        ("script-dir=", "s", "install scripts to DIR"),
        ("exclude-scripts", "x", "Don't install scripts"),
        ("always-copy", "a", "Copy all needed packages to install dir"),
        ("index-url=", "i", "base URL of Python Package Index"),
        ("find-links=", "f", "additional URL(s) to search for packages"),
        ("delete-conflicting", "D", "no longer needed; don't use this"),
        ("ignore-conflicts-at-my-risk", None,
            "no longer needed; don't use this"),
        ("build-directory=", "b",
            "download/extract/build in DIR; keep the results"),
        ('optimize=', 'O',
         "also compile with optimization: -O1 for \"python -O\", "
         "-O2 for \"python -OO\", and -O0 to disable [default: -O0]"),
        ('record=', None,
         "filename in which to record list of installed files"),
        ('always-unzip', 'Z', "don't install as a zipfile, no matter what"),
        ('site-dirs=','S',"list of directories where .pth files work"),
        ('editable', 'e', "Install specified packages in editable form"),
        ('no-deps', 'N', "don't install dependencies"),
        ('allow-dev', 'D', "allow non-release packages to be installed"),
        ('allow-hosts=', 'H', "pattern(s) that hostnames must match"),
        ('local-snapshots-ok', 'l', "allow building eggs from local checkouts"),
        ('proxy=', 'p', "Add proxy for downloads")
    ]
    boolean_options = [
        'zip-ok', 'multi-version', 'exclude-scripts', 'upgrade', 'always-copy',
        'delete-conflicting', 'ignore-conflicts-at-my-risk', 'editable',
        'no-deps', 'local-snapshots-ok', 'allow-dev'
    ]
    negative_opt = {'always-unzip': 'zip-ok'}
    create_index = PackageIndex

    def initialize_options(self):
        self.zip_ok = self.local_snapshots_ok = None
        self.install_dir = self.script_dir = self.exclude_scripts = None
        self.index_url = None
        self.find_links = None
        self.build_directory = None
        self.args = None
        self.optimize = self.record = None
        self.remove = None
        self.proxy = None
        self.upgrade = self.always_copy = self.multi_version = None
        self.editable = self.no_deps = self.allow_hosts = None
        self.allow_dev = None
        self.root = self.prefix = self.no_report = None

        # Options not specifiable via command line
        self.package_index = None
        self.pth_file = self.always_copy_from = None
        self.delete_conflicting = None
        self.ignore_conflicts_at_my_risk = None
        self.site_dirs = None
        self.installed_projects = {}
        self.sitepy_installed = False
        # Always read easy_install options, even if we are subclassed, or have
        # an independent instance created.  This ensures that defaults will
        # always come from the standard configuration file(s)' "easy_install"
        # section, even if this is a "develop" or "install" command, or some
        # other embedding.
        self._dry_run = None
        self.verbose = self.distribution.verbose
        self.distribution._set_command_options(
            self, self.distribution.get_option_dict('easy_install')
        )

    def delete_blockers(self, blockers):
        for filename in blockers:
            if os.path.exists(filename) or os.path.islink(filename):
                log.info("Deleting %s", filename)
                if not self.dry_run:
                    rm_rf(filename)

    def finalize_options(self):
        self._expand('install_dir','script_dir','build_directory','site_dirs')
        # If a non-default installation directory was specified, default the
        # script directory to match it.
        if self.script_dir is None:
            self.script_dir = self.install_dir

        # Let install_dir get set by install_lib command, which in turn
        # gets its info from the install command, and takes into account
        # --prefix and --home and all that other crud.
        self.set_undefined_options('install_lib',
            ('install_dir','install_dir')
        )
        # Likewise, set default script_dir from 'install_scripts.install_dir'
        self.set_undefined_options('install_scripts',
            ('install_dir', 'script_dir')
        )
        # default --record from the install command
        self.set_undefined_options('install', ('record', 'record'))
        normpath = map(normalize_path, sys.path)
        self.all_site_dirs = get_site_dirs()
        if self.site_dirs is not None:
            site_dirs = [
                os.path.expanduser(s.strip()) for s in self.site_dirs.split(',')
            ]
            for d in site_dirs:
                if not os.path.isdir(d):
                    log.warn("%s (in --site-dirs) does not exist", d)
                elif normalize_path(d) not in normpath:
                    raise DistutilsOptionError(
                        d+" (in --site-dirs) is not on sys.path"
                    )
                else:
                    self.all_site_dirs.append(normalize_path(d))
        if not self.editable: self.check_site_dir()
        self.index_url = self.index_url or "http://pypi.python.org/simple"
        self.shadow_path = self.all_site_dirs[:]
        for path_item in self.install_dir, normalize_path(self.script_dir):
            if path_item not in self.shadow_path:
                self.shadow_path.insert(0, path_item)

        if self.allow_hosts is not None:
            hosts = [s.strip() for s in self.allow_hosts.split(',')]
        else:
            hosts = ['*']
        if self.package_index is None:
            self.package_index = self.create_index(
                self.index_url, search_path = self.shadow_path, hosts=hosts,
            )
        self.local_index = Environment(self.shadow_path+sys.path)

        if self.find_links is not None:
            if isinstance(self.find_links, basestring):
                self.find_links = self.find_links.split()
        else:
            self.find_links = []

        # Add any additional configured repos.
        from enstaller.config import get_configured_repos
        self.find_links.extend(get_configured_repos())

        if self.local_snapshots_ok:
            self.package_index.scan_egg_links(self.shadow_path+sys.path)
        self.package_index.add_find_links(self.find_links)
        self.set_undefined_options('install_lib', ('optimize','optimize'))
        if not isinstance(self.optimize,int):
            try:
                self.optimize = int(self.optimize)
                if not (0 <= self.optimize <= 2): raise ValueError
            except ValueError:
                raise DistutilsOptionError("--optimize must be 0, 1, or 2")

        if self.delete_conflicting and self.ignore_conflicts_at_my_risk:
            raise DistutilsOptionError(
                "Can't use both --delete-conflicting and "
                "--ignore-conflicts-at-my-risk at the same time")

        if self.editable and not self.build_directory:
            raise DistutilsArgError(
                "Must specify a build directory (-b) when using --editable")

        if not self.args:
            raise DistutilsArgError(
                "No urls, filenames, or requirements specified, see --help")

        self.outputs = []

        # Add a list for files installed by the current package only.
        self.outputs_this_package = []

        if self.proxy is not None:
            from enstaller.proxy.api import setup_proxy
            installed = setup_proxy(self.proxy)
            print "Yes...", installed
            print self.proxy

    def run(self):
        if self.verbose<>self.distribution.verbose:
            log.set_verbosity(self.verbose)
        try:
            if self.remove:
                self.uninstall(self.args)
            else:
                for spec in self.args:
                    self.easy_install(spec, not self.no_deps)

                if self.record:
                    # Get the list of files installed
                    outputs = self.outputs

                    # Strip any package prefix
                    if self.root:
                        root_len = len(self.root)
                        for counter in xrange(len(outputs)):
                            outputs[counter] = outputs[counter][root_len:]
                    from distutils import file_util
                    self.execute(
                        file_util.write_file, (self.record, outputs),
                        "writing list of installed files to '%s'" %
                        self.record
                    )
                self.warn_deprecated_options()
        finally:
            log.set_verbosity(self.distribution.verbose)

    def pseudo_tempname(self):
        """Return a pseudo-tempname base in the install directory.
        This code is intentionally naive; if a malicious party can write to
        the target directory you're already in deep doodoo.
        """
        try:
            pid = os.getpid()
        except:
            pid = random.randint(0, sys.maxint)
        return os.path.join(self.install_dir, "test-easy-install-%s" % pid)

    def warn_deprecated_options(self):
        if self.delete_conflicting or self.ignore_conflicts_at_my_risk:
            log.warn(
                "Note: The -D, --delete-conflicting and"
                " --ignore-conflicts-at-my-risk no longer have any purpose"
                " and should not be used."
            )

    def check_site_dir(self):
        """Verify that self.install_dir is .pth-capable dir, if needed"""
        instdir = normalize_path(self.install_dir)
        pth_file = os.path.join(instdir,'easy-install.pth')

        try:
            os.makedirs(instdir)
        except OSError:
            # Oh well -- hopefully this error simply means that it
            #  is already there.  If not the subsequent write test
            #  will identify the problem.
            pass

        # Is it a configured, PYTHONPATH, implicit, or explicit site dir?
        is_site_dir = instdir in self.all_site_dirs

        if not is_site_dir:
            # No?  Then directly test whether it does .pth file processing
            is_site_dir = self.check_pth_processing()
        else:
            # make sure we can write to target dir
            testfile = self.pseudo_tempname()+'.write-test'
            test_exists = os.path.exists(testfile)
            try:
                if test_exists: os.unlink(testfile)
                open(testfile,'w').close()
                os.unlink(testfile)
            except (OSError,IOError):
                self.cant_write_to_target()

        if not is_site_dir and not self.multi_version:
            # Can't install non-multi to non-site dir
            log.warn(self.no_default_version_msg())

        if is_site_dir:
            if self.pth_file is None:
                self.pth_file = PthDistributions(pth_file, self.all_site_dirs)
        else:
            self.pth_file = None

        PYTHONPATH = os.environ.get('PYTHONPATH','').split(os.pathsep)
        if instdir not in map(normalize_path, filter(None,PYTHONPATH)):
            # only PYTHONPATH dirs need a site.py, so pretend it's there
            self.sitepy_installed = True
        elif self.multi_version and not os.path.exists(pth_file):
            self.sitepy_installed = True    # don't need site.py in this case
            self.pth_file = None            # and don't create a .pth file
        self.install_dir = instdir

    def cant_write_to_target(self):
        msg = """can't create or remove files in install directory

The following error occurred while trying to add or remove files in the
installation directory:

    %s

The installation directory you specified (via --install-dir, --prefix, or
the distutils default setting) was:

    %s
"""     % (sys.exc_info()[1], self.install_dir,)

        if not os.path.exists(self.install_dir):
            msg += """
This directory does not currently exist.  Please create it and try again, or
choose a different installation directory (using the -d or --install-dir
option).
"""
        else:
            msg += """
Perhaps your account does not have write access to this directory?  If the
installation directory is a system-owned directory, you may need to sign in
as the administrator or "root" account.  If you do not have administrative
access to this machine, you may wish to choose a different installation
directory, preferably one that is listed in your PYTHONPATH environment
variable.

For information on other options, you may wish to consult the
documentation at:

  http://peak.telecommunity.com/EasyInstall.html

Please make the appropriate changes for your system and try again.
"""
        raise DistutilsError(msg)


    def check_pth_processing(self):
        """Empirically verify whether .pth files are supported in inst. dir"""
        instdir = self.install_dir
        log.info("Checking .pth file support in %s", instdir)
        pth_file = self.pseudo_tempname()+".pth"
        ok_file = pth_file+'.ok'
        ok_exists = os.path.exists(ok_file)
        try:
            if ok_exists: os.unlink(ok_file)
            f = open(pth_file,'w')
        except (OSError,IOError):
            self.cant_write_to_target()
        else:
            try:
                f.write("import os;open(%r,'w').write('OK')\n" % (ok_file,))
                f.close(); f=None
                executable = sys.executable
                if os.name=='nt':
                    dirname,basename = os.path.split(executable)
                    alt = os.path.join(dirname,'pythonw.exe')
                    if basename.lower()=='python.exe' and os.path.exists(alt):
                        # use pythonw.exe to avoid opening a console window
                        executable = alt

                from distutils.spawn import spawn
                spawn([executable,'-E','-c','pass'],0)

                if os.path.exists(ok_file):
                    log.info(
                        "TEST PASSED: %s appears to support .pth files",
                        instdir
                    )
                    return True
            finally:
                if f: f.close()
                if os.path.exists(ok_file): os.unlink(ok_file)
                if os.path.exists(pth_file): os.unlink(pth_file)
        if not self.multi_version:
            log.warn("TEST FAILED: %s does NOT support .pth files", instdir)
        return False

    def install_egg_scripts(self, dist):
        """Write all the scripts for `dist`, unless scripts are excluded"""
        if not self.exclude_scripts and dist.metadata_isdir('scripts'):
            for script_name in dist.metadata_listdir('scripts'):
                self.install_script(
                    dist, script_name,
                    dist.get_metadata('scripts/'+script_name)
                )
        self.install_wrapper_scripts(dist)

    def add_output(self, path):
        if os.path.isdir(path):
            for base, dirs, files in os.walk(path):
                for filename in files:
                    self.outputs.append(os.path.join(base, filename))
                    self.outputs_this_package.append(os.path.join(base,
                                                                  filename))
        else:
            self.outputs.append(path)
            self.outputs_this_package.append(path)

    def not_editable(self, spec):
        if self.editable:
            raise DistutilsArgError(
                "Invalid argument %r: you can't use filenames or URLs "
                "with --editable (except via the --find-links option)."
                % (spec,)
            )

    def check_editable(self,spec):
        if not self.editable:
            return

        if os.path.exists(os.path.join(self.build_directory, spec.key)):
            raise DistutilsArgError(
                "%r already exists in %s; can't do a checkout there" %
                (spec.key, self.build_directory)
            )

    def get_deps(self):
        """Check for back dependencies. The idea is that if a package is being
        uninstalled and a dependency is also being uninstalled, this will help
        check to see if any other packages will be broken by the dependency. It
        returns a dictionary in the format
        {dependency: {project1: dependency_version},
                     {project2: dependency_version}}
        """

        # Get the set of installed packages
        projects = WorkingSet()

        # Instantiate dictionary of project requirements
        reqdict = {}

        # Instantiate back-dependency dictionary. The ONLY items in this
        # will be projects that are depended upon by something else.
        low_dep_dict = {}

        # Create dictionary of each installed project and related dependencies
        for project in projects:

            # list of requirements for each project
            try:
                reqs = require(project.project_name)
            except DistributionNotFound:
                continue
            reqdict[project] = reqs

        # Create the dictionary of packages being depended on.
        for key, value in reqdict.iteritems():
            for item in value:

                # This keeps out entries that have only themselves in the
                # dependency list
                if item.project_name == key.project_name:
                    continue

                # spec becomes a list of tuples of version numbers and
                # operators, eg [(">=", "2.0"), ("<=", 3.0")]
                spec = value[0].requires()[0].specs

                # version_list will become a concatenated list of versions, eg
                # ['>=2.0', '<=3.0']
                version_list = []
                if not spec:
                    version_list.append('(any version)')
                for version in spec:
                    version_list.append(''.join(version))
                version_string = ','.join(version_list)
                if low_dep_dict.has_key(item.project_name):
                    low_dep_dict[item.project_name][key.project_name] = \
                        version_string
                else:
                    low_dep_dict[item.project_name] = \
                        {key.project_name: version_string}

        # low_dep_dict is in the form
        # {dependency: {project1: dependency_version},
        #              {project2: dependency_version}}
        return low_dep_dict

    def check_deps(self, current_dep, user_spec, all):
        """Takes in the current dependency being checked, the project specified
        for removal, and the set of all packages that something depends upon.
        Returns True or False based on user confirmation
        """

        if current_dep not in all.keys():
            print "Nothing else depends on %s." % current_dep

        # Check to see if a dependency is is only required by the package
        # specified for removal.
        elif len(all[current_dep]) == 1 and \
             user_spec in all[current_dep].keys():
            print "Only %s seems to require %s." % (user_spec, current_dep)

        else:
            # Fetch dictionary of projects that require current_dep
            depends_on_dep = all[current_dep]

            # Print list of projects that need current_dep
            for project in depends_on_dep:
                print "%s %s is needed by %s." % \
                (current_dep, depends_on_dep[project], project)

        choice = ''
        while 1:
            if choice.lower() == 'n':
                return False
            elif choice.lower() == 'y':
                return True
            else:
                choice = raw_input("Remove %s? [Y/N] " % current_dep)

    def _run_post_install(self, installed_egg_path):
        """
        run any post-install scripts in the newly-installed egg defined by the
        package passed in (the package is either a string defining a package
        name or package requirement spec, or a package object containing info
        about the package downloaded (not just installed)).
        """
        tmp_unpack_dir = None

        #
        # if the egg installed is a dir, simply check the EGG-INFO subdir
        # for a post_install.py script and run it, otherwise, unzip it to
        # a temp location and do the same thing
        #
        if(path.isdir(installed_egg_path)):
            egg_dir = installed_egg_path
        else:
            # FIXME:
            #     If the installed egg is a zipfile, open the zipfile
            #     (using the zipfile module) and only store the
            #     post install script to a tempfile (instead of unpacking
            #     the whole archive).
            tmp_unpack_dir = tempfile.mkdtemp(prefix="easy_install-")
            egg_dir = path.join(tmp_unpack_dir,
                                path.basename(installed_egg_path))
            unpack_archive(installed_egg_path, egg_dir)
        #
        # check for post_install.py and run if present
        #
        pi_script = path.join(egg_dir, "EGG-INFO", "post_install.py")
        if path.exists(pi_script):
            log.info("Found EGG-INFO/post_install.py, executing it")
            try:
                execute_script(pi_script)
            except Exception, err:
                log.error("Error: problem running post-install script "
                          "%s: %s\n", pi_script, err)

        #
        # cleanup if a temp extraction was done
        #
        if tmp_unpack_dir:
            rm_rf(tmp_unpack_dir)


    def _run_pre_uninstall(self, installed_egg_path):
        """
        run any pre-uninstall scripts in the installed egg defined by the
        package passed in (the package is either a string defining a package
        name or package requirement spec, or a package object containing info
        about the package.
        """
        tmp_unpack_dir = None

        #
        # if the egg installed is a dir, simply check the EGG-INFO subdir
        # for a pre_uninstall.py script and run it, otherwise, unzip it to
        # a temp location and do the same thing
        #
        if path.isdir(installed_egg_path):
            egg_dir = installed_egg_path

        else:
            tmp_unpack_dir = tempfile.mkdtemp(prefix="easy_install-")
            egg_dir = path.join(tmp_unpack_dir,
                                path.basename(installed_egg_path))
            unpack_archive(installed_egg_path, egg_dir)
        #
        # check for uninstall.py and run if present
        #
        uninstall_script = path.join(egg_dir, "EGG-INFO", "pre_uninstall.py")
        if path.exists(uninstall_script):
            log.info("Found EGG-INFO/pre_uninstall.py, executing it")
            try:
                execute_script(uninstall_script)
            except Exception, err:
                log.error("Error: problem running uninstall script %s: %s\n",
                          uninstall_script, err)

        #
        # cleanup if a temp extraction was done
        #
        if tmp_unpack_dir:
            rm_rf(tmp_unpack_dir)


    def uninstall(self, specs):
        """ Uninstall function to remove all files associated with an egg,
        including scripts generated.  Also does a back-check of dependencies
        on the package(s) being uninstalled and prompts the user for
        uninstalling unnecessary dependencies that can be removed
        and warns if uninstalling a package could break another installed
        package.
        """
        try:
            # If no dependencies, then just get the distributions from
            #  the requirement specifications and remove them.
            if self.no_deps:
                for spec in specs:
                    try:
                        dist = working_set.find(Requirement.parse(spec))
                    except:
                        raise DistributionNotFound
                    self._remove_dist(dist)
            return

            all_deps = self.get_deps()

            for spec in specs:
                pkgs = require(spec)

                # If the package found has dependencies, prompt the user if they
                # want the dependencies to be uninstalled as well.
                if len(pkgs) > 1:
                    for dep in pkgs[1:]:
                        if self.check_deps(dep.project_name, spec, all_deps):
                            self._remove_dist(dep)

                # Finally, check if user-specified package can be safely
                # removed.
                if self.check_deps(pkgs[0].project_name, spec, all_deps):
                    self._remove_dist(pkgs[0])

        except DistributionNotFound:
                log.info("Could not find suitable package for: %s" % spec)


    def _remove_dist(self, dist):
        """ Module to remove Distribution objects.
        """
        pkg_path = dist.location
        log.info("Removing %s..." % pkg_path)
        self._run_pre_uninstall(pkg_path)
        self._remove_package_file(pkg_path)


    def _remove_package_file(self, package_filepath):
        """
        Finds the easy_install.pth file and removes the line containing the
        package_filepath, if present, then removes the egg file or dir.
        """
        retcode = 0
        package_path = path.abspath(package_filepath)
        (package_dir, package_fullname) = path.split(package_path)

        # Read the file (if it exists), find the matching line and write the
        # file again without the matching line.
        pth_file = path.join(package_dir, "easy-install.pth")
        if path.exists(pth_file):
            fh = open(pth_file, "r")
            lines = fh.readlines()
            fh.close()

            newlines = []
            for line in lines:
                # On Windows, a leading ./ is often found and is safe to remove
                # for comparisons...also, strip off the newline.
                chkline = line.strip()
                if chkline.startswith("./") :
                    chkline = chkline[2:]
                if chkline != package_fullname:
                    newlines.append(line)

            else:
                fh = open(pth_file, "wu")
                for line in newlines:
                    fh.write(line)
                fh.close()

        # Check for the installed_files.log file in the EGG-INFO of the
        #   egg and remove all files listed in it.
        #   This check will look inside zip-safe and non-zip-safe eggs.
        files_file_path = path.join(package_path,
                                    'EGG-INFO', 'installed_files.log')
        if path.isdir(package_path):
            if path.exists(files_file_path):
                fh = open(files_file_path, "r")
                for filename in fh.readlines():
                    retcode = rm_rf(filename.strip()) or retcode
                fh.close()
                retcode = rm_rf(files_file_path) or retcode
        else:
            egg_file = zipfile.ZipFile(package_path, "r")
            installed_files = []
            for filename in egg_file.namelist():
                if filename.endswith('installed_files.log'):
                    log = egg_file.read(filename)
                    installed_files = log.splitlines()
                    break
            egg_file.close()
            for file in installed_files:
                retcode = rm_rf(file) or retcode

        # If it can't find the .files file, just try to remove the
        # directory or egg file.
        retcode = rm_rf(package_path) or retcode

        return retcode


    def easy_install(self, spec, deps=False):
        tmpdir = tempfile.mkdtemp(prefix="easy_install-")
        download = None
        if not self.editable: self.install_site_py()

        try:
            if not isinstance(spec,Requirement):
                if URL_SCHEME(spec):
                    # It's a url, download it to tmpdir and process
                    self.not_editable(spec)
                    download = self.package_index.download(spec, tmpdir)
                    return self.install_item(None, download, tmpdir, deps, True)

                elif os.path.exists(spec):
                    # Existing file or directory, just process it directly
                    self.not_editable(spec)
                    return self.install_item(None, spec, tmpdir, deps, True)
                else:
                    spec = parse_requirement_arg(spec)

            self.check_editable(spec)
            dist = self.package_index.fetch_distribution(
                spec, tmpdir, self.upgrade, self.editable,
                not self.always_copy, not self.allow_dev
            )

            if dist is None:
                msg = "Could not find suitable distribution for %r" % spec
                if self.always_copy:
                    msg+=" (--always-copy skips system and development eggs)"
                raise DistutilsError(msg)
            elif dist.precedence==DEVELOP_DIST:
                # .egg-info dists don't need installing, just process deps
                self.process_distribution(spec, dist, deps, "Using")
                return dist
            else:
                return self.install_item(spec, dist.location, tmpdir, deps)

        finally:
            rm_rf(tmpdir)

    def install_item(self, spec, download, tmpdir, deps, install_needed=False):

        # Installation is also needed if file in tmpdir or is not an egg
        install_needed = install_needed or self.always_copy
        install_needed = install_needed or os.path.dirname(download) == tmpdir
        install_needed = install_needed or not download.endswith('.egg')
        install_needed = install_needed or (
            self.always_copy_from is not None and
            os.path.dirname(normalize_path(download)) ==
            normalize_path(self.always_copy_from)
        )

        if spec and not install_needed:
            # at this point, we know it's a local .egg, we just don't know if
            # it's already installed.
            for dist in self.local_index[spec.project_name]:
                if dist.location==download:
                    break
            else:
                install_needed = True   # it's not in the local index

        log.info("Processing %s", os.path.basename(download))

        if install_needed:
            dists = self.install_eggs(spec, download, tmpdir)
            for dist in dists:
                self.process_distribution(spec, dist, deps)
        else:
            dists = [self.check_conflicts(self.egg_distribution(download))]
            self.process_distribution(spec, dists[0], deps, "Using")

        if spec is not None:
            for dist in dists:
                if dist in spec:
                    return dist


    def add_installed_files_list(self):
        """ Adds all of the installed files to the EGG-INFO folder of the
        installed package, in the file 'installed_files.log'.
        """
        # Get the list of files installed, give error if none are passed
        outputs = self.outputs_this_package
        if len(outputs) == 0:
            log.warn('No files installed.')
            return

        # Check for either a zipped egg or an egg directory
        egg_zip = None
        egg_path = ''
        for file in outputs:

            # If an EGG-INFO path is found, save the .egg path
            if len(file.split('EGG-INFO')) > 1:
                egg_path = file.split('EGG-INFO')[0]
                break

            # If a .egg is found, open the egg and save its path
            if file.endswith('.egg'):
                egg_zip = zipfile.ZipFile(file, 'a')
                egg_path = os.path.split(file)[0]
                break

        # If nothing was found, return
        if egg_path == '':
            return

        # Strip any package prefixes
        if self.root:
            root_len = len(self.root)
            for counter in xrange(len(outputs)):
                outputs[counter] = outputs[counter][root_len:]

        # If egg was not zip-safe, set path to 'EGG-INFO' dir
        if egg_zip is None:
            log_path = os.path.join(egg_path, 'EGG-INFO',
                                    'installed_files.log')
        # Else, set path to the dir that the .egg file is in
        else:
            log_path = os.path.join(egg_path, 'installed_files.log')

        # Create the file in the path set above
        file = open(log_path, 'w')

        # For each installed file, write then on a new line in the log
        for installed_file in outputs:
            file.write(installed_file + '\n')
        file.close()

        # If .egg file was found, put the log file in the egg, then
        # remove the file from the directory the egg is in
        if egg_zip is not None:
            egg_zip.write(log_path, 'EGG-INFO/installed_files.log')
            egg_zip.close()
            os.remove(log_path)


    def process_distribution(self, requirement, dist, deps=True, *info):
        self.update_pth(dist)
        self.package_index.add(dist)
        self.local_index.add(dist)
        self.install_egg_scripts(dist)
        self.installed_projects[dist.key] = dist

        # Add the log of all the installed files to EGG-INFO
        self.add_installed_files_list()
        self.outputs_this_package = []

        log.info(self.installation_report(requirement, dist, *info))
        if dist.has_metadata('dependency_links.txt'):
            self.package_index.add_find_links(
                dist.get_metadata_lines('dependency_links.txt')
            )
        if not deps and not self.always_copy:
            return
        elif requirement is not None and dist.key != requirement.key:
            log.warn("Skipping dependencies for %s", dist)
            return  # XXX this is not the distribution we were looking for
        elif requirement is None or dist not in requirement:
            # if we wound up with a different version, resolve what we've got
            distreq = dist.as_requirement()
            requirement = requirement or distreq
            requirement = Requirement(
                distreq.project_name, distreq.specs, requirement.extras
            )

        log.info("Processing dependencies for %s", requirement)
        try:
            distros = WorkingSet([]).resolve(
                [requirement], self.local_index, self.easy_install
            )
        except DistributionNotFound, e:
            raise DistutilsError(
                "Could not find required distribution %s" % e.args
            )
        except VersionConflict, e:
            raise DistutilsError(
                "Installed distribution %s conflicts with requirement %s"
                % e.args
            )
        if self.always_copy or self.always_copy_from:
            # Force all the relevant distros to be copied or activated
            for dist in distros:
                if dist.key not in self.installed_projects:
                    self.easy_install(dist.as_requirement())
        log.info("Finished processing dependencies for %s", requirement)

        # Run post-install scripts
        self._run_post_install(dist.location)


    def should_unzip(self, dist):
        if self.zip_ok is not None:
            return not self.zip_ok
        if dist.has_metadata('not-zip-safe'):
            return True
        if not dist.has_metadata('zip-safe'):
            return True
        return False

    def maybe_move(self, spec, dist_filename, setup_base):
        dst = os.path.join(self.build_directory, spec.key)
        if os.path.exists(dst):
            log.warn(
               "%r already exists in %s; build directory %s will not be kept",
               spec.key, self.build_directory, setup_base
            )
            return setup_base
        if os.path.isdir(dist_filename):
            setup_base = dist_filename
        else:
            if os.path.dirname(dist_filename)==setup_base:
                os.unlink(dist_filename)   # get it out of the tmp dir
            contents = os.listdir(setup_base)
            if len(contents)==1:
                dist_filename = os.path.join(setup_base,contents[0])
                if os.path.isdir(dist_filename):
                    # if the only thing there is a directory, move it instead
                    setup_base = dist_filename
        ensure_directory(dst)
        shutil.move(setup_base, dst)
        return dst

    def install_wrapper_scripts(self, dist):
        if not self.exclude_scripts:
            for args in get_script_args(dist):
                self.write_script(*args)


    def install_script(self, dist, script_name, script_text, dev_path=None):
        """Generate a legacy script wrapper and install it"""
        spec = str(dist.as_requirement())
        is_script = is_python_script(script_text, script_name)

        if is_script and dev_path:
            script_text = get_script_header(script_text) + (
                "# EASY-INSTALL-DEV-SCRIPT: %(spec)r,%(script_name)r\n"
                "__requires__ = %(spec)r\n"
                "from pkg_resources import require; require(%(spec)r)\n"
                "del require\n"
                "__file__ = %(dev_path)r\n"
                "execfile(__file__)\n"
            ) % locals()
        elif is_script:
            script_text = get_script_header(script_text) + (
                "# EASY-INSTALL-SCRIPT: %(spec)r,%(script_name)r\n"
                "__requires__ = %(spec)r\n"
                "import pkg_resources\n"
                "pkg_resources.run_script(%(spec)r, %(script_name)r)\n"
            ) % locals()
        self.write_script(script_name, script_text, 'b')

    def write_script(self, script_name, contents, mode="t", blockers=()):
        """Write an executable file to the scripts directory"""
        self.delete_blockers(   # clean up old .py/.pyw w/o a script
            [os.path.join(self.script_dir,x) for x in blockers])
        log.info("Installing %s script to %s", script_name, self.script_dir)
        target = os.path.join(self.script_dir, script_name)
        self.add_output(target)

        if not self.dry_run:
            ensure_directory(target)
            f = open(target,"w"+mode)
            f.write(contents)
            f.close()
            chmod(target, 0755)


    def install_eggs(self, spec, dist_filename, tmpdir):
        # .egg dirs or files are already built, so just return them
        if dist_filename.lower().endswith('.egg'):
            return [self.install_egg(dist_filename, tmpdir)]
        elif dist_filename.lower().endswith('.exe'):
            return [self.install_exe(dist_filename, tmpdir)]

        # Anything else, try to extract and build
        setup_base = tmpdir
        if os.path.isfile(dist_filename) and not dist_filename.endswith('.py'):
            unpack_archive(dist_filename, tmpdir, self.unpack_progress)

        elif os.path.isdir(dist_filename):
            setup_base = os.path.abspath(dist_filename)

        if (setup_base.startswith(tmpdir) and   # something we downloaded
            self.build_directory
            and spec is not None):

            setup_base = self.maybe_move(spec, dist_filename, setup_base)

        # Find the setup.py file
        setup_script = os.path.join(setup_base, 'setup.py')

        if not os.path.exists(setup_script):
            setups = glob(os.path.join(setup_base, '*', 'setup.py'))
            if not setups:
                raise DistutilsError(
                    "Couldn't find a setup script in %s" %
                    os.path.abspath(dist_filename)
                )
            if len(setups)>1:
                raise DistutilsError(
                    "Multiple setup scripts in %s" %
                    os.path.abspath(dist_filename)
                )
            setup_script = setups[0]

        # Now run it, and return the result
        if self.editable:
            log.info(self.report_editable(spec, setup_script))
            return []
        else:
            return self.build_and_install(setup_script, setup_base)

    def egg_distribution(self, egg_path):
        if os.path.isdir(egg_path):
            metadata = PathMetadata(egg_path,os.path.join(egg_path,'EGG-INFO'))
        else:
            metadata = EggMetadata(zipimport.zipimporter(egg_path))

        return Distribution.from_filename(egg_path,metadata=metadata)


    def install_egg(self, egg_path, tmpdir):
        destination = os.path.join(self.install_dir,os.path.basename(egg_path))
        destination = os.path.abspath(destination)
        if not self.dry_run:
            ensure_directory(destination)

        dist = self.egg_distribution(egg_path)
        self.check_conflicts(dist)
        if not samefile(egg_path, destination):
            if os.path.isdir(destination) and not os.path.islink(destination):
                dir_util.remove_tree(destination, dry_run=self.dry_run)
            elif os.path.exists(destination):
                self.execute(os.unlink,(destination,),"Removing "+destination)
            uncache_zipdir(destination)
            if os.path.isdir(egg_path):
                if egg_path.startswith(tmpdir):
                    f,m = shutil.move, "Moving"
                else:
                    f,m = shutil.copytree, "Copying"
            elif self.should_unzip(dist):
                self.mkpath(destination)
                f,m = self.unpack_and_compile, "Extracting"
            elif egg_path.startswith(tmpdir):
                f,m = shutil.move, "Moving"
            else:
                f,m = shutil.copy2, "Copying"

            self.execute(f, (egg_path, destination),
                (m+" %s to %s") %
                (os.path.basename(egg_path),os.path.dirname(destination)))

        self.add_output(destination)
        return self.egg_distribution(destination)


    def install_exe(self, dist_filename, tmpdir):
        # See if it's valid, get data
        cfg = extract_wininst_cfg(dist_filename)
        if cfg is None:
            raise DistutilsError(
                "%s is not a valid distutils Windows .exe" % dist_filename
            )
        # Create a dummy distribution object until we build the real distro
        dist = Distribution(None,
            project_name=cfg.get('metadata','name'),
            version=cfg.get('metadata','version'), platform="win32"
        )

        # Convert the .exe to an unpacked egg
        egg_path = dist.location = os.path.join(tmpdir, dist.egg_name()+'.egg')
        egg_tmp  = egg_path+'.tmp'
        egg_info = os.path.join(egg_tmp, 'EGG-INFO')
        pkg_inf = os.path.join(egg_info, 'PKG-INFO')
        ensure_directory(pkg_inf)   # make sure EGG-INFO dir exists
        dist._provider = PathMetadata(egg_tmp, egg_info)    # XXX
        self.exe_to_egg(dist_filename, egg_tmp)

        # Write EGG-INFO/PKG-INFO
        if not os.path.exists(pkg_inf):
            f = open(pkg_inf,'w')
            f.write('Metadata-Version: 1.0\n')
            for k,v in cfg.items('metadata'):
                if k<>'target_version':
                    f.write('%s: %s\n' % (k.replace('_','-').title(), v))
            f.close()
        script_dir = os.path.join(egg_info,'scripts')
        self.delete_blockers(   # delete entry-point scripts to avoid duping
            [os.path.join(script_dir,args[0]) for args in get_script_args(dist)]
        )
        # Build .egg file from tmpdir
        bdist_egg.make_zipfile(
            egg_path, egg_tmp, verbose=self.verbose, dry_run=self.dry_run
        )
        # install the .egg
        return self.install_egg(egg_path, tmpdir)


    def exe_to_egg(self, dist_filename, egg_tmp):
        """Extract a bdist_wininst to the directories an egg would use"""
        # Check for .pth file and set up prefix translations
        prefixes = get_exe_prefixes(dist_filename)
        to_compile = []
        native_libs = []
        top_level = {}
        def process(src,dst):
            s = src.lower()
            for old,new in prefixes:
                if s.startswith(old):
                    src = new+src[len(old):]
                    parts = src.split('/')
                    dst = os.path.join(egg_tmp, *parts)
                    dl = dst.lower()
                    if dl.endswith('.pyd') or dl.endswith('.dll'):
                        parts[-1] = bdist_egg.strip_module(parts[-1])
                        top_level[os.path.splitext(parts[0])[0]] = 1
                        native_libs.append(src)
                    elif dl.endswith('.py') and old!='SCRIPTS/':
                        top_level[os.path.splitext(parts[0])[0]] = 1
                        to_compile.append(dst)
                    return dst
            if not src.endswith('.pth'):
                log.warn("WARNING: can't process %s", src)
            return None
        # extract, tracking .pyd/.dll->native_libs and .py -> to_compile
        unpack_archive(dist_filename, egg_tmp, process)
        stubs = []
        for res in native_libs:
            if res.lower().endswith('.pyd'):    # create stubs for .pyd's
                parts = res.split('/')
                resource = parts[-1]
                parts[-1] = bdist_egg.strip_module(parts[-1])+'.py'
                pyfile = os.path.join(egg_tmp, *parts)
                to_compile.append(pyfile)
                stubs.append(pyfile)
                bdist_egg.write_stub(resource, pyfile)
        self.byte_compile(to_compile)   # compile .py's
        bdist_egg.write_safety_flag(os.path.join(egg_tmp,'EGG-INFO'),
            bdist_egg.analyze_egg(egg_tmp, stubs))  # write zip-safety flag

        for name in 'top_level','native_libs':
            if locals()[name]:
                txt = os.path.join(egg_tmp, 'EGG-INFO', name+'.txt')
                if not os.path.exists(txt):
                    open(txt,'w').write('\n'.join(locals()[name])+'\n')


    def check_conflicts(self, dist):
        """Verify that there are no conflicting "old-style" packages"""

        return dist     # XXX temporarily disable until new strategy is stable
        from imp import find_module, get_suffixes
        from glob import glob

        blockers = []

        # XXX private attr
        names = dict.fromkeys(dist._get_metadata('top_level.txt'))

        exts = {'.pyc':1, '.pyo':1}     # get_suffixes() might leave one out
        for ext,mode,typ in get_suffixes():
            exts[ext] = 1

        for path,files in expand_paths([self.install_dir]+self.all_site_dirs):
            for filename in files:
                base,ext = os.path.splitext(filename)
                if base in names:
                    if not ext:
                        # no extension, check for package
                        try:
                            f, filename, descr = find_module(base, [path])
                        except ImportError:
                            continue
                        else:
                            if f: f.close()
                            if filename not in blockers:
                                blockers.append(filename)
                    elif ext in exts and base!='site':  # XXX ugh
                        blockers.append(os.path.join(path,filename))
        if blockers:
            self.found_conflicts(dist, blockers)

        return dist

    def found_conflicts(self, dist, blockers):
        if self.delete_conflicting:
            log.warn("Attempting to delete conflicting packages:")
            return self.delete_blockers(blockers)

        msg = """\
-------------------------------------------------------------------------
CONFLICT WARNING:

The following modules or packages have the same names as modules or
packages being installed, and will be *before* the installed packages in
Python's search path.  You MUST remove all of the relevant files and
directories before you will be able to use the package(s) you are
installing:

   %s

""" % '\n   '.join(blockers)

        if self.ignore_conflicts_at_my_risk:
            msg += """\
(Note: you can run EasyInstall on '%s' with the
--delete-conflicting option to attempt deletion of the above files
and/or directories.)
""" % dist.project_name
        else:
            msg += """\
Note: you can attempt this installation again with EasyInstall, and use
either the --delete-conflicting (-D) option or the
--ignore-conflicts-at-my-risk option, to either delete the above files
and directories, or to ignore the conflicts, respectively.  Note that if
you ignore the conflicts, the installed package(s) may not work.
"""
        msg += """\
-------------------------------------------------------------------------
"""
        sys.stderr.write(msg)
        sys.stderr.flush()
        if not self.ignore_conflicts_at_my_risk:
            raise DistutilsError("Installation aborted due to conflicts")

    def installation_report(self, req, dist, what="Installed"):
        """Helpful installation message for display to package users"""
        msg = "\n%(what)s %(eggloc)s%(extras)s"
        if self.multi_version and not self.no_report:
            msg += """

Because this distribution was installed --multi-version, before you can
import modules from this package in an application, you will need to
'import pkg_resources' and then use a 'require()' call similar to one of
these examples, in order to select the desired version:

    pkg_resources.require("%(name)s")  # latest installed version
    pkg_resources.require("%(name)s==%(version)s")  # this exact version
    pkg_resources.require("%(name)s>=%(version)s")  # this version or higher
"""
            if self.install_dir not in map(normalize_path,sys.path):
                msg += """

Note also that the installation directory must be on sys.path at runtime for
this to work.  (e.g. by being the application's script directory, by being on
PYTHONPATH, or by being added to sys.path by your code.)
"""
        eggloc = dist.location
        name = dist.project_name
        version = dist.version
        extras = '' # TODO: self.report_extras(req, dist)
        return msg % locals()

    def report_editable(self, spec, setup_script):
        dirname = os.path.dirname(setup_script)
        python = sys.executable
        return """\nExtracted editable version of %(spec)s to %(dirname)s

If it uses setuptools in its setup script, you can activate it in
"development" mode by going to that directory and running::

    %(python)s setup.py develop

See the setuptools documentation for the "develop" command for more info.
""" % locals()

    def run_setup(self, setup_script, setup_base, args):
        sys.modules.setdefault('distutils.command.bdist_egg', bdist_egg)
        sys.modules.setdefault('distutils.command.egg_info', egg_info)

        args = list(args)
        if self.verbose>2:
            v = 'v' * (self.verbose - 1)
            args.insert(0,'-'+v)
        elif self.verbose<2:
            args.insert(0,'-q')
        if self.dry_run:
            args.insert(0,'-n')
        log.info(
            "Running %s %s", setup_script[len(setup_base)+1:], ' '.join(args))
        try:
            run_setup(setup_script, args)
        except SystemExit, v:
            raise DistutilsError("Setup script exited with %s" % (v.args[0],))

    def build_and_install(self, setup_script, setup_base):
        args = ['bdist_egg', '--dist-dir']
        dist_dir = tempfile.mkdtemp(prefix='egg-dist-tmp-',
                                    dir=os.path.dirname(setup_script))
        try:
            args.append(dist_dir)
            self.run_setup(setup_script, setup_base, args)
            all_eggs = Environment([dist_dir])
            eggs = []
            for key in all_eggs:
                for dist in all_eggs[key]:
                    eggs.append(self.install_egg(dist.location, setup_base))
            if not eggs and not self.dry_run:
                log.warn("No eggs found in %s (setup script problem?)",
                    dist_dir)
            return eggs
        finally:
            rm_rf(dist_dir)
            log.set_verbosity(self.verbose) # restore our log verbosity

    def update_pth(self,dist):
        if self.pth_file is None:
            return

        for d in self.pth_file[dist.key]:    # drop old entries
            if self.multi_version or d.location != dist.location:
                log.info("Removing %s from easy-install.pth file", d)
                self.pth_file.remove(d)
                if d.location in self.shadow_path:
                    self.shadow_path.remove(d.location)

        if not self.multi_version:
            if dist.location in self.pth_file.paths:
                log.info(
                    "%s is already the active version in easy-install.pth",
                    dist
                )
            else:
                log.info("Adding %s to easy-install.pth file", dist)
                self.pth_file.add(dist) # add new entry
                if dist.location not in self.shadow_path:
                    self.shadow_path.append(dist.location)

        if not self.dry_run:

            self.pth_file.save()

            if dist.key=='enstaller':
                # Ensure that enstaller itself never becomes unavailable!
                # XXX should this check for latest version?
                filename = os.path.join(self.install_dir, 'Enstaller.pth')
                if os.path.islink(filename): os.unlink(filename)
                f = open(filename, 'wt')
                f.write(self.pth_file.make_relative(dist.location)+'\n')
                f.close()

    def unpack_progress(self, src, dst):
        # Progress filter for unpacking
        log.debug("Unpacking %s to %s", src, dst)
        return dst     # only unpack-and-compile skips files for dry run

    def unpack_and_compile(self, egg_path, destination):
        to_compile = []
        to_chmod = []

        def pf(src,dst):
            if dst.endswith('.py') and not src.startswith('EGG-INFO/'):
                to_compile.append(dst)
                to_chmod.append(dst)
            elif dst.endswith('.dll') or dst.endswith('.so'):
                to_chmod.append(dst)
            self.unpack_progress(src,dst)
            return not self.dry_run and dst or None

        unpack_archive(egg_path, destination, pf)
        self.byte_compile(to_compile)
        if not self.dry_run:
            for f in to_chmod:
                mode = ((os.stat(f)[stat.ST_MODE]) | 0555) & 07755
                chmod(f, mode)

    def byte_compile(self, to_compile):
        from distutils.util import byte_compile
        try:
            # try to make the byte compile messages quieter
            log.set_verbosity(self.verbose - 1)

            byte_compile(to_compile, optimize=0, force=1, dry_run=self.dry_run)
            if self.optimize:
                byte_compile(
                    to_compile, optimize=self.optimize, force=1,
                    dry_run=self.dry_run
                )
        finally:
            log.set_verbosity(self.verbose)     # restore original verbosity


    def no_default_version_msg(self):
        return """bad install directory or PYTHONPATH

You are attempting to install a package to a directory that is not
on PYTHONPATH and which Python does not read ".pth" files from.  The
installation directory you specified (via --install-dir, --prefix, or
the distutils default setting) was:

    %s

and your PYTHONPATH environment variable currently contains:

    %r

Here are some of your options for correcting the problem:

* You can choose a different installation directory, i.e., one that is
  on PYTHONPATH or supports .pth files

* You can add the installation directory to the PYTHONPATH environment
  variable.  (It must then also be on PYTHONPATH whenever you run
  Python and want to use the package(s) you are installing.)

* You can set up the installation directory to support ".pth" files by
  using one of the approaches described here:

  http://peak.telecommunity.com/EasyInstall.html#custom-installation-locations

Proceeding to install.  Please remember that unless you make one of these changes you will not be able to run the installed code.""" % (
        self.install_dir, os.environ.get('PYTHONPATH','')
    )


    def install_site_py(self):
        """Make sure there's a site.py in the target dir, if needed"""

        if self.sitepy_installed:
            return  # already did it, or don't need to

        sitepy = os.path.join(self.install_dir, "site.py")
        source = resource_string(Requirement.parse("enstaller"), "site.py")
        current = ""

        if os.path.exists(sitepy):
            log.debug("Checking existing site.py in %s", self.install_dir)
            current = open(sitepy,'rb').read()
            if not current.startswith('def __boot():'):
                raise DistutilsError(
                    "%s is not an enstaller-generated site.py; please"
                    " remove it." % sitepy
                )

        if current != source:
            log.info("Creating %s", sitepy)
            if not self.dry_run:
                ensure_directory(sitepy)
                f = open(sitepy,'wb')
                f.write(source)
                f.close()
            self.byte_compile([sitepy])

        self.sitepy_installed = True


    INSTALL_SCHEMES = dict(
        posix = dict(
            install_dir = '$base/lib/python$py_version_short/site-packages',
            script_dir  = '$base/bin',
        ),
    )

    DEFAULT_SCHEME = dict(
        install_dir = '$base/Lib/site-packages',
        script_dir  = '$base/Scripts',
    )

    def _expand(self, *attrs):
        config_vars = self.get_finalized_command('install').config_vars

        if self.prefix:
            # Set default install_dir/scripts from --prefix
            config_vars = config_vars.copy()
            config_vars['base'] = self.prefix
            scheme = self.INSTALL_SCHEMES.get(os.name,self.DEFAULT_SCHEME)
            for attr,val in scheme.items():
                if getattr(self,attr,None) is None:
                    setattr(self,attr,val)

        from distutils.util import subst_vars
        for attr in attrs:
            val = getattr(self, attr)
            if val is not None:
                val = subst_vars(val, config_vars)
                if os.name == 'posix':
                    val = os.path.expanduser(val)
                setattr(self, attr, val)


def get_site_dirs():
    # return a list of 'site' dirs
    sitedirs = filter(None,os.environ.get('PYTHONPATH','').split(os.pathsep))
    prefixes = [sys.prefix]
    if sys.exec_prefix != sys.prefix:
        prefixes.append(sys.exec_prefix)
    for prefix in prefixes:
        if prefix:
            if sys.platform in ('os2emx', 'riscos'):
                sitedirs.append(os.path.join(prefix, "Lib", "site-packages"))
            elif os.sep == '/':
                sitedirs.extend([os.path.join(prefix,
                                         "lib",
                                         "python" + sys.version[:3],
                                         "site-packages"),
                            os.path.join(prefix, "lib", "site-python")])
            else:
                sitedirs.extend(
                    [prefix, os.path.join(prefix, "lib", "site-packages")]
                )
            if sys.platform == 'darwin':
                # for framework builds *only* we add the standard Apple
                # locations. Currently only per-user, but /Library and
                # /Network/Library could be added too
                if 'Python.framework' in prefix:
                    home = os.environ.get('HOME')
                    if home:
                        sitedirs.append(
                            os.path.join(home,
                                         'Library',
                                         'Python',
                                         sys.version[:3],
                                         'site-packages'))
    for plat_specific in (0,1):
        site_lib = get_python_lib(plat_specific)
        if site_lib not in sitedirs: sitedirs.append(site_lib)

    sitedirs = map(normalize_path, sitedirs)
    return sitedirs


def expand_paths(inputs):
    """Yield sys.path directories that might contain "old-style" packages"""

    seen = {}

    for dirname in inputs:
        dirname = normalize_path(dirname)
        if dirname in seen:
            continue

        seen[dirname] = 1
        if not os.path.isdir(dirname):
            continue

        files = os.listdir(dirname)
        yield dirname, files

        for name in files:
            if not name.endswith('.pth'):
                # We only care about the .pth files
                continue
            if name in ('easy-install.pth','Enstaller.pth'):
                # Ignore .pth files that we control
                continue

            # Read the .pth file
            f = open(os.path.join(dirname,name))
            lines = list(yield_lines(f))
            f.close()

            # Yield existing non-dupe, non-import directory lines from it
            for line in lines:
                if not line.startswith("import"):
                    line = normalize_path(line.rstrip())
                    if line not in seen:
                        seen[line] = 1
                        if not os.path.isdir(line):
                            continue
                        yield line, os.listdir(line)


def extract_wininst_cfg(dist_filename):
    """Extract configuration data from a bdist_wininst .exe

    Returns a ConfigParser.RawConfigParser, or None
    """
    f = open(dist_filename,'rb')
    try:
        endrec = zipfile._EndRecData(f)
        if endrec is None:
            return None

        prepended = (endrec[9] - endrec[5]) - endrec[6]
        if prepended < 12:  # no wininst data here
            return None
        f.seek(prepended-12)

        import struct, StringIO, ConfigParser
        tag, cfglen, bmlen = struct.unpack("<iii",f.read(12))
        if tag not in (0x1234567A, 0x1234567B):
            return None     # not a valid tag

        f.seek(prepended-(12+cfglen))
        cfg = ConfigParser.RawConfigParser({'version':'','target_version':''})
        try:
            cfg.readfp(StringIO.StringIO(f.read(cfglen).split(chr(0),1)[0]))
        except ConfigParser.Error:
            return None
        if not cfg.has_section('metadata') or not cfg.has_section('Setup'):
            return None
        return cfg

    finally:
        f.close()


def get_exe_prefixes(exe_filename):
    """Get exe->egg path translations for a given .exe file"""

    prefixes = [
        ('PURELIB/', ''), ('PLATLIB/pywin32_system32', ''),
        ('PLATLIB/', ''),
        ('SCRIPTS/', 'EGG-INFO/scripts/')
    ]
    z = zipfile.ZipFile(exe_filename)
    try:
        for info in z.infolist():
            name = info.filename
            parts = name.split('/')
            if len(parts)==3 and parts[2]=='PKG-INFO':
                if parts[1].endswith('.egg-info'):
                    prefixes.insert(0,('/'.join(parts[:2]), 'EGG-INFO/'))
                    break
            if len(parts)<>2 or not name.endswith('.pth'):
                continue
            if name.endswith('-nspkg.pth'):
                continue
            if parts[0].upper() in ('PURELIB','PLATLIB'):
                for pth in yield_lines(z.read(name)):
                    pth = pth.strip().replace('\\','/')
                    if not pth.startswith('import'):
                        prefixes.append((('%s/%s/' % (parts[0],pth)), ''))
    finally:
        z.close()

    prefixes = [(x.lower(),y) for x, y in prefixes]
    prefixes.sort()
    prefixes.reverse()
    return prefixes


def parse_requirement_arg(spec):
    try:
        return Requirement.parse(spec)
    except ValueError:
        raise DistutilsError(
            "Not a URL, existing file, or requirement spec: %r" % (spec,)
        )


class PthDistributions(Environment):
    """A .pth file with Distribution paths in it"""

    dirty = False

    def __init__(self, filename, sitedirs=()):
        self.filename = filename
        self.sitedirs=map(normalize_path, sitedirs)
        self.basedir = normalize_path(os.path.dirname(self.filename))
        self._load()
        Environment.__init__(self, [], None, None)
        for path in yield_lines(self.paths):
            map(self.add, find_distributions(path, True))

    def _load(self):
        self.paths = []
        saw_import = False
        seen = dict.fromkeys(self.sitedirs)
        if os.path.isfile(self.filename):
            for line in open(self.filename,'rt'):
                if line.startswith('import'):
                    saw_import = True
                    continue
                path = line.rstrip()
                self.paths.append(path)
                if not path.strip() or path.strip().startswith('#'):
                    continue
                # skip non-existent paths, in case somebody deleted a package
                # manually, and duplicate paths as well
                path = self.paths[-1] = normalize_path(
                    os.path.join(self.basedir,path)
                )
                if not os.path.exists(path) or path in seen:
                    self.paths.pop()    # skip it
                    self.dirty = True   # we cleaned up, so we're dirty now :)
                    continue
                seen[path] = 1

        if self.paths and not saw_import:
            self.dirty = True   # ensure anything we touch has import wrappers
        while self.paths and not self.paths[-1].strip():
            self.paths.pop()

    def save(self):
        """Write changed .pth file back to disk"""
        if not self.dirty:
            return

        data = '\n'.join(map(self.make_relative,self.paths))
        if data:
            log.debug("Saving %s", self.filename)
            data = (
                "import sys; sys.__plen = len(sys.path)\n"
                "%s\n"
                "import sys, os; new=sys.path[sys.__plen:];"
                " del sys.path[sys.__plen:];"
                " p=getattr(sys,'__egginsert',len(os.environ.get('PYTHONPATH','').split(os.pathsep))); sys.path[p:p]=new;"
                " sys.__egginsert = p+len(new)\n"
            ) % data

            if os.path.islink(self.filename):
                os.unlink(self.filename)
            f = open(self.filename,'wb')
            f.write(data)
            f.close()

        elif os.path.exists(self.filename):
            log.debug("Deleting empty %s", self.filename)
            os.unlink(self.filename)

        self.dirty = False

    def add(self,dist):
        """Add `dist` to the distribution map"""
        if dist.location not in self.paths and dist.location not in self.sitedirs:
            self.paths.append(dist.location)
            self.dirty = True
        Environment.add(self,dist)

    def remove(self,dist):
        """Remove `dist` from the distribution map"""
        while dist.location in self.paths:
            self.paths.remove(dist.location)
            self.dirty = True
        Environment.remove(self,dist)

    def make_relative(self,path):
        npath, last = os.path.split(normalize_path(path))
        baselen = len(self.basedir)
        parts = [last]
        sep = os.altsep=='/' and '/' or os.sep
        while len(npath)>=baselen:
            if npath==self.basedir:
                parts.append(os.curdir)
                parts.reverse()
                return sep.join(parts)
            npath, last = os.path.split(npath)
            parts.append(last)
        else:
            return path


def get_script_header(script_text, executable=sys_executable, wininst=False):
    """Create a #! line, getting options (if any) from script_text"""
    from distutils.command.build_scripts import first_line_re
    first = (script_text+'\n').splitlines()[0]
    match = first_line_re.match(first)
    options = ''
    if match:
        options = match.group(1) or ''
        if options: options = ' '+options
    if wininst:
        executable = "python.exe"
    else:
        executable = nt_quote_arg(executable)
    hdr = "#!%(executable)s%(options)s\n" % locals()
    if unicode(hdr,'ascii','ignore').encode('ascii') != hdr:
        # Non-ascii path to sys.executable, use -x to prevent warnings
        if options:
            if options.strip().startswith('-'):
                options = ' -x'+options.strip()[1:]
            # else: punt, we can't do it, let the warning happen anyway
        else:
            options = ' -x'
    executable = fix_jython_executable(executable, options)
    hdr = "#!%(executable)s%(options)s\n" % locals()
    return hdr


def auto_chmod(func, arg, exc):
    if func is os.remove and os.name=='nt':
        chmod(arg, stat.S_IWRITE)
        return func(arg)
    exc = sys.exc_info()
    raise exc[0], (exc[1][0], exc[1][1] + (" %s %s" % (func,arg)))


def uncache_zipdir(path):
    """Ensure that the importer caches dont have stale info for `path`"""
    from zipimport import _zip_directory_cache as zdc
    _uncache(path, zdc)
    _uncache(path, sys.path_importer_cache)


def _uncache(path, cache):
    if path in cache:
        del cache[path]
    else:
        path = normalize_path(path)
        for p in cache:
            if normalize_path(p)==path:
                del cache[p]
                return


def is_python(text, filename='<string>'):
    "Is this string a valid Python script?"
    try:
        compile(text, filename, 'exec')
    except (SyntaxError, TypeError):
        return False
    else:
        return True


def is_sh(executable):
    """Determine if the specified executable is a .sh (contains a #! line)"""
    try:
        fp = open(executable)
        magic = fp.read(2)
        fp.close()
    except (OSError,IOError): return executable
    return magic == '#!'


def nt_quote_arg(arg):
    """Quote a command line argument according to Windows parsing rules"""

    result = []
    needquote = False
    nb = 0

    needquote = (" " in arg) or ("\t" in arg)
    if needquote:
        result.append('"')

    for c in arg:
        if c == '\\':
            nb += 1
        elif c == '"':
            # double preceding backslashes, then add a \"
            result.append('\\' * (nb*2) + '\\"')
            nb = 0
        else:
            if nb:
                result.append('\\' * nb)
                nb = 0
            result.append(c)

    if nb:
        result.append('\\' * nb)

    if needquote:
        result.append('\\' * nb)    # double the trailing backslashes
        result.append('"')

    return ''.join(result)


def is_python_script(script_text, filename):
    """Is this text, as a whole, a Python script? (as opposed to shell/bat/etc.
    """
    if filename.endswith('.py') or filename.endswith('.pyw'):
        return True     # extension says it's Python
    if is_python(script_text, filename):
        return True     # it's syntactically valid Python
    if script_text.startswith('#!'):
        # It begins with a '#!' line, so check if 'python' is in it somewhere
        return 'python' in script_text.splitlines()[0].lower()

    return False    # Not any Python I can recognize


def fix_jython_executable(executable, options):
    if sys.platform.startswith('java') and is_sh(executable):
        # Workaround Jython's sys.executable being a .sh (an invalid
        # shebang line interpreter)
        if options:
            # Can't apply the workaround, leave it broken
            log.warn(
                "WARNING: Unable to adapt shebang line for Jython,"
                " the following script is NOT executable\n"
                "         see http://bugs.jython.org/issue1112 for"
                " more information.")
        else:
            return '/usr/bin/env %s' % executable

    return executable


def get_script_args(dist, executable=sys_executable, wininst=False):
    """Yield write_script() argument tuples for a distribution's entrypoints"""
    spec = str(dist.as_requirement())
    header = get_script_header("", executable, wininst)
    for group in 'console_scripts', 'gui_scripts':
        for name,ep in dist.get_entry_map(group).items():
            script_text = (
                "# EASY-INSTALL-ENTRY-SCRIPT: %(spec)r,%(group)r,%(name)r\n"
                "__requires__ = %(spec)r\n"
                "import sys\n"
                "from pkg_resources import load_entry_point\n"
                "\n"
                "sys.exit(\n"
                "   load_entry_point(%(spec)r, %(group)r, %(name)r)()\n"
                ")\n"
            ) % locals()
            if sys.platform=='win32' or wininst:
                # On Windows/wininst, add a .py extension and an .exe launcher
                if group=='gui_scripts':
                    ext, launcher = '-script.pyw', 'gui.exe'
                    old = ['.pyw']
                    new_header = re.sub('(?i)python.exe','pythonw.exe', header)
                else:
                    ext, launcher = '-script.py', 'cli.exe'
                    old = ['.py','.pyc','.pyo']
                    new_header = re.sub('(?i)pythonw.exe','python.exe', header)

                if os.path.exists(new_header[2:-1]) or sys.platform!='win32':
                    hdr = new_header
                else:
                    hdr = header
                yield (name+ext, hdr+script_text, 't', [name+x for x in old])
                yield (
                    name+'.exe', resource_string('setuptools', launcher),
                    'b' # write in binary mode
                )
            else:
                # On other platforms, we assume the right thing to do is to
                # just write the stub with no extension.
                yield (name, header+script_text)


def bootstrap():
    # This function is called when enstaller*.egg is run using /bin/sh
    import setuptools

    argv0 = os.path.dirname(setuptools.__path__[0])
    sys.argv[0] = argv0
    sys.argv.append(argv0)
    main()


def main(argv=None, **kw):
    from setuptools import setup
    from setuptools.dist import Distribution
    import distutils.core

    USAGE = """\
usage: %(script)s [options] requirement_or_url ...
   or: %(script)s --help
"""

    def gen_usage (script_name):
        script = os.path.basename(script_name)
        return USAGE % vars()

    def with_ei_usage(f):
        old_gen_usage = distutils.core.gen_usage
        try:
            distutils.core.gen_usage = gen_usage
            return f()
        finally:
            distutils.core.gen_usage = old_gen_usage

    class DistributionWithoutHelpCommands(Distribution):
        common_usage = ""
        def _show_help(self,*args,**kw):
            with_ei_usage(lambda: Distribution._show_help(self,*args,**kw))

    if argv is None:
        argv = sys.argv[1:]

    if '--version' in argv:
        from enstaller import __version__
        print "Enstaller version %s" % __version__
        return

    if '--debug' in argv:
        import setuptools, enstaller
        print "sys.prefix = %s" % sys.prefix
        print "setuptools = %s" % setuptools
        print "enstaller = %s" % enstaller
        print "Enstaller version = %r" % enstaller.__version__
        return

    with_ei_usage(lambda:
        setup(
            script_args = ['-q','easy_install', '-v']+argv,
            script_name = sys.argv[0] or 'easy_install',
            distclass=DistributionWithoutHelpCommands, **kw
        )
    )
