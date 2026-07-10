"""
aoh2codec.py — Core read/write engine for AoH2 (Age of History II) save files.

AoH2 save files are raw Java Object Serialization streams (the format
produced by java.io.ObjectOutputStream). This module wraps javaobj-py3's
v3 parser/writer, which is the only variant of that library that round-trips
byte-identically (v1's dumps() is marked "WIP" upstream and corrupts nested
collections - confirmed by testing).

Design principle: the live parsed object graph (javaobj.v3 JavaInstance /
JavaList tree) IS the source of truth. We never flatten-then-rebuild from
JSON, because that risks losing the handle-based shared-reference structure
(e.g. the same Civ or Province object being pointed to from two different
lists, like the ownership list and a war's territory list). Edits happen
directly on the live tree; saving just re-dumps() that same tree.
"""

from __future__ import annotations

import io
import os
import shutil
import datetime
from dataclasses import dataclass
from typing import Any, Iterator

import javaobj.v3 as j3
from javaobj.v3.beans import JavaInstance, JavaField

JAVA_SERIAL_MAGIC = b"\xac\xed\x00\x05"

def is_java_serialized(filepath: str) -> bool:
    """Check the magic bytes (AC ED 00 05) to see if a file is a Java
    Object Serialization stream, without trying to fully parse it."""
    try:
        with open(filepath, "rb") as f:
            return f.read(4) == JAVA_SERIAL_MAGIC
    except OSError:
        return False


def scan_project(folder: str) -> list[str]:
    """Walk a folder (an extracted/unzipped AoH2 save) and return paths of
    every file that looks like a Java serialization stream. AoH2 save
    folders mix these with plain text/config files, so we filter by magic
    bytes rather than assuming a naming convention."""
    found = []
    for root, _dirs, files in os.walk(folder):
        for name in files:
            if ".bak_" in name:  # skip our own timestamped backups
                continue
            path = os.path.join(root, name)
            if is_java_serialized(path):
                found.append(path)
    return sorted(found)


def load_file(filepath: str) -> Any:
    """Decode a single save file into a live javaobj.v3 object graph."""
    with open(filepath, "rb") as f:
        data = f.read()
    return j3.loads(data)


def dump_to_bytes(root: Any) -> bytes:
    """Re-encode a live object graph to Java serialization bytes.

    CRITICAL: this calls sync_collections() first. javaobj.v3's writer does
    NOT serialize a JavaList from its live Python list contents — it replays
    the `annotations` dict captured at parse time (writer.py line ~449:
    `for ann in instance.annotations.get(cd, [])`). So without syncing,
    list mutations (remove a war, append an element) silently vanish from
    the output file even though the in-memory list looks correct. This is
    the exact silent-loss failure mode we hit before; the sync layer is
    what makes structural edits (not just field edits) actually stick.
    """
    sync_collections(root)
    return j3.dumps(root)


def sync_collections(root: Any) -> int:
    """Walk the whole object graph and rebuild every collection's
    `annotations` entry from its live Python contents, so the writer emits
    what the user actually sees/edited. Returns number of collections synced.

    Java's collection classes serialize via writeObject() like this:
      ArrayList:  a `size` int FIELD, then annotations = [capacity, elem...]
      LinkedList: no fields, annotations = [BlockData(size int), elem...]
      HashSet:    no meaningful fields, annotations = [BlockData(capacity,
                  loadFactor, size), elem...]
      HashMap:    threshold/loadFactor fields, annotations =
                  [BlockData(buckets, size), k0, v0, k1, v1, ...]
    We rebuild each accordingly. For ArrayList we keep the original capacity
    annotation (Java's readObject reads and discards it in modern JDKs, and
    AoH2 runs on a modern-enough JVM; size correctness is what matters).
    """
    import struct as _struct
    from javaobj.v3.beans import BlockData
    from javaobj.v3.transformers import JavaList, JavaMap, JavaSet

    synced = 0
    seen: set[int] = set()
    stack = [root]
    while stack:
        node = stack.pop()
        oid = id(node)
        if oid in seen:
            continue
        seen.add(oid)

        if isinstance(node, JavaList):
            for cd in list(node.annotations.keys()):
                ann = node.annotations[cd]
                if cd.name == "java.util.ArrayList":
                    head = ann[0] if ann else BlockData(_struct.pack(">i", len(node)))
                    node.annotations[cd] = [head] + list(node)
                    _set_size_field(node, len(node))
                    synced += 1
                elif cd.name == "java.util.LinkedList":
                    node.annotations[cd] = (
                        [BlockData(_struct.pack(">i", len(node)))] + list(node)
                    )
                    synced += 1
            stack.extend(node)

        elif isinstance(node, JavaMap):
            for cd in list(node.annotations.keys()):
                ann = node.annotations[cd]
                if cd.name in JavaMap.HANDLED_CLASSES and ann:
                    head = ann[0]
                    # HashMap's leading BlockData is (buckets:int, size:int);
                    # patch the size (last 4 bytes) to the live dict length.
                    if isinstance(head, BlockData) and len(head.data) >= 8:
                        head = BlockData(
                            head.data[:-4] + _struct.pack(">i", len(node)),
                            head.handle,
                        )
                    flat = []
                    for k, v in node.items():
                        flat.append(k)
                        flat.append(v)
                    node.annotations[cd] = [head] + flat
                    synced += 1
            stack.extend(node.keys())
            stack.extend(node.values())

        elif isinstance(node, JavaSet):
            for cd in list(node.annotations.keys()):
                ann = node.annotations[cd]
                if ann:
                    head = ann[0]
                    # HashSet's leading BlockData is (capacity:int,
                    # loadFactor:float, size:int) — size is the last 4 bytes.
                    if isinstance(head, BlockData) and len(head.data) >= 12:
                        head = BlockData(
                            head.data[:-4] + _struct.pack(">i", len(node)),
                            head.handle,
                        )
                    node.annotations[cd] = [head] + list(node)
                    synced += 1
            stack.extend(node)

        elif isinstance(node, JavaInstance):
            for _cd, fields in node.field_data.items():
                stack.extend(fields.values())
            for _cd, anns in node.annotations.items():
                stack.extend(a for a in anns if not isinstance(a, (bytes, int, float, str, bool, type(None))))

        elif isinstance(node, list):  # plain arrays (JavaArray etc.)
            stack.extend(node)

    return synced


def _set_size_field(instance: Any, new_size: int) -> bool:
    """Update the serialized `size` field on a collection instance (ArrayList
    keeps its element count as a real field, separate from the annotation
    stream — both must agree or Java's readObject reads garbage)."""
    for _cd, fields in instance.field_data.items():
        for jf in list(fields.keys()):
            if jf.name == "size":
                fields[jf] = new_size
                return True
    return False


def save_file(root: Any, filepath: str, backup: bool = True) -> str | None:
    """Re-encode `root` and write it to filepath. Returns the backup path
    if one was made."""
    backup_path = None
    if backup and os.path.exists(filepath):
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = f"{filepath}.bak_{ts}"
        shutil.copy2(filepath, backup_path)
    data = dump_to_bytes(root)
    with open(filepath, "wb") as f:
        f.write(data)
    return backup_path

def get_fields(instance: JavaInstance) -> dict[str, Any]:
    """Flatten an instance's field_data (which is keyed per-classdesc, to
    support inheritance) into a single {name: value} dict. AoH2's classes
    are not known to have field name collisions across a hierarchy, so this
    flattening is safe in practice; if it ever isn't, get_fields_by_class
    below preserves the full structure."""
    out = {}
    for _classdesc, fields in instance.field_data.items():
        for jf, value in fields.items():
            out[jf.name] = value
    return out


def get_fields_by_class(instance: JavaInstance) -> dict[Any, dict[str, Any]]:
    """Like get_fields, but keeps the per-classdesc grouping (useful when a
    subclass and superclass both declare a field with the same name)."""
    out = {}
    for classdesc, fields in instance.field_data.items():
        out[classdesc] = {jf.name: value for jf, value in fields.items()}
    return out


def set_field(instance: JavaInstance, name: str, value: Any) -> bool:
    """Set a named field's value on the live instance. Searches across all
    classdescs in the hierarchy (first match wins) since AoH2 field names
    are not known to collide across superclass/subclass boundaries."""
    for _classdesc, fields in instance.field_data.items():
        for jf in list(fields.keys()):
            if jf.name == name:
                fields[jf] = value
                return True
    return False


def class_name(instance: JavaInstance) -> str:
    try:
        return instance.get_class().name
    except Exception:
        return "?"

@dataclass
class TreeNode:
    label: str                 # display label, e.g. "iId" or "[3]" or "Civ"
    kind: str                  # "object" | "list" | "primitive" | "ref"
    value: Any                 # the live node (JavaInstance / JavaList / primitive)
    handle: int | None = None  # identity handle, for shared-reference detection
    editable: bool = False


def describe(value: Any, label: str, seen: set[int]) -> TreeNode:
    """Classify a node in the live tree for display. `seen` tracks handles
    already expanded in THIS top-level walk, so a shared reference (e.g. a
    province object pointed to from two different lists) is shown once in
    full and as a '-> ref' pointer everywhere else, rather than being
    duplicated or recursing forever on a cycle."""
    handle = getattr(value, "handle", None)
    
    if isinstance(value, list):  # JavaList / JavaArray
        if handle is not None and handle in seen:
            return TreeNode(label=f"{label} -> ref #{handle}", kind="ref",
                             value=value, handle=handle)
        if handle is not None:
            seen.add(handle)
        return TreeNode(label=f"{label} [{len(value)} items]", kind="list",
                         value=value, handle=handle)

    if isinstance(value, JavaInstance):
        if handle is not None and handle in seen:
            return TreeNode(label=f"{label} -> ref #{handle}", kind="ref",
                             value=value, handle=handle)
        if handle is not None:
            seen.add(handle)
        return TreeNode(label=f"{label}: {class_name(value)}", kind="object",
                         value=value, handle=handle)

    # primitive: int, float, str, bool, None
    return TreeNode(label=f"{label} = {value!r}", kind="primitive",
                     value=value, editable=True)


def children(node: TreeNode, seen: set[int]) -> Iterator[TreeNode]:
    if node.kind == "object":
        for fname, fval in get_fields(node.value).items():
            yield describe(fval, fname, seen)
    elif node.kind == "list":
        for i, item in enumerate(node.value):
            yield describe(item, f"[{i}]", seen)
    # "primitive" and "ref" nodes have no children
