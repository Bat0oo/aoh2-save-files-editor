import os
import contextlib
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
import aoh2codec as codec

class Aoh2Editor(tk.Tk):

    @contextlib.contextmanager
    def busy(self, message='Working...'):
        prev = self.status_var.get()
        self.status_var.set(message)
        self.config(cursor='watch')
        self.update_idletasks()
        try:
            yield
        finally:
            self.config(cursor='')
            if self.status_var.get() == message:
                self.status_var.set(prev)

    def __init__(self):
        super().__init__()
        self.title('AoH2 Save Editor')
        self.geometry('950x650')
        self.files = {}
        self.item_node = {}
        self.item_owner = {}
        self._build_menu()
        self._build_widgets()

    def _build_menu(self):
        menubar = tk.Menu(self)
        filemenu = tk.Menu(menubar, tearoff=0)
        filemenu.add_command(label='Open Project Folder...', command=self.open_folder)
        filemenu.add_command(label='Open Single File...', command=self.open_single_file)
        filemenu.add_separator()
        filemenu.add_command(label='Save Modified Files', command=self.save_all)
        filemenu.add_command(label='Export Copy To Folder...', command=self.export_copy)
        filemenu.add_separator()
        filemenu.add_command(label='Exit', command=self.destroy)
        menubar.add_cascade(label='File', menu=filemenu)
        viewmenu = tk.Menu(menubar, tearoff=0)
        viewmenu.add_command(label='Expand All & Search', command=self.expand_all_and_search)
        menubar.add_cascade(label='View', menu=viewmenu)
        self.config(menu=menubar)

    def _build_widgets(self):
        toolbar = ttk.Frame(self)
        toolbar.pack(fill='x', padx=6, pady=6)
        ttk.Label(toolbar, text='Search:').pack(side='left')
        self.search_var = tk.StringVar()
        entry = ttk.Entry(toolbar, textvariable=self.search_var, width=24)
        entry.pack(side='left', padx=4)
        entry.bind('<Return>', lambda e: self.search())
        ttk.Button(toolbar, text='Find Next', command=self.search).pack(side='left')
        ttk.Button(toolbar, text='Expand All', command=self.expand_all).pack(side='left', padx=(6, 0))
        ttk.Separator(toolbar, orient='vertical').pack(side='left', fill='y', padx=8)
        ttk.Label(toolbar, text='Filter civ (id or tag):').pack(side='left')
        self.filter_var = tk.StringVar()
        fentry = ttk.Entry(toolbar, textvariable=self.filter_var, width=12)
        fentry.pack(side='left', padx=4)
        fentry.bind('<Return>', lambda e: self.filter_apply())
        ttk.Button(toolbar, text='Filter', command=self.filter_apply).pack(side='left')
        ttk.Button(toolbar, text='Clear', command=self.filter_clear).pack(side='left', padx=(2, 0))
        self.status_var = tk.StringVar(value='No project open. File > Open Project Folder...')
        ttk.Label(self, textvariable=self.status_var).pack(fill='x', padx=8, pady=(0, 2), anchor='w')
        self.tree = ttk.Treeview(self, show='tree')
        self.tree.pack(fill='both', expand=True, padx=6, pady=(0, 6))
        self.tree.bind('<<TreeviewOpen>>', self.on_expand)
        self.tree.bind('<Double-1>', self.on_double_click)
        self.tree.bind('<Motion>', self._on_tree_motion)
        self.tree.bind('<Leave>', lambda e: self._hide_tooltip())
        self._tooltip_win = None
        self._tooltip_iid = None
        self._tooltip_after = None
        self._last_match_index = -1
        self._last_query = None
        self.filter_active = False

    def open_folder(self):
        folder = filedialog.askdirectory(title='Select AoH2 save folder')
        if not folder:
            return
        paths = codec.scan_project(folder)
        if not paths:
            messagebox.showwarning('No save files found', 'No Java-serialized files (magic bytes AC ED 00 05) were found in that folder.')
            return
        if self.files:
            unsaved = [os.path.basename(p) for p, i in self.files.items() if i['modified']]
            if unsaved:
                if not messagebox.askyesno('Unsaved changes', 'These files have unsaved edits that will be DISCARDED:\n\n' + '\n'.join(unsaved) + '\n\nOpen the new folder anyway?'):
                    return
            self._close_project()
        loaded = 0
        with self.busy(f'Loading {len(paths)} file(s)...'):
            for p in paths:
                if self._load_one(p):
                    loaded += 1
        self.status_var.set(f'Loaded {loaded}/{len(paths)} file(s) from {folder}')

    def _close_project(self):
        self.files.clear()
        self.item_node.clear()
        self.item_owner.clear()
        self.tree.delete(*self.tree.get_children(''))
        self.filter_active = False
        self._last_match_index = -1
        self._last_query = None

    def open_single_file(self):
        path = filedialog.askopenfilename(title='Select save file')
        if not path:
            return
        if not codec.is_java_serialized(path):
            if not messagebox.askyesno('Not detected as Java serialization', "This file doesn't start with the expected magic bytes (AC ED 00 05).\nTry loading it anyway?"):
                return
        if self._load_one(path):
            self.status_var.set(f'Loaded {os.path.basename(path)}')

    def _load_one(self, path) -> bool:
        try:
            root = codec.load_file(path)
        except Exception as e:
            messagebox.showerror('Failed to parse', f'{path}\n\n{type(e).__name__}: {e}')
            return False
        self.files[path] = {'root': root, 'seen': set(), 'modified': False}
        iid = self.tree.insert('', 'end', text=os.path.basename(path), open=False)
        node = codec.describe(root, os.path.basename(path), self.files[path]['seen'])
        self.item_node[iid] = node
        self.item_owner[iid] = path
        self._ensure_expandable(iid, node)
        return True

    def _ensure_expandable(self, iid, node):
        if node.kind in ('object', 'list') and (not self.tree.get_children(iid)):
            self.tree.insert(iid, 'end', text='(loading...)', tags=('placeholder',))

    def on_expand(self, event=None, iid=None):
        iid = iid or self.tree.focus()
        node = self.item_node.get(iid)
        if node is None:
            return
        kids = self.tree.get_children(iid)
        if len(kids) == 1 and 'placeholder' in self.tree.item(kids[0], 'tags'):
            self.tree.delete(kids[0])
            owner = self.item_owner[iid]
            seen = self.files[owner]['seen']
            for child in codec.children(node, seen):
                child_iid = self.tree.insert(iid, 'end', text=child.label)
                self.item_node[child_iid] = child
                self.item_owner[child_iid] = owner
                self._ensure_expandable(child_iid, child)

    def on_double_click(self, event):
        iid = self.tree.focus()
        node = self.item_node.get(iid)
        if node is None or not node.editable:
            return
        owner = self.item_owner[iid]
        parent_iid = self.tree.parent(iid)
        parent_node = self.item_node.get(parent_iid)
        if parent_node is None:
            return
        current = node.value
        new_str = simpledialog.askstring('Edit value', f'{node.label}\n\nNew value:', initialvalue=str(current), parent=self)
        if new_str is None:
            return
        try:
            new_value = self._coerce(current, new_str)
        except ValueError as e:
            messagebox.showerror('Invalid value', str(e))
            return
        if not self._write_back(parent_node, iid, new_value):
            messagebox.showerror('Edit failed', 'Could not locate this field on its parent object.')
            return
        node.value = new_value
        field_label = node.label.split(' = ')[0]
        self.tree.item(iid, text=f'{field_label} = {new_value!r}')
        self.files[owner]['modified'] = True
        self.status_var.set(f'Modified (unsaved): {os.path.basename(owner)}')

    @staticmethod
    def _coerce(current, new_str):
        if isinstance(current, bool):
            low = new_str.strip().lower()
            if low in ('true', '1', 'yes'):
                return True
            if low in ('false', '0', 'no'):
                return False
            raise ValueError('Expected true/false')
        if isinstance(current, int):
            return int(new_str)
        if isinstance(current, float):
            return float(new_str)
        return new_str

    def _write_back(self, parent_node, child_iid, new_value) -> bool:
        idx = self.tree.index(child_iid)
        if parent_node.kind == 'object':
            fields = codec.get_fields(parent_node.value)
            names = list(fields.keys())
            if idx >= len(names):
                return False
            return codec.set_field(parent_node.value, names[idx], new_value)
        if parent_node.kind == 'list':
            try:
                current = parent_node.value[idx]
                if isinstance(current, codec.JavaString) and isinstance(new_value, str):
                    current.value = new_value
                else:
                    parent_node.value[idx] = new_value
                return True
            except (IndexError, TypeError):
                return False
        return False

    @staticmethod
    def _make_matcher(query: str):
        query = query.strip()
        if '=' in query:
            name, _, val = query.partition('=')
            name = name.strip().lower()
            val = val.strip().strip('\'"').lower()

            def matcher(label: str) -> bool:
                if ' = ' not in label:
                    return False
                lname, _, lval = label.partition(' = ')
                return lname.strip().lower() == name and lval.strip().strip('\'"').lower() == val
            return matcher
        q = query.lower()
        return lambda label: q in label.lower()

    def search(self):
        query = self.search_var.get().strip()
        if not query:
            return
        if query != self._last_query:
            self._last_match_index = -1
            self._last_query = query
        matcher = self._make_matcher(query)

        def collect():
            found = []

            def walk(iid):
                if matcher(self.tree.item(iid, 'text')):
                    found.append(iid)
                for c in self.tree.get_children(iid):
                    walk(c)
            for top in self.tree.get_children(''):
                walk(top)
            return found
        matches = collect()
        if not matches:
            with self.busy(f"Searching '{query}' (expanding tree)..."):
                self.expand_all()
                matches = collect()
        if not matches:
            self.status_var.set(f"No matches for '{query}'")
            return
        self._last_match_index = (self._last_match_index + 1) % len(matches)
        target = matches[self._last_match_index]
        self.tree.see(target)
        self.tree.selection_set(target)
        self.tree.focus(target)
        self.status_var.set(f"Match {self._last_match_index + 1}/{len(matches)} for '{query}'")

    def expand_all(self, max_nodes=20000):
        counter = [0]

        def rec(iid):
            if counter[0] > max_nodes:
                return
            self.on_expand(iid=iid)
            counter[0] += 1
            for c in self.tree.get_children(iid):
                rec(c)
        with self.busy('Expanding tree...'):
            for top in self.tree.get_children(''):
                rec(top)
        if counter[0] > max_nodes:
            self.status_var.set(f'Expanded {max_nodes} nodes (limit reached — use Filter to narrow down instead)')
        else:
            self.status_var.set('Expanded all nodes.')

    def expand_all_and_search(self):
        self.expand_all()
        self.search()

    def filter_apply(self):
        query = self.filter_var.get().strip()
        if not query:
            return
        if not self.files:
            messagebox.showwarning('Filter', 'Open a project folder first.')
            return
        want_id = int(query) if query.lstrip('-').isdigit() else None
        want_tag = query.lower()
        TAG_FIELDS = ('sCivTag', 'sCivName', 'sTag', 'sName')
        ID_FIELDS = ('iId', 'iID', 'iCivID', 'iCivId')

        def obj_matches(fields: dict) -> bool:
            if want_id is not None:
                return any((fields.get(k) == want_id for k in ID_FIELDS))
            return any((fields.get(k) is not None and want_tag in str(fields[k]).lower() for k in TAG_FIELDS))
        with self.busy(f"Filtering for '{query}'..."):
            self._filter_run(query, obj_matches)

    def _filter_run(self, query, obj_matches):
        self.tree.delete(*self.tree.get_children(''))
        self.item_node.clear()
        self.item_owner.clear()
        total = 0
        for path, info in self.files.items():
            matches = []
            for inst in codec.iter_instances(info['root']):
                fields = codec.get_fields(inst)
                if obj_matches(fields):
                    matches.append(inst)
            if not matches:
                continue
            info['seen'] = set()
            head = self.tree.insert('', 'end', text=f"{os.path.basename(path)} — {len(matches)} match(es) for '{query}'", open=True)
            for inst in matches:
                fields = codec.get_fields(inst)
                tag = next((str(fields[k]) for k in ('sCivTag', 'sTag') if fields.get(k)), '')
                name = next((str(fields[k]) for k in ('sCivName', 'sName') if fields.get(k)), '')
                iid_val = next((fields[k] for k in ('iId', 'iCivID', 'iCivId') if fields.get(k) is not None), None)
                cls = codec.class_name(inst).rsplit('.', 1)[-1]
                extra = '  '.join((x for x in (f'[{tag}]' if tag else '', name, f'id={iid_val}' if iid_val is not None else '') if x))
                node = codec.describe(inst, f'{cls}  {extra}'.strip(), info['seen'])
                child = self.tree.insert(head, 'end', text=node.label)
                self.item_node[child] = node
                self.item_owner[child] = path
                self._ensure_expandable(child, node)
            total += len(matches)
        self.filter_active = True
        if total == 0:
            self.tree.insert('', 'end', text=f"(no objects matching '{query}')")
        self.status_var.set(f"Filter '{query}': {total} match(es). Edits here modify the real save — Clear restores full view.")

    def filter_clear(self):
        if not self.filter_active and self.tree.get_children(''):
            return
        self._rebuild_tree()
        self.filter_active = False
        self.status_var.set('Filter cleared — full view restored. (Unsaved edits are kept.)')

    def _rebuild_tree(self):
        self.tree.delete(*self.tree.get_children(''))
        self.item_node.clear()
        self.item_owner.clear()
        for path, info in self.files.items():
            info['seen'] = set()
            iid = self.tree.insert('', 'end', text=os.path.basename(path), open=False)
            node = codec.describe(info['root'], os.path.basename(path), info['seen'])
            self.item_node[iid] = node
            self.item_owner[iid] = path
            self._ensure_expandable(iid, node)

    def _on_tree_motion(self, event):
        iid = self.tree.identify_row(event.y)
        if iid == self._tooltip_iid:
            return
        self._hide_tooltip()
        self._tooltip_iid = iid
        if not iid or iid not in self.item_node:
            return
        self._tooltip_after = self.after(500, lambda: self._show_tooltip(iid, event.x_root + 14, event.y_root + 12))

    def _tooltip_text(self, node):
        if node.kind == 'object':
            fields = codec.get_fields(node.value)
            preview = ', '.join(list(fields.keys())[:8])
            more = '...' if len(fields) > 8 else ''
            return f'Object: {codec.class_name(node.value)}\n{len(fields)} field(s): {preview}{more}\nExpand to browse; leaves are editable.'
        if node.kind == 'list':
            return f'List with {len(node.value)} item(s).\nExpand to browse items.'
        if node.kind == 'ref':
            return 'Shared reference — this exact object also appears\nelsewhere in the tree (same identity in the save).\nEdit it at its first occurrence; changes apply everywhere.'
        if node.kind == 'primitive':
            return f'{type(node.value).__name__} value.\nDouble-click to edit.'
        return ''

    def _show_tooltip(self, iid, x, y):
        node = self.item_node.get(iid)
        if node is None:
            return
        text = self._tooltip_text(node)
        if not text:
            return
        self._tooltip_win = tw = tk.Toplevel(self)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f'+{x}+{y}')
        tk.Label(tw, text=text, justify='left', background='#ffffe0', relief='solid', borderwidth=1, font=('Segoe UI', 9)).pack()

    def _hide_tooltip(self):
        if self._tooltip_after is not None:
            self.after_cancel(self._tooltip_after)
            self._tooltip_after = None
        if self._tooltip_win is not None:
            self._tooltip_win.destroy()
            self._tooltip_win = None
        self._tooltip_iid = None

    def export_copy(self):
        if not self.files:
            messagebox.showwarning('Export', 'Open a project folder first.')
            return
        target = filedialog.askdirectory(title='Export copies into folder...')
        if not target:
            return
        src_dirs = {os.path.dirname(os.path.abspath(p)) for p in self.files}
        if os.path.abspath(target) in src_dirs:
            messagebox.showerror('Export', "That's the same folder the save was loaded from.\nPick a different folder (or use Save Modified Files to save in place).")
            return
        exported, failed = ([], [])
        with self.busy('Exporting...'):
            for path, info in self.files.items():
                out_path = os.path.join(target, os.path.basename(path))
                try:
                    data = codec.dump_to_bytes(info['root'])
                    with open(out_path, 'wb') as f:
                        f.write(data)
                    exported.append(os.path.basename(path))
                except Exception as e:
                    failed.append(f'{os.path.basename(path)}: {type(e).__name__}: {e}')
        msg = f'Exported {len(exported)} file(s) to:\n{target}'
        if failed:
            msg += '\n\nFAILED:\n' + '\n'.join(failed)
        msg += '\n\nNote: exported files include your unsaved edits. The original save is untouched.'
        (messagebox.showwarning if failed else messagebox.showinfo)('Export', msg)
        self.status_var.set(f'Exported {len(exported)} file(s) to {target}')

    def save_all(self):
        saved = []
        for path, info in self.files.items():
            if not info['modified']:
                continue
            try:
                backup = codec.save_file(info['root'], path, backup=True)
            except Exception as e:
                messagebox.showerror('Save failed', f'{path}\n\n{type(e).__name__}: {e}')
                continue
            info['modified'] = False
            saved.append((path, backup))
        if not saved:
            messagebox.showinfo('Save', 'No changes to save.')
            return
        lines = [f'{os.path.basename(p)}  (backup: {(os.path.basename(b) if b else 'none')})' for p, b in saved]
        messagebox.showinfo('Saved', 'Re-encoded and saved:\n\n' + '\n'.join(lines))
        self.status_var.set('All changes saved.')
if __name__ == '__main__':
    app = Aoh2Editor()
    app.mainloop()