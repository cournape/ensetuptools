import sys
import re
import os
import bz2
import string
import hashlib
import zipfile
from collections import defaultdict
from os.path import abspath, basename, join, isfile

from egginst.utils import human_bytes

_verbose = False


def parse_index(data):
    """
    Given the bz2 compressed data of an index file, return a dictionary
    mapping the distribution names to the content of the cooresponding
    section.
    """
    data = bz2.decompress(data)

    d = defaultdict(list)
    sep_pat = re.compile(r'==>\s*(\S+)\s*<==')
    for line in data.splitlines():
        m = sep_pat.match(line)
        if m:
            fn = m.group(1)
            continue
        d[fn].append(line.rstrip())

    res = {}
    for fn in d.iterkeys():
        res[fn] = '\n'.join(d[fn])
    return res


def metadata_from_spec(spec):
    """
    Given a spec dictionary, returns a the spec file as a well formed string.
    Also this function is a reference for metadata version 1.1
    """
    str_None = str, type(None)
    for var, typ in [
        ('name', str), ('version', str), ('build', int),
        ('arch', str_None), ('platform', str_None), ('osdist', str_None),
        ('python', str_None), ('packages', list)]:
        assert isinstance(spec[var], typ), spec
        if isinstance(spec[var], str):
            s = spec[var]
            assert s == s.strip(), spec
            assert s != '', spec
    assert spec['build'] > 0

    for req in spec['packages']:
        assert isinstance(req, str), req

    lst = ["""\
metadata_version = '1.1'
name = %(name)r
version = %(version)r
build = %(build)i

arch = %(arch)r
platform = %(platform)r
osdist = %(osdist)r
python = %(python)r""" % spec]

    if spec['packages']:
        lst.append('packages = [')
        deps = spec['packages']
        for req in sorted(deps, key=string.lower):
            lst.append("  %r," % req)
        lst.append(']')
    else:
        lst.append('packages = []')

    lst.append('')
    return '\n'.join(lst)


def parse_metadata(data, index):
    """
    Given the content of a dependency spec file, return a dictionary mapping
    the variables to their values.
    """
    spec = {}
    exec data in spec
    assert spec['metadata_version'] in ('1.0', '1.1'), spec

    var_names = [ # these must be present
        'metadata_version', 'name', 'version', 'build',
        'arch', 'platform', 'osdist', 'python', 'packages']
    if index:
        # An index spec also has these
        var_names.extend(['md5', 'size'])
        assert isinstance(spec['md5'], str) and len(spec['md5']) == 32
        assert isinstance(spec['size'], int)

    if spec['metadata_version'] == '1.0':
        # convert 1.0 -> 1.1
        spec['metadata_version'] = '1.1'

        assert spec['filename'].endswith('.egg')
        dum, v, b = spec['filename'][:-4].split('-')
        assert v == spec['version']
        assert b >= 1
        spec['build'] = int(b)
        pkgs = spec['packages']
        spec['packages'] = [name + " " + pkgs[name]
                            for name in sorted(pkgs, key=string.lower)]
    res = {}
    for name in var_names:
        res[name] = spec[name]
    return res


def parse_depend_index(data):
    """
    Given the data of index-depend.bz2, return a dict mapping each distname
    to a dict mapping variable names to their values.
    """
    d = parse_index(data)
    for fn in d.iterkeys():
        d[fn] = parse_metadata(d[fn], index=True)
    return d


def canonical(s):
    """
    Return a canonical representations of a project name.  This is used
    for finding matches.
    """
    s = s.lower()
    s = s.replace('-', '_')
    if s == 'tables':
        s = 'pytables'
    return s


class Req(object):
    def __init__(self, string):
        for c in '<>=':
            assert c not in string, string
        lst = string.replace(',', ' ').split()
        self.name = canonical(lst[0])
        assert '-' not in self.name
        self.versions = sorted(lst[1:])
        if any('-' in v for v in self.versions):
            assert len(self.versions) == 1
            assert '-' in self.versions[0]
            self.strict = True
        else:
            self.strict = False

    def matches(self, spec):
        """
        Returns True if the spec of a distribution matches the requirement
        (self).  That is, the canonical name must match, and the version
        must be in the list of required versions.
        """
        assert spec['metadata_version'] == '1.1', spec
        if canonical(spec['name']) != self.name:
            return False
        if self.versions == []:
            return True
        if self.strict:
            return '%(version)s-%(build)i' % spec == self.versions[0]
        return spec['version'] in self.versions

    def __repr__(self):
        tmp = '%s %s' % (self.name, ', '.join(self.versions))
        return 'Req(%r)' % tmp.strip()

    def __cmp__(self, other):
        assert isinstance(other, Req)
        return cmp(repr(self), repr(other))


def add_Reqs(spec):
    """
    add the 'Reqs' key to a spec dictionary.
    """
    spec['Reqs'] = set(Req(s) for s in spec['packages'])


_old_version_pat = re.compile(r'(\S+?)(n\d+)$')
def split_old_version(version):
    """
    Return tuple(version, build) for an old 'n' version.
    """
    m = _old_version_pat.match(version)
    if m is None:
        return version, None
    return m.group(1), int(m.group(2)[1:])

def split_old_eggname(eggname):
    assert basename(eggname) == eggname and eggname.endswith('.egg')
    name, old_version = eggname[:-4].split('-')[:2]
    version, build = split_old_version(old_version)
    assert build is not None
    return name, version, build

def get_version_build(dist):
    """
    Return the verion and build number of an old style "n" egg, as a
    tuple(version, build), where version is a string and build is an integer.
    """
    eggname = basename(dist)
    return split_old_eggname(eggname)[1:]


def download_data(url, size):
    """
    Downloads data from the url, returns the data as a string.
    """
    from setuptools.package_index import open_with_auth

    if _verbose:
        print "downloading data from: %r" % url
    if size:
        sys.stdout.write('%9s [' % human_bytes(size))
        sys.stdout.flush()
        cur = 0

    handle = open_with_auth(url)
    data = []

    if size and size < 16384:
        buffsize = 1
    else:
        buffsize = 256

    while True:
        chunk = handle.read(buffsize)
        if not chunk:
            break
        data.append(chunk)
        if not size:
            continue
        rat = float(buffsize) * len(data) / size
        if rat * 64 >= cur:
            sys.stdout.write('.')
            sys.stdout.flush()
            cur += 1

    if size:
        sys.stdout.write(']\n')
        sys.stdout.flush()

    data = ''.join(data)
    handle.close()
    if _verbose:
        print "downloaded %i bytes" % len(data)

    return data


def get_data_from_url(url, md5=None, size=None):
    """
    Get data from a url and check optionally check the MD5.
    """
    if url.startswith('file://'):
        index_path = url[7:]
        data = open(index_path).read()

    elif url.startswith('http://'):
        data = download_data(url, size)

    else:
        raise Exception("Not valid url: " + url)

    if md5 is not None and hashlib.md5(data).hexdigest() != md5:
        sys.stderr.write("FATAL ERROR: Data received from\n\n"
                         "    %s\n\n"
                         "is corrupted.  MD5 sums mismatch.\n" % url)
        sys.exit(1)

    return data



class IndexedRepo(object):

    def __init__(self, verbose=False):
        """
        Initialize the index.
        """
        global _verbose
        self.verbose = _verbose = verbose

        # Local directory
        self.local = '.'

        # chain of repos, either local or remote, from which distributions
        # may be fetched, the local directory is always first.
        self.chain = ['local:/']

        # maps distributions to specs
        self.index = {}

    def add_repo(self, repo):
        """
        Add a repo to the list of extra repos, i.e. read the index file of
        the url, parse it and update the index.
        """
        if self.verbose:
            print "Adding repo:", repo
        assert repo.endswith('/'), repo

        data = get_data_from_url(repo + 'index-depend.bz2')

        new_index = parse_depend_index(data)
        for spec in new_index.itervalues():
            add_Reqs(spec)

        self.chain.append(repo)

        for distname, spec in new_index.iteritems():
            self.index[repo + distname] = spec

    def get_matches_repo(self, req, repo):
        """
        Return the set of distributions which match the requirement from the
        repository.  That is, all distribution which match the requirement.
        """
        matches = set()
        for dist, spec in self.index.iteritems():
            if dist.startswith(repo) and req.matches(spec):
                assert dist not in matches
                matches.add(dist)
        return matches

    def get_matches(self, req):
        """
        Return the set of distributions which match the requirement from the
        first repository in the chain which contains at least one match.
        """
        for repo in self.chain:
            matches = self.get_matches_repo(req, repo)
            if matches:
                return matches
        # no matching distributions are found in any repo
        return None

    def get_dist(self, req):
        """
        Return the distributions with the largest version and build number
        from the first repository which contains any matches.
        """
        matches = self.get_matches(req)
        if matches is None:
            print 'Warning: No distribution found for', req
            # no matching distributions were found in any repo
            return None
        # found matches, return the one with largest (version, build)
        lst = sorted(matches, key=get_version_build)
        return lst[-1]

    def fetch_dist(self, req):
        """
        Get a distribution, i.e. copy the distribution into the local
        repo, according to how the chain is resolved.
        """
        dist = self.get_dist(req)
        if dist is None:
            raise Exception("no distribution found for %r" % req)

        if dist.startswith('local:/'):
            if self.verbose:
                print "Nothing to do for:", dist
            return

        data = get_data_from_url(dist,
                                 self.index[dist]['md5'],
                                 self.index[dist]['size'])

        dst = join(self.local, basename(dist))
        if self.verbose:
            print "Copying %r to %r" % (dist, dst)
        fo = open(dst, 'wb')
        fo.write(data)
        fo.close()

    def reqs_dist(self, dist):
        """
        Return the requirement objects (as a sorted list) which are a
        required by the distribution.
        """
        return sorted(self.index[dist]['Reqs'])

    def dist_as_req(self, dist, strict=False):
        """
        Return the distribution in terms of the a requirement object.
        That is: What requirement gives me the distribution?
        Which is different from the method reqs_dist above.
        """
        spec = self.index[dist]
        tmp = '%(name)s %(version)s' % spec
        if strict:
            tmp += '-%(build)i' % spec
        return Req(tmp)

    def append_deps(self, dists, dist):
        """
        Append distributions required by (the distribution) 'dist' to the list
        recursively.
        """
        # first we need to know what the requirements of 'dist' are, we sort
        # them to because we want the list of distributions to be
        # deterministic.
        for r in self.reqs_dist(dist):
            # This is the distribution we finally want to append
            d = self.get_dist(r)

            # if the distribution 'd' is already in the list, we have already
            # added it (and it's dependencies) earlier.
            if d in dists:
                continue

            # Append dependencies of the 'd', before 'd' itself.
            self.append_deps(dists, d)

            # Make sure we've only added dependencies and not 'd' itself, which
            # could happen if there a loop is the dependency tree.
            assert d not in dists

            # Append the distribution itself.
            dists.append(d)

    def install_order(self, req):
        """
        Return the list of distributions which need to be installed to meet
        the requirement.
        The returned list is given in dependency order, i.e. the distributions
        can be installed in this order without any package being installed
        before its dependencies got installed.
        """
        # This is the actual distribution we append at the end
        d = self.get_dist(req)

        # Start with no distributions and add all dependencies of the required
        # distribution first.
        dists = []
        self.append_deps(dists, d)

        # dists now has all dependencies, before adding the required
        # distribution itself, we make sure it is not listed already.
        assert d not in dists
        dists.append(d)

        return dists

    def add_dist(self, filename):
        """
        Add an unindexed distribution, which must already exist in the local
        repository, to the index (in memory).  Note that the index file on
        disk remains untouched.
        """
        if self.verbose:
            print "Adding %r to index" % filename

        if filename != basename(filename):
            raise Exception("base filename expected, got %r" % filename)

        arcname = 'EGG-INFO/spec/depend'
        z = zipfile.ZipFile(join(self.local, filename))
        if arcname not in z.namelist():
            z.close()
            raise Exception("arcname=%r does not exist in %r" %
                            (arcname, filename))

        spec = parse_metadata(z.read(arcname), index=False)
        z.close()
        add_Reqs(spec)
        self.index['local:/' + filename] = spec

    def test(self, assert_files_exist=False):
        """
        Test the content of the repo for consistency.
        """
        allreqs = defaultdict(int)

        for fn in sorted(self.index.keys(), key=string.lower):
            if self.verbose:
                print fn

            if assert_files_exist:
                dist_path = join(self.local, fn)
                assert isfile(dist_path), dist_path

            spec = self.index[fn]
            for r in spec['Reqs']:
                allreqs[r] += 1
                d = self.get_dist(r)
                if self.verbose:
                    print '\t', r, '->', self.get_dist(r)
                assert isinstance(r.versions, list) and r.versions
                assert all(v == v.strip() for v in r.versions)
                assert d in self.index

            r = Req('%(name)s %(version)s' % spec)
            assert self.dist_as_req(fn) == r
            assert self.get_dist(r)
            if self.verbose:
                print

        if self.verbose:
            print 70 * '='
        print "Index has %i distributions" % len(self.index)
        print "The following distributions are not required anywhere:"
        for fn, spec in self.index.iteritems():
            if not any(r.matches(spec) for r in allreqs):
                print '\t%s' % fn
        print 'OK'


def main():
    from optparse import OptionParser

    p = OptionParser(
        usage="usage: %prog [options] [REQUIREMENT]",
        prog=basename(sys.argv[0]),
        description="queries and tests a repository")

    p.add_option('-u', "--url",
        action="store",
        default="",
        help="repo url to look at")

    p.add_option('-d', "--dir",
        action="store",
        default="",
        help="local repo to look at, i.e. the directory path")

    p.add_option('-l', "--list",
        action="store_true",
        default=False,
        help="List the requirements the distribution meeting REQUIREMENTS")

    p.add_option('-t', "--test",
        action="store_true",
        default=False,
        help="test if repo is self contained")

    opts, args = p.parse_args()

    ir = IndexedRepo()
    if opts.dir:
        ir.add_repo('file://' + abspath(opts.dir) + '/')

    if opts.url:
        ir.add_repo(opts.url.rstrip('/') + '/')

    if opts.test:
        ir.test()
        return

    if not args:
        print "nothing to do"
        return

    # query for a requirement
    req = req_from_string(args[0])
    if opts.list:
        # list
        dist = ir.get_dist(req)
        for r in ir.reqs_dist(dist):
            print r
    else:
        # install order
        for fn in ir.install_order(req):
            print fn


if __name__ == '__main__':
    main()
