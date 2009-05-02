# Changes library path in object code (ELF and Mach-O).

import re
from os.path import abspath, basename, join, islink, isfile, exists

from utils import rel_prefix


# Extensions which are assumed to belong to files which don't contain
# object code
NO_OBJ = ('.py', '.pyc', '.pyo', '.h', '.a', '.c', '.txt', '.html', '.xml',
          '.png', '.jpg', '.gif')

MAGIC = {
    '\xca\xfe\xba\xbe': 'MachO-universal',
    '\xce\xfa\xed\xfe': 'MachO-i386',
    '\xfe\xed\xfa\xce': 'MachO-ppc',
    '\x7fELF': 'ELF',
}

# List of target direcories where shared object files are found
TARGETS = []


def get_object_type(fpath):
    """
    Return the object file type of the specified file (not link).
    Otherwise, if the file is not an object file return False.
    """
    if fpath.endswith(NO_OBJ) or islink(fpath) or not isfile(fpath):
        return False

    fi = open(fpath, 'rb')
    head = fi.read(4)
    fi.close()

    return MAGIC.get(head, False)


def find_lib(fn):
    for tgt in TARGETS:
        dst = abspath(join(tgt, fn))
        if exists(dst):
            return dst
    print "Error: library %r not found" % fn
    return join('/ERROR/path/not/found', fn)


_PLACEHOLD_PAT = re.compile('/PLACEHOLD' * 5 + '([^\0]*)\0')
def fix_object_code(fpath):
    obj_type = get_object_type(fpath)
    if not obj_type:
        return

    f = open(fpath, 'r+b')
    data = f.read()

    matches = list(_PLACEHOLD_PAT.finditer(data))
    if not matches:
        f.close()
        return

    print "Fixing placeholders in:", rel_prefix(fpath)
    for m in matches:
        gr1 = m.group(1)
        if obj_type.startswith('MachO'):
            r = find_lib(basename(gr1))

        elif obj_type == 'ELF':
            rpaths = [p for p in gr1.split(os.pathsep)
                      if not p.startswith('/PLACEHOLD')]
            rpaths.extend(TARGETS)
            r = os.pathsep.join(rpaths)

        padding = len(m.group(0)) - len(r)
        if padding < 1: # we need at least one nul-character
            raise Exception("placeholder %r too short" % m.group(0))
        r += padding * '\0'
        assert m.start() + len(r) == m.end()

        f.seek(m.start())
        f.write(r)

    f.close()


def fix_files(paths, targets):
    """
    Tries to fix the library path for all object files in the list of files.
    """
    global TARGETS
    TARGETS = targets

    for p in paths:
        fix_object_code(p)