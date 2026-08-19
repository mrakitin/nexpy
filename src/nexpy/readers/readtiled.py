# -----------------------------------------------------------------------------
# Copyright (c) 2013-2026, NeXpy Development Team.
#
# Distributed under the terms of the Modified BSD License.
#
# The full license is in the file COPYING, distributed with this software.
# -----------------------------------------------------------------------------

"""
Module to browse and import datasets from a Tiled server into NeXpy.

The default server is https://tiled.nsls2.bnl.gov/ but any Tiled URL can be
used.  Authentication uses the normal Tiled client flow:

* If the user has previously logged in (``tiled login <url>``) the cached
  token is reused automatically.
* Otherwise Qt ``QInputDialog`` popups collect credentials — the terminal is
  never blocked.

Tree browsing is fully lazy and paginated (PAGE_SIZE items per request).
A "Load N more…" placeholder row appears at the bottom of any container that
has more entries than the current page.
"""

import numpy as np
from nexusformat.nexus import NeXusError, NXcollection, NXdata, NXfield

from nexpy.gui.importdialog import NXImportDialog
from nexpy.gui.pyqt import QtCore, QtWidgets
from nexpy.gui.utils import natural_sort, report_error
from nexpy.gui.widgets import NXLabel, NXLineEdit, NXPushButton

filetype = "Tiled Dataset"

DEFAULT_URL = "https://tiled.nsls2.bnl.gov/"
PAGE_SIZE = 100

# Sentinel stored in UserRole to mark a "Load more" row
_LOAD_MORE = "__load_more__"


class ImportDialog(NXImportDialog):
    """Dialog to browse a Tiled catalog and import a dataset."""

    def __init__(self, parent=None):
        super().__init__(parent=parent)

        self._catalog = None  # root tiled client node after connect

        # --- URL row ---
        url_label = NXLabel("Server URL")
        self.url_box = NXLineEdit(DEFAULT_URL)
        self.url_box.setMinimumWidth(380)
        url_layout = self.make_layout(url_label, self.url_box,
                                      align='justified')

        # --- Connect button ---
        self.connect_button = NXPushButton("Connect", self._connect)
        connect_layout = self.make_layout(self.connect_button, align='center')

        # --- Tree widget ---
        self.tree_widget = QtWidgets.QTreeWidget()
        self.tree_widget.setHeaderLabel("Catalog")
        self.tree_widget.setMinimumWidth(500)
        self.tree_widget.setMinimumHeight(300)
        self.tree_widget.itemExpanded.connect(self._on_expand)
        self.tree_widget.itemClicked.connect(self._on_item_click)
        self.tree_widget.itemSelectionChanged.connect(self._on_select)

        # --- Status label ---
        self.status_label = NXLabel("")
        self.status_label.setWordWrap(True)

        self.set_layout(
            url_layout,
            connect_layout,
            self.tree_widget,
            self.status_label,
            self.selection_layout(),
        )
        self.set_title("Import Tiled Dataset")

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def _connect(self):
        """Connect to the Tiled server and show the top-level catalog."""
        try:
            from tiled.client import from_uri
        except ImportError:
            report_error("Import Tiled Dataset",
                         NeXusError(
                             "The 'tiled' package is not installed.\n"
                             "Install it with:  pip install tiled  or "
                             "add the 'tiled' feature in your pixi environment."
                         ))
            return

        url = self.url_box.text().strip()
        if not url:
            self.status_label.setText("Please enter a server URL.")
            return

        self.status_label.setText("Connecting…")
        QtWidgets.QApplication.processEvents()

        try:
            import tiled.client.context as _tiled_ctx
            _orig_username = _tiled_ctx.username_input
            _orig_password = _tiled_ctx.password_input

            def _qt_username():
                text, ok = QtWidgets.QInputDialog.getText(
                    self, "Tiled Login", "Username:")
                if not ok:
                    raise NeXusError("Login cancelled")
                return text

            def _qt_password():
                text, ok = QtWidgets.QInputDialog.getText(
                    self, "Tiled Login", "Password:",
                    QtWidgets.QLineEdit.EchoMode.Password)
                if not ok:
                    raise NeXusError("Login cancelled")
                return text

            _tiled_ctx.username_input = _qt_username
            _tiled_ctx.password_input = _qt_password
            try:
                self._catalog = from_uri(url, remember_me=True)
            finally:
                _tiled_ctx.username_input = _orig_username
                _tiled_ctx.password_input = _orig_password
        except NeXusError as e:
            self.status_label.setText("")
            report_error("Import Tiled Dataset", e)
            return
        except Exception as e:
            self.status_label.setText("")
            report_error("Import Tiled Dataset", e)
            return

        self.tree_widget.clear()
        self._append_page(self.tree_widget.invisibleRootItem(),
                          self._catalog, path=[], offset=0)
        self.status_label.setText(f"Connected to {url}")

    # ------------------------------------------------------------------
    # Tree helpers
    # ------------------------------------------------------------------

    def _node_at_path(self, path):
        """Return the tiled node reached by traversing *path* from root."""
        node = self._catalog
        for key in path:
            node = node[key]
        return node

    def _is_container(self, node):
        """Return True if *node* is a browsable container (including xarray)."""
        from tiled.client.container import Container
        return isinstance(node, Container)

    @staticmethod
    def _metadata_tooltip(node):
        """Return a short metadata string suitable for a tree tooltip."""
        try:
            meta = node.metadata
            if not meta:
                return ""
            import json
            return json.dumps(dict(meta), indent=2, default=str)
        except Exception:
            return ""

    def _make_item(self, parent, label, path, node=None):
        """Create a tree item with tooltip and optional expand placeholder."""
        item = QtWidgets.QTreeWidgetItem(parent, [str(label)])
        item.setData(0, QtCore.Qt.ItemDataRole.UserRole, path)
        if node is not None:
            tip = self._metadata_tooltip(node)
            if tip:
                item.setToolTip(0, tip)
            if self._is_container(node):
                placeholder = QtWidgets.QTreeWidgetItem(item, ["Loading…"])
                placeholder.setData(0, QtCore.Qt.ItemDataRole.UserRole, None)
        return item

    def _append_page(self, parent_item, node, path, offset):
        """
        Append one page (PAGE_SIZE keys starting at *offset*) of *node*'s
        children to *parent_item*.

        After the items a "Load N more…" row is added when there are
        additional entries beyond this page.
        """
        try:
            page = node.items()[offset:offset + PAGE_SIZE]
        except Exception:
            try:
                page = list(node.items())[offset:offset + PAGE_SIZE]
            except Exception:
                return

        for key, child in sorted(page, key=lambda kv: natural_sort(str(kv[0]))):
            child_path = path + [key]
            self._make_item(parent_item, key, child_path, node=child)

        # "Load more" row if there are additional keys
        next_offset = offset + len(page)
        if len(page) == PAGE_SIZE:
            try:
                total = len(node)
            except Exception:
                total = None
            if total is None or next_offset < total:
                remaining = (f"{total - next_offset} remaining"
                             if total is not None else "more")
                load_item = QtWidgets.QTreeWidgetItem(
                    parent_item,
                    [f"⬇  Load {PAGE_SIZE} more  ({remaining})…"])
                load_item.setData(
                    0, QtCore.Qt.ItemDataRole.UserRole,
                    {'marker': _LOAD_MORE, 'path': path, 'offset': next_offset})
                load_item.setForeground(
                    0, QtWidgets.QApplication.palette().link())

    # ------------------------------------------------------------------
    # Signals
    # ------------------------------------------------------------------

    def _on_expand(self, item):
        """Lazily load children when a node is first expanded."""
        data = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
        # Only act on normal path nodes that still show a placeholder
        if not isinstance(data, list):
            return
        if item.childCount() != 1:
            return
        child0 = item.child(0)
        if child0.data(0, QtCore.Qt.ItemDataRole.UserRole) is not None:
            return  # already loaded

        item.removeChild(child0)
        try:
            node = self._node_at_path(data)
            # Ensure tooltip is set (may be missing if metadata wasn't
            # available at item-creation time)
            if not item.toolTip(0):
                tip = self._metadata_tooltip(node)
                if tip:
                    item.setToolTip(0, tip)
            if self._is_container(node):
                self._append_page(item, node, data, offset=0)
            # Leaf nodes show no children (expand arrow disappears)
        except Exception as e:
            QtWidgets.QTreeWidgetItem(item, [f"Error: {e}"])

    def _on_item_click(self, item, _column):
        """Handle clicks on 'Load more' rows."""
        data = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
        if not isinstance(data, dict) or data.get('marker') != _LOAD_MORE:
            return
        parent = item.parent() or self.tree_widget.invisibleRootItem()
        parent.removeChild(item)
        try:
            node = self._node_at_path(data['path'])
            self._append_page(parent, node, data['path'], data['offset'])
        except Exception as e:
            QtWidgets.QTreeWidgetItem(parent, [f"Error: {e}"])

    def _on_select(self):
        """Update the import name field and status when the user selects a node."""
        items = self.tree_widget.selectedItems()
        if not items:
            return
        data = items[0].data(0, QtCore.Qt.ItemDataRole.UserRole)
        if isinstance(data, list) and data:
            self.import_name = "/".join(str(p) for p in data)
            tip = items[0].toolTip(0)
            self.status_label.setText(tip[:200] if tip else "")

    # ------------------------------------------------------------------
    # Data conversion
    # ------------------------------------------------------------------

    def get_data(self):
        """Return a NeXus group built from the selected Tiled node."""
        items = self.tree_widget.selectedItems()
        if not items:
            raise NeXusError("No dataset selected")
        data = items[0].data(0, QtCore.Qt.ItemDataRole.UserRole)
        if not isinstance(data, list) or not data:
            raise NeXusError("No dataset selected")
        if self._catalog is None:
            raise NeXusError("Not connected to a Tiled server")
        try:
            node = self._node_at_path(data)
        except Exception as e:
            raise NeXusError(f"Cannot access node: {e}")
        return self._node_to_nexus(node, name=str(data[-1]))

    def _node_to_nexus(self, node, name="data"):
        """Recursively convert a Tiled node to a NeXus object."""
        from tiled.client.container import Container
        if isinstance(node, Container):
            group = NXcollection()
            self._attach_metadata(group, node)
            for key in node:
                try:
                    group[str(key)] = self._node_to_nexus(node[key],
                                                          name=str(key))
                except Exception:
                    pass
            return group

        try:
            data = node.read()
        except Exception as e:
            raise NeXusError(f"Cannot read data: {e}")

        try:
            import pandas as pd
            if isinstance(data, pd.DataFrame):
                group = NXcollection()
                self._attach_metadata(group, node)
                for col in data.columns:
                    group[str(col)] = NXfield(data[col].values, name=str(col))
                return group
        except ImportError:
            pass

        arr = np.asarray(data)
        result = NXdata(NXfield(arr, name=name))
        self._attach_metadata(result, node)
        return result

    @staticmethod
    def _attach_metadata(nx_obj, node):
        """Copy Tiled node metadata to NeXus attributes."""
        try:
            for k, v in node.metadata.items():
                try:
                    nx_obj.attrs[str(k)] = v
                except Exception:
                    pass
        except Exception:
            pass


import numpy as np
