# `augment-structure` cannot target the root of a YANG data structure

**Component:** `pyang/statements.py` (`v_expand_2_augment`) and `pyang/plugins/structure.py`
**Severity:** blocks a valid-looking RFC 8791 construct that libyang accepts
**Reproduced on:** `upstream/master` `3d9cfb0` ("updated tests", 2026-11-11), pyang 2.7.1

## Summary

pyang rejects an `sx:augment-structure` statement whose `path` has a single
component, i.e. one that targets the `sx:structure` node itself rather than a
node nested inside it:

```
error: node test-5-aug-struct-root::doc of type
       ('ietf-yang-structure-ext', 'structure') cannot be target node
```

libyang (`yanglint` 6.4.13) accepts the same statement and places the augmented
children at the top level of the structure, which is what the module author
intends.

The error comes from the generic augment target check in `statements.py`, which
tests the target's keyword against a hardcoded tuple. That tuple cannot contain
`('ietf-yang-structure-ext', 'structure')`, because the keyword is contributed
by a plugin that is loaded after `statements.py`. The RFC 8791 plugin's *own*
target check accepts the statement; it is only the generic one that fails.

A five-line change, in the style of the existing `add_copy_*_keyword` hooks,
makes the construct work. It is given below, with test results.

## Environment

| | |
|---|---|
| pyang | 2.7.1, `upstream/master` `3d9cfb0` |
| Python | 3.13.12 |
| OS | macOS 27.0.1 (Darwin 27.0.0) |
| `ietf-yang-structure-ext` | `@2020-06-17` (as shipped in `modules/ietf/`) |
| libyang / yanglint | 6.4.13 |

## Minimal reproducer

A single self-contained module, with no external dependency beyond
`ietf-yang-structure-ext`. It contains two `augment-structure` statements: the
first targets a node *inside* the structure, the second targets the structure
root.

```yang
module test-5-aug-struct-root {
  yang-version 1.1;
  namespace "urn:example:aug-struct-root";
  prefix ex;

  import ietf-yang-structure-ext {
    prefix sx;
    revision-date 2020-06-17;
  }

  sx:structure doc {
    container payload {
      leaf value {
        type string;
      }
    }
  }

  // Targets a descendant of the structure: accepted by pyang.
  sx:augment-structure "/ex:doc/ex:payload" {
    leaf extra {
      type string;
    }
  }

  // Targets the root of the structure: rejected by pyang,
  // accepted by libyang/yanglint.
  sx:augment-structure "/ex:doc" {
    leaf flag {
      type boolean;
      default "false";
    }
  }
}
```

(Added to the test suite by the patch below, as
`test/test_structure/test-5-aug-struct-root.yang`.)

### Observed

```console
$ pyang -p modules/ietf test-5-aug-struct-root.yang
test-5-aug-struct-root.yang:28: error: node test-5-aug-struct-root::doc of type ('ietf-yang-structure-ext', 'structure') cannot be target node
$ echo $?
1
```

Line 28 is the second `sx:augment-structure`. The first one, at line 20, is
accepted. With `-f tree --tree-print-structures`, `flag` is missing from the
structure, as a consequence of the rejected augment:

```
  structure doc:
    +-- payload
       +-- value?   string
       +-- extra?   string
```

### Expected

`flag` added as a top-level child of the structure, which is what libyang
produces and what the patch below produces:

```
  structure doc:
    +-- payload
    |  +-- value?   string
    |  +-- extra?   string
    +-- flag?      boolean
```

## Cross-implementation check: libyang accepts it

```console
$ yanglint -p . test-5-aug-struct-root.yang
$ echo $?
0
```

libyang compiles both augments and places `flag` at the structure root, exactly
as in the "Expected" tree above.

This is not laxity on libyang's part. It validates `augment-structure` targets
strictly; three deliberately wrong targets each produce a hard error:

| target | yanglint |
|---|---|
| `/sid:nexistepas` | `err: Augment ext-inst target node ... was not found` — exit 1 |
| `/sid:sid-file/sid:nexistepas` | `err: ... was not found` — exit 1 |
| `/sid:sid-file/sid:item/sid:identifier` (a `leaf`) | `err: ... was not found` — exit 1 |

So libyang's acceptance of a single-component path is a deliberate resolution
of the `sx:structure` node as a legitimate augment target, not a missing check.

## Root cause

pyang applies **two** independent target checks to `augment-structure`.

**1. The RFC 8791 plugin's own check — this one passes.**
`pyang/plugins/structure.py:88-94` walks up from the target to its top-level
ancestor and requires it to be a `structure` node:

```python
    target = stmt.i_target_node
    # Try to find toplevel 'structure' node parent
    while (target.parent != None and
           target.parent.keyword not in ('module', 'submodule')):
        target = target.parent
    if target.keyword != (module_name, 'structure'):
        err_add(ctx.errors, stmt.pos, 'BAD_AUGMENT_STRUCTURE_TARGET_NODE', ())
```

When the target *is* the structure, the loop terminates immediately and
`target.keyword == (module_name, 'structure')` holds. No error. The plugin
author's intent is therefore satisfied by a single-component path.

**2. The generic augment check — this one fails.**
`pyang/statements.py:2002-2007`, in `v_expand_2_augment`:

```python
        elif (stmt.i_target_node.keyword not in
              ('container', 'list', 'choice', 'case', 'input',
                  'output', 'notification', '__tmp_augment__')):
            nd = stmt.i_target_node
            err_add(ctx.errors, stmt.pos, 'BAD_TARGET_NODE',
                    (nd.i_module.i_modulename, nd.arg, nd.keyword))
```

The tuple is a literal in core code. An extension keyword is a
`(module-name, keyword)` tuple registered at plugin-init time, so a plugin has
no way to add itself to this list — unlike the data-node, children and config
registries, for which `statements.py` already exposes `add_data_keyword`,
`add_keyword_with_children` and `add_keywords_with_no_explicit_config`, all
three of which `structure.py` already calls.

The hardcoded tuple predates the RFC 8791 plugin; its last change was
`10d5ec8` ("add a `__tmp_augment__` target node").

## Suggested fix

Extract the tuple into a module-level list and add a registration hook, in the
style of the neighbouring `add_copy_uses_keyword` / `add_copy_augment_keyword`;
then have the RFC 8791 plugin register its `structure` keyword.

```diff
--- a/pyang/statements.py
+++ b/pyang/statements.py
@@ -79,6 +79,9 @@ def add_copy_uses_keyword(keyword):
 def add_copy_augment_keyword(keyword):
     _copy_augment_keywords.append(keyword)
 
+def add_augment_target_keyword(keyword):
+    _augment_target_keywords.append(keyword)
+
 def add_xpath_function(name, input_params, output_param):
     xpath.add_extra_xpath_function(name, input_params, output_param)
 
@@ -299,6 +302,11 @@ _copy_uses_keywords = []
 
 _copy_augment_keywords = []
 
+# Keywords that an 'augment' may target.  A plugin that defines an
+# extension holding data definition statements adds its keyword here.
+_augment_target_keywords = ['container', 'list', 'choice', 'case', 'input',
+                            'output', 'notification', '__tmp_augment__']
+
 _refinements = [
@@ -1881,9 +1889,7 @@ def v_expand_2_augment(ctx, stmt):
-        elif (stmt.i_target_node.keyword not in
-              ('container', 'list', 'choice', 'case', 'input',
-                  'output', 'notification', '__tmp_augment__')):
+        elif stmt.i_target_node.keyword not in _augment_target_keywords:
             nd = stmt.i_target_node
             err_add(ctx.errors, stmt.pos, 'BAD_TARGET_NODE',
                     (nd.i_module.i_modulename, nd.arg, nd.keyword))
--- a/pyang/plugins/structure.py
+++ b/pyang/plugins/structure.py
@@ -31,6 +31,8 @@ def pyang_plugin_init():
     statements.add_data_keyword(sx)
     statements.add_keyword_with_children(sx)
     statements.add_keywords_with_no_explicit_config(sx)
+    # RFC 8791 allows an augment-structure to target the structure itself
+    statements.add_augment_target_keyword(sx)
     asx = (module_name, 'augment-structure')
```

Behaviour is unchanged for every keyword already in the list, and for every
plugin that does not call the new hook.

### Test results

All runs on `upstream/master` `3d9cfb0`, with the worktree's own `pyang` forced
onto `sys.path` via `PYTHONPATH`.

| | without patch | with patch |
|---|---|---|
| minimal reproducer | `error: ... cannot be target node`, exit 1 | exit 0, `flag` at structure root |
| `test/test_structure` (pre-existing 4 tests) | 4/4 pass | 4/4 pass |
| `test5`, added by this patch | fails: `flag?` missing from the structure | passes |
| `make selftest` | ok, exit 0 | ok, exit 0 — **output byte-identical** |
| `make mtest` | fails: `iana-if-type.yang:5: error: module "ietf-interfaces" not found in search path` | same failure |

`test-4-bad-aug` keeps failing as designed: its target is a `container` outside
any structure, so the plugin's own check in `structure.py` still rejects it.
The `mtest` failure is pre-existing and unrelated — it reproduces identically
with and without the patch.

The new `test5` is a genuine regression test rather than one that passes by
construction: without the patch its diff shows `flag?` missing from the
structure and `payload`'s children re-indented. Its expected tree was checked
against libyang's compiled `structure doc:` output, which is identical.

## Specification analysis

RFC 8791 does not settle the question, so this is partly a request for a ruling
rather than a pure bug report. The relevant text, verbatim:

**Section 4**, defining the `path` argument — this is the sentence pyang's
behaviour follows:

> The mandatory 'path' parameter value identifies the YANG conceptual data node
> that is being augmented and is represented as an absolute-schema-nodeid
> string, where the first node in the absolute-schema-nodeid string identifies
> the YANG data structure to augment, and the rest of the nodes in the string
> identifies the node within the YANG structure to augment.

For a single-component path, "the rest of the nodes" is empty. The RFC does not
say what that means.

**Section 4**, on where the children land — this supports libyang's reading:

> The XPath document element is the augmented extension statement itself, such
> that the child nodes of the document element are represented by the
> data-def-stmt substatements within the augmented 'structure' statement.

If the document element is the structure itself, then adding children directly
to it is a coherent operation.

**Section 3**, the tree-diagram template, uses a bare structure name rather
than a path:

> augment-structure <structure-name>:

though Appendix A.2's rendered output does use a path
(`augment-structure /exm:address-book/exm:address:`).

Two things are **not** in the RFC:

- any sentence stating whether the root of a YANG data structure may itself be
  augmented;
- any constraint on the target node's keyword. Section 4 constrains only the
  *substatements* of `augment-structure`, as `1*(data-def-stmt / case-stmt)`.

No example anywhere in the document uses a single-component path. The example
inside the extension's own `description`
(`sx:augment-structure /foo:foo-data/foo:foo-con`) and the only Appendix A
instance (`/exm:address-book/exm:address`) are both nested.

So: the RFC leans toward nesting in its wording and its examples, but never
forbids the single-component form, and the "document element" sentence reads
naturally in its favour. Two implementations have diverged on it.

## Real-world impact

`draft-toutain-core-sid-extension` augments the `sid-file` structure of
`ietf-sid-file` (RFC 9595) at two levels: a per-item `choice` for type and key
information, and a `sid-extensions` flag in the file header. The header flag has
to attach to the structure root — a `.sid` file's header has no intermediate
container to hang it on:

```yang
  sx:augment-structure "/sid:sid-file" {
    leaf sid-extensions {
      type boolean;
      default "false";
    }
  }
```

With unpatched pyang:

```
ietf-sid-file-extension@2026-11-12.yang:191: error: node ietf-sid-file::sid-file of type ('ietf-yang-structure-ext', 'structure') cannot be target node
ietf-sid-file-extension@2026-11-12.yang:212: warning: node "ietf-sid-file-extension::sid-extensions" is not found in "ietf-sid-file::sid-file"
```

The second message is a consequence of the first: the augment having been
rejected, the leaf does not exist for the `when` expression that references it.
With the patch, the module compiles cleanly (exit 0), as it already does under
yanglint.

To be precise about the scope: this does **not** break any tooling that
produces or consumes `.sid` files. pyang's own `sid` plugin writes the extension
members as literal JSON member names and never loads
`ietf-sid-file-extension`, so `--sid-generate-file`, `--sid-check-file` and
`--sid-update-file` all work unpatched, and the resulting files validate against
the extended schema under a third implementation (yangson). What the rejection
breaks is **module validation**: pyang is one of the validators the IETF
datatracker runs over every module in a submitted draft, so an RFC-track module
that pyang rejects is flagged there. That is a practical problem independently
of how the ambiguity is resolved, which is why it would help to have the
project's position recorded here.

---

# Appendix A — separate bug: libyang's tree printer duplicates augment bodies

Not a pyang bug; filed here only because it misled the investigation and
because `yanglint -f tree` cannot be trusted as a cross-check on these modules.

Given the minimal reproducer above, `yanglint -f tree` prints the body of the
**first** `augment-structure` under **every** `augment-structure` header:

```
  structure doc:                        <- correct
    +-- payload
    |  +-- value?   string
    |  +-- extra?   string
    +-- flag?      boolean
  augment-structure /ex:doc/ex:payload:
    +-- extra?   string
  augment-structure /ex:doc:
    +-- extra?   string                 <- wrong, should be "flag? boolean"
```

Swapping the order of the two `augment-structure` statements in the source
swaps which body is duplicated, which confirms the diagnosis. The compiled
`structure doc:` section is correct in both orders, so the defect is confined
to the per-augment echo in the printer. pyang's `-f tree
--tree-print-structures` renders both augments correctly.

# Appendix B — non-portable `when` in the SID extension module

Not a tool bug: a module-side issue, recorded because it was found by the same
comparison and is the one thing the draft should change.

The `when` on the per-item `choice` uses an absolute path:

```yang
  when "/sid:sid-file/sid-ext:sid-extensions = 'true'";
```

Inside a YANG data structure, the structure root is not a datastore root, and
the two implementations disagree about what an absolute path means there — as
Section 4's "document element" sentence would predict. Measured results:

| `when` expression | yanglint 6.4.13 | pyang (patched) |
|---|---|---|
| `/sid:sid-file/sid-ext:sid-extensions = 'true'` | warn: `Schema node "sid-file" for parent "<root>" not found` | clean |
| `/sid-ext:sid-extensions = 'true'` | warn: `Schema node "sid-extensions" for parent "<root>" not found` | warn: node not found in module |
| `../sid-ext:sid-extensions = 'true'` | **clean** | **clean** |

Only the relative form is accepted by both. It is also correct per RFC 7950
§7.21.5: for a `when` that is a substatement of a `choice`, the context node is
the closest ancestor data node — here the `item` list, which libyang confirms
in its diagnostic (`with context node "/ietf-sid-file:sid-file/item"`) — so
`..` denotes the `sid-file` structure and `../sid-ext:sid-extensions` resolves.

Recommendation: use the relative form.
