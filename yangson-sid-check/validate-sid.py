#!/usr/bin/env python3
"""Validate a .sid file against ietf-sid-file + ietf-sid-file-extension, using yangson.

yangson does not implement RFC 8791, so an sx:structure is invisible to it.
This script builds a throw-away "shim" copy of the two modules in which:

  * 'sx:structure sid-file { uses sid-file-contents; }' becomes the wrapper
    container that RFC 9595 already provides in 'grouping sid-file', marked
    'config false' so that keyless lists stay legal;
  * each 'sx:augment-structure' becomes a plain 'augment'.

The module name and namespace are preserved, so the top-level JSON member stays
'ietf-sid-file:sid-file' and the extension members keep their
'ietf-sid-file-extension:' prefix: the .sid file is validated exactly as pyang
emits it.

Caveat: in the shim the structure is a real top-level container, so an absolute
'when' such as "/sid:sid-file/..." resolves here even though libyang rejects it
on the real structure. This script therefore does not test that portability
problem.

Usage:
    validate-sid.py <file.sid> [-m <module-dir>] [-e <extension-module.yang>]
    validate-sid.py <file.sid> ... --negative     # run the mutation suite
"""
import argparse
import copy
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

SID_MOD = 'ietf-sid-file@2024-07-31.yang'
TYPES_MOD = 'ietf-yang-types'
SX_MOD = 'ietf-yang-structure-ext@2020-06-17.yang'

STRUCT_RE = re.compile(
    r'sx:structure\s+sid-file\s*\{\s*uses\s+sid-file-contents\s*;\s*\}')
STRUCT_NEW = ('container sid-file {\n    config false;\n'
              '    uses sid-file-contents;\n  }')


def build_shim(dest, module_dir, ext_module):
    """Write the shim modules and the YANG library into 'dest'."""
    module_dir = Path(module_dir)
    src = module_dir / SID_MOD
    text = src.read_text()
    text, n = STRUCT_RE.subn(STRUCT_NEW, text)
    if n != 1:
        sys.exit("could not rewrite the sx:structure in %s" % src)
    (dest / SID_MOD).write_text(text)

    shutil.copy(module_dir / SX_MOD, dest / SX_MOD)
    types = sorted(module_dir.glob(TYPES_MOD + '@*.yang'))
    if not types:
        sys.exit("no %s@*.yang in %s" % (TYPES_MOD, module_dir))
    shutil.copy(types[-1], dest / types[-1].name)
    types_rev = types[-1].stem.split('@')[1]

    modules = [
        {"name": "ietf-sid-file", "revision": "2024-07-31",
         "namespace": "urn:ietf:params:xml:ns:yang:ietf-sid-file",
         "conformance-type": "implement"},
        {"name": "ietf-yang-structure-ext", "revision": "2020-06-17",
         "namespace": "urn:ietf:params:xml:ns:yang:ietf-yang-structure-ext",
         "conformance-type": "import"},
        {"name": TYPES_MOD, "revision": types_rev,
         "namespace": "urn:ietf:params:xml:ns:yang:" + TYPES_MOD,
         "conformance-type": "import"},
    ]

    if ext_module:
        ext = Path(ext_module)
        etext = ext.read_text().replace('sx:augment-structure ', 'augment ')
        (dest / ext.name).write_text(etext)
        name, _, rev = ext.stem.partition('@')
        modules.append(
            {"name": name, "revision": rev,
             "namespace": "urn:ietf:params:xml:ns:yang:" + name,
             "conformance-type": "implement"})

    (dest / 'ylib.json').write_text(json.dumps(
        {"ietf-yang-library:modules-state":
         {"module-set-id": "sid-shim", "module": modules}}, indent=2))


def load(dest):
    from yangson import DataModel
    return DataModel.from_file(str(dest / 'ylib.json'), [str(dest)])


def check(dm, raw):
    from yangson.enumerations import ContentType, ValidationScope
    try:
        dm.from_raw(raw).validate(scope=ValidationScope.all,
                                  ctype=ContentType.all)
        return None
    except Exception as exc:
        return "%s: %s" % (type(exc).__name__, str(exc)[:120])


EXT = 'ietf-sid-file-extension:'


def mutations(base):
    """Faults that a conforming implementation must reject."""
    def sf(r):
        return r['ietf-sid-file:sid-file']

    def first(r, key):
        for it in sf(r)['item']:
            if key in it:
                return it
        return None

    out = {}

    def add(label, fn, needs=None):
        r = copy.deepcopy(base)
        if needs:
            it = first(r, needs)
            if it is None:
                return
            fn(r, it)
        else:
            fn(r, None)
        out[label] = r

    add("type and keys in the same item (choice)",
        lambda r, it: it.update({EXT + 'keys': ["60010"]}), EXT + 'type')
    add("keys as a JSON number, not a string (RFC 7951)",
        lambda r, it: it.update({EXT + 'keys': [60010]}), EXT + 'keys')
    add("base-type: unknown identity",
        lambda r, it: it[EXT + 'type'][0].update(
            {'base-type': EXT + 'not-a-type'}), EXT + 'type')
    add("sid-extensions as a string, not a boolean",
        lambda r, _: sf(r).update({EXT + 'sid-extensions': "true"}))
    add("flag removed while extensions remain (when)",
        lambda r, _: sf(r).pop(EXT + 'sid-extensions', None))
    add("enumerate on a string base-type (inner when)",
        lambda r, it: it[EXT + 'type'][0].update(
            {'base-type': EXT + 'string',
             'enumerate': [{"enum-value": 0, "enum-name": "x"}]}),
        EXT + 'type')
    add("duplicate enum-value (list key)",
        lambda r, it: it[EXT + 'type'][0].update(
            {'base-type': EXT + 'enumeration',
             'enumerate': [{"enum-value": 1, "enum-name": "a"},
                           {"enum-value": 1, "enum-name": "b"}]}),
        EXT + 'type')
    add("enum-name missing (mandatory true)",
        lambda r, it: it[EXT + 'type'][0].update(
            {'base-type': EXT + 'enumeration',
             'enumerate': [{"enum-value": 1}]}), EXT + 'type')
    add("base-type missing (mandatory true)",
        lambda r, it: it[EXT + 'type'][0].pop('base-type', None),
        EXT + 'type')
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('sid', nargs='+', help='.sid file(s) to validate')
    ap.add_argument('-m', '--module-dir',
                    default=str(Path(__file__).resolve().parents[1]
                                / 'modules' / 'ietf'),
                    help='directory holding ietf-sid-file and friends')
    ap.add_argument('-e', '--extension',
                    help='ietf-sid-file-extension@<rev>.yang')
    ap.add_argument('--negative', action='store_true',
                    help='also check that known faults are rejected')
    args = ap.parse_args()

    rc = 0
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp)
        build_shim(dest, args.module_dir, args.extension)
        dm = load(dest)
        for path in args.sid:
            raw = json.load(open(path))
            err = check(dm, raw)
            print("%-44s %s" % (Path(path).name, err or "VALID"))
            if err:
                rc = 1
                continue
            if args.negative:
                muts = mutations(raw)
                if not muts:
                    print("    (no extension members: nothing to mutate)")
                    continue
                caught = 0
                for label, mutated in muts.items():
                    merr = check(dm, mutated)
                    print("    %-52s %s" % (
                        label, "rejected" if merr else "*** NOT DETECTED ***"))
                    caught += 1 if merr else 0
                print("    %d/%d faults rejected" % (caught, len(muts)))
                if caught != len(muts):
                    rc = 1
    return rc


if __name__ == '__main__':
    sys.exit(main())
