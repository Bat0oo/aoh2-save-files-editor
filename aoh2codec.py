from __future__ import annotations
import io
import os
import shutil
import datetime
from dataclasses import dataclass
from typing import Any, Iterator
import javaobj.v3 as j3
from javaobj.v3.beans import JavaInstance, JavaField, JavaString
JAVA_SERIAL_MAGIC = b'\xac\xed\x00\x05'

def is_java_serialized(filepath: str) -> bool:
    try:
        with open(filepath, 'rb') as f:
            return f.read(4) == JAVA_SERIAL_MAGIC
    except OSError:
        return False

def scan_project(folder: str) -> list[str]:
    found = []
    for root, _dirs, files in os.walk(folder):
        for name in files:
            if '.bak_' in name:
                continue
            path = os.path.join(root, name)
            if is_java_serialized(path):
                found.append(path)
    return sorted(found)

def load_file(filepath: str) -> Any:
    with open(filepath, 'rb') as f:
        data = f.read()
    return j3.loads(data)

def dump_to_bytes(root: Any) -> bytes:
    sync_collections(root)
    return j3.dumps(root)

def sync_collections(root: Any) -> int:
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
                if cd.name == 'java.util.ArrayList':
                    head = ann[0] if ann else BlockData(_struct.pack('>i', len(node)))
                    node.annotations[cd] = [head] + list(node)
                    _set_size_field(node, len(node))
                    synced += 1
                elif cd.name == 'java.util.LinkedList':
                    node.annotations[cd] = [BlockData(_struct.pack('>i', len(node)))] + list(node)
                    synced += 1
            stack.extend(node)
        elif isinstance(node, JavaMap):
            for cd in list(node.annotations.keys()):
                ann = node.annotations[cd]
                if cd.name in JavaMap.HANDLED_CLASSES and ann:
                    head = ann[0]
                    if isinstance(head, BlockData) and len(head.data) >= 8:
                        head = BlockData(head.data[:-4] + _struct.pack('>i', len(node)), head.handle)
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
                    if isinstance(head, BlockData) and len(head.data) >= 12:
                        head = BlockData(head.data[:-4] + _struct.pack('>i', len(node)), head.handle)
                    node.annotations[cd] = [head] + list(node)
                    synced += 1
            stack.extend(node)
        elif isinstance(node, JavaInstance):
            for _cd, fields in node.field_data.items():
                stack.extend(fields.values())
            for _cd, anns in node.annotations.items():
                stack.extend((a for a in anns if not isinstance(a, (bytes, int, float, str, bool, type(None)))))
        elif isinstance(node, list):
            stack.extend(node)
    return synced

def _set_size_field(instance: Any, new_size: int) -> bool:
    for _cd, fields in instance.field_data.items():
        for jf in list(fields.keys()):
            if jf.name == 'size':
                fields[jf] = new_size
                return True
    return False

def save_file(root: Any, filepath: str, backup: bool=True) -> str | None:
    backup_path = None
    if backup and os.path.exists(filepath):
        ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_path = f'{filepath}.bak_{ts}'
        shutil.copy2(filepath, backup_path)
    data = dump_to_bytes(root)
    with open(filepath, 'wb') as f:
        f.write(data)
    return backup_path

def get_fields(instance: JavaInstance) -> dict[str, Any]:
    out = {}
    for _classdesc, fields in instance.field_data.items():
        for jf, value in fields.items():
            out[jf.name] = value
    return out

def get_fields_by_class(instance: JavaInstance) -> dict[Any, dict[str, Any]]:
    out = {}
    for classdesc, fields in instance.field_data.items():
        out[classdesc] = {jf.name: value for jf, value in fields.items()}
    return out

def set_field(instance: JavaInstance, name: str, value: Any) -> bool:
    for _classdesc, fields in instance.field_data.items():
        for jf in list(fields.keys()):
            if jf.name == name:
                current = fields[jf]
                if isinstance(current, JavaString) and isinstance(value, str):
                    current.value = value
                else:
                    fields[jf] = value
                return True
    return False

def class_name(instance: JavaInstance) -> str:
    try:
        return instance.get_class().name
    except Exception:
        return '?'

@dataclass
class TreeNode:
    label: str
    kind: str
    value: Any
    handle: int | None = None
    editable: bool = False

def describe(value: Any, label: str, seen: set[int]) -> TreeNode:
    handle = getattr(value, 'handle', None)
    if isinstance(value, list):
        if handle is not None and handle in seen:
            return TreeNode(label=f'{label} -> ref #{handle}', kind='ref', value=value, handle=handle)
        if handle is not None:
            seen.add(handle)
        return TreeNode(label=f'{label} [{len(value)} items]', kind='list', value=value, handle=handle)
    if isinstance(value, JavaInstance):
        if handle is not None and handle in seen:
            return TreeNode(label=f'{label} -> ref #{handle}', kind='ref', value=value, handle=handle)
        if handle is not None:
            seen.add(handle)
        return TreeNode(label=f'{label}: {class_name(value)}', kind='object', value=value, handle=handle)
    return TreeNode(label=f'{label} = {value!r}', kind='primitive', value=value, editable=True)

def iter_instances(root: Any) -> Iterator[JavaInstance]:
    seen: set[int] = set()
    stack = [root]
    while stack:
        node = stack.pop()
        oid = id(node)
        if oid in seen:
            continue
        seen.add(oid)
        if isinstance(node, list):
            if isinstance(node, JavaInstance):
                yield node
            stack.extend(node)
        elif isinstance(node, JavaInstance):
            yield node
            for _cd, fields in node.field_data.items():
                stack.extend(fields.values())

def children(node: TreeNode, seen: set[int]) -> Iterator[TreeNode]:
    if node.kind == 'object':
        for fname, fval in get_fields(node.value).items():
            yield describe(fval, fname, seen)
    elif node.kind == 'list':
        for i, item in enumerate(node.value):
            yield describe(item, f'[{i}]', seen)