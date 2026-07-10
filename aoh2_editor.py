"""
aoh2_editor.py — GUI for browsing and editing AoH2 save files as a tree
instead of raw hex.

Usage:
    pip install javaobj-py3
    python aoh2_editor.py

Then File > Open Project Folder... and point it at your save folder, e.g.
    D:\\Steam\\steamapps\\common\\AoCII\\saves\\games\\Earth\\<save_id>\\

Every file inside that's a Java serialization stream (civs, provinces, wars,
etc.) is auto-detected and loaded. Expand nodes to browse; double-click a
value to edit it. File > Save Modified Files re-encodes only the files you
changed, after writing a timestamped .bak copy of each.

Known v1 limitations (next increments, not blockers):
  - Editing is field-by-field; you can't yet add/remove list items or
    create new objects from the GUI.
  - Search only looks at nodes you've already expanded (the tree loads
    lazily so huge saves don't stall on open). Expand the relevant section
    first, or use "Expand All & Search" for a full sweep.
"""

import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

import aoh2codec as codec


class Aoh2Editor(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("AoH2 Save Editor")
        self.geometry("950x650")

        # path -> {"root": live_object_graph, "seen": set(), "modified": bool}
        self.files = {}
        # tree item id -> codec.TreeNode
        self.item_node = {}
        # tree item id -> owning file path
        self.item_owner = {}

        self._build_menu()
        self._build_widgets()

    def _build_menu(self):
        menubar = tk.Menu(self)
        filemenu = tk.Menu(menubar, tearoff=0)
        filemenu.add_command(label="Open Project Folder...", command=self.open_folder)
        filemenu.add_command(label="Open Single File...", command=self.open_single_file)
        filemenu.add_separator()
        filemenu.add_command(label="Save Modified Files", command=self.save_all)
        filemenu.add_separator()
        filemenu.add_command(label="Exit", command=self.destroy)
        menubar.add_cascade(label="File", menu=filemenu)

        viewmenu = tk.Menu(menubar, tearoff=0)
        viewmenu.add_command(label="Expand All & Search", command=self.expand_all_and_search)
        menubar.add_cascade(label="View", menu=viewmenu)

        self.config(menu=menubar)

    def _build_widgets(self):
        toolbar = ttk.Frame(self)
        toolbar.pack(fill="x", padx=6, pady=6)
        ttk.Label(toolbar, text="Search:").pack(side="left")
        self.search_var = tk.StringVar()
        entry = ttk.Entry(toolbar, textvariable=self.search_var, width=30)
        entry.pack(side="left", padx=4)
        entry.bind("<Return>", lambda e: self.search())
        ttk.Button(toolbar, text="Find Next", command=self.search).pack(side="left")
        self.status_var = tk.StringVar(value="No project open. File > Open Project Folder...")
        ttk.Label(toolbar, textvariable=self.status_var).pack(side="right")

        self.tree = ttk.Treeview(self, show="tree")
        self.tree.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        self.tree.bind("<<TreeviewOpen>>", self.on_expand)
        self.tree.bind("<Double-1>", self.on_double_click)

        self._last_match_index = -1

    def open_folder(self):
        folder = filedialog.askdirectory(title="Select AoH2 save folder")
        if not folder:
            return
        paths = codec.scan_project(folder)
        if not paths:
            messagebox.showwarning(
                "No save files found",
                "No Java-serialized files (magic bytes AC ED 00 05) were found in that folder.",
            )
            return
        loaded = 0
        for p in paths:
            if self._load_one(p):
                loaded += 1
        self.status_var.set(f"Loaded {loaded}/{len(paths)} file(s) from {folder}")

    def open_single_file(self):
        path = filedialog.askopenfilename(title="Select save file")
        if not path:
            return
        if not codec.is_java_serialized(path):
            if not messagebox.askyesno(
                "Not detected as Java serialization",
                "This file doesn't start with the expected magic bytes (AC ED 00 05).\n"
                "Try loading it anyway?",
            ):
                return
        if self._load_one(path):
            self.status_var.set(f"Loaded {os.path.basename(path)}")

    def _load_one(self, path) -> bool:
        try:
            root = codec.load_file(path)
        except Exception as e:
            messagebox.showerror("Failed to parse", f"{path}\n\n{type(e).__name__}: {e}")
            return False
        self.files[path] = {"root": root, "seen": set(), "modified": False}
        iid = self.tree.insert("", "end", text=os.path.basename(path), open=False)
        node = codec.describe(root, os.path.basename(path), self.files[path]["seen"])
        self.item_node[iid] = node
        self.item_owner[iid] = path
        self._ensure_expandable(iid, node)
        return True

    def _ensure_expandable(self, iid, node):
        if node.kind in ("object", "list") and not self.tree.get_children(iid):
            self.tree.insert(iid, "end", text="(loading...)", tags=("placeholder",))

    def on_expand(self, event=None, iid=None):
        iid = iid or self.tree.focus()
        node = self.item_node.get(iid)
        if node is None:
            return
        kids = self.tree.get_children(iid)
        if len(kids) == 1 and "placeholder" in self.tree.item(kids[0], "tags"):
            self.tree.delete(kids[0])
            owner = self.item_owner[iid]
            seen = self.files[owner]["seen"]
            for child in codec.children(node, seen):
                child_iid = self.tree.insert(iid, "end", text=child.label)
                self.item_node[child_iid] = child
                self.item_owner[child_iid] = owner
                self._ensure_expandable(child_iid, child)

    def on_double_click(self, event):
        iid = self.tree.focus()
        node = self.item_node.get(iid)
        if node is None or not node.editable:
            return  # only primitive leaves are directly editable in v1
        owner = self.item_owner[iid]
        parent_iid = self.tree.parent(iid)
        parent_node = self.item_node.get(parent_iid)
        if parent_node is None:
            return

        current = node.value
        new_str = simpledialog.askstring(
            "Edit value", f"{node.label}\n\nNew value:", initialvalue=str(current), parent=self
        )
        if new_str is None:
            return
        try:
            new_value = self._coerce(current, new_str)
        except ValueError as e:
            messagebox.showerror("Invalid value", str(e))
            return

        if not self._write_back(parent_node, iid, new_value):
            messagebox.showerror("Edit failed", "Could not locate this field on its parent object.")
            return

        node.value = new_value
        field_label = node.label.split(" = ")[0]
        self.tree.item(iid, text=f"{field_label} = {new_value!r}")
        self.files[owner]["modified"] = True
        self.status_var.set(f"Modified (unsaved): {os.path.basename(owner)}")

    @staticmethod
    def _coerce(current, new_str):
        if isinstance(current, bool):
            low = new_str.strip().lower()
            if low in ("true", "1", "yes"):
                return True
            if low in ("false", "0", "no"):
                return False
            raise ValueError("Expected true/false")
        if isinstance(current, int):
            return int(new_str)
        if isinstance(current, float):
            return float(new_str)
        return new_str

    def _write_back(self, parent_node, child_iid, new_value) -> bool:
        idx = self.tree.index(child_iid)
        if parent_node.kind == "object":
            fields = codec.get_fields(parent_node.value)
            names = list(fields.keys())
            if idx >= len(names):
                return False
            return codec.set_field(parent_node.value, names[idx], new_value)
        if parent_node.kind == "list":
            try:
                parent_node.value[idx] = new_value
                return True
            except (IndexError, TypeError):
                return False
        return False

    def search(self):
        query = self.search_var.get().strip().lower()
        if not query:
            return
        matches = []

        def walk(iid):
            text = self.tree.item(iid, "text").lower()
            if query in text:
                matches.append(iid)
            for c in self.tree.get_children(iid):
                walk(c)

        for top in self.tree.get_children(""):
            walk(top)

        if not matches:
            messagebox.showinfo(
                "Search",
                "No matches among currently-expanded nodes.\n\n"
                "Tip: expand the relevant section first, or use "
                "View > Expand All & Search for a full sweep (slower on big saves).",
            )
            return

        self._last_match_index = (self._last_match_index + 1) % len(matches)
        target = matches[self._last_match_index]
        self.tree.see(target)
        self.tree.selection_set(target)
        self.tree.focus(target)
        self.status_var.set(f"Match {self._last_match_index + 1}/{len(matches)} for '{query}'")

    def expand_all_and_search(self):
        for top in self.tree.get_children(""):
            self._expand_recursive(top)
        self.search()

    def _expand_recursive(self, iid, max_nodes=20000, _counter=[0]):
        if _counter[0] > max_nodes:
            return
        self.on_expand(iid=iid)
        _counter[0] += 1
        for c in self.tree.get_children(iid):
            self._expand_recursive(c, max_nodes, _counter)

    def save_all(self):
        saved = []
        for path, info in self.files.items():
            if not info["modified"]:
                continue
            try:
                backup = codec.save_file(info["root"], path, backup=True)
            except Exception as e:
                messagebox.showerror("Save failed", f"{path}\n\n{type(e).__name__}: {e}")
                continue
            info["modified"] = False
            saved.append((path, backup))

        if not saved:
            messagebox.showinfo("Save", "No changes to save.")
            return

        lines = [
            f"{os.path.basename(p)}  (backup: {os.path.basename(b) if b else 'none'})"
            for p, b in saved
        ]
        messagebox.showinfo("Saved", "Re-encoded and saved:\n\n" + "\n".join(lines))
        self.status_var.set("All changes saved.")


if __name__ == "__main__":
    app = Aoh2Editor()
    app.mainloop()
