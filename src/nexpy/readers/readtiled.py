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

* If the user has previously run ``tiled login <url>`` (or connected from
  another Tiled client), the cached token is reused automatically.
* If no cached token exists (or it has expired) and the session is
  non-interactive (headless GUI context), a ``CannotPrompt`` error is caught
  and the user is shown instructions to authenticate via the terminal first.

Tree browsing is lazy — container children are fetched only when a node is
expanded, so opening the dialog does not fetch the full catalog.
"""

import numpy as np
from nexusformat.nexus import (NeXusError, NXcollection, NXdata, NXfield,
                               NXgroup)

from nexpy.gui.importdialog import NXImportDialog
from nexpy.gui.pyqt import QtCore, QtWidgets
from nexpy.gui.utils import report_error
from nexpy.gui.widgets import NXLabel, NXLineEdit, NXPushButton

filetype = "Tiled Dataset"

DEFAULT_URL = "https://tiled.nsls2.bnl.gov/"


class ImportDialog(NXImportDialog):
    """Dialog to browse a Tiled catalog and import a dataset."""

    def __init__(self, parent=None):
        super().__init__(parent=parent)

        self._catalog = None   # root tiled client node after connect
        self._node_map = {}    # maps QTreeWidgetItem id → tiled path list

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
    # Connection helpers
    # ------------------------------------------------------------------

    def _connect(self):
        """Connect to the Tiled server and populate the top-level tree."""
        try:
            from tiled.client import from_uri
            from tiled.client.context import CannotPrompt
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
            self._catalog = from_uri(url, remember_me=True)
        except CannotPrompt:
            self.status_label.setText("")
            report_error(
                "Import Tiled Dataset",
                NeXusError(
                    "Authentication required.\n\n"
                    "NeXpy cannot prompt for credentials inside the GUI.\n"
                    "Please open a terminal and run:\n\n"
                    f"    tiled login {url}\n\n"
                    "Then click Connect again — the cached token will be "
                    "used automatically."
                )
            )
            return
        except Exception as e:
            self.status_label.setText("")
            report_error("Import Tiled Dataset", e)
            return

        self.tree_widget.clear()
        self._node_map.clear()

        # Populate top-level entries
        self._populate_children(self.tree_widget.invisibleRootItem(),
                                self._catalog, path=[])
        self.status_label.setText(f"Connected to {url}")

    # ------------------------------------------------------------------
    # Tree population (lazy)
    # ------------------------------------------------------------------

    def _node_at_path(self, path):
        """Return the tiled node reached by traversing *path* from root."""
        node = self._catalog
        for key in path:
            node = node[key]
        return node

    def _populate_children(self, parent_item, node, path):
        """
        Add immediate children of *node* as child items of *parent_item*.

        Containers get a dummy child so the expand arrow is shown; the real
        children are loaded in ``_on_expand``.
        """
        try:
            keys = list(node)
        except Exception:
            return

        for key in keys:
            child_path = path + [key]
            item = QtWidgets.QTreeWidgetItem(parent_item, [str(key)])
            item.setData(0, QtCore.Qt.ItemDataRole.UserRole, child_path)

            # Determine whether this child is itself a container
            try:
                child = node[key]
                is_container = hasattr(child, '__iter__') and not hasattr(
                    child, 'read')
                if is_container:
                    # Add a placeholder so the expand arrow appears
                    _placeholder = QtWidgets.QTreeWidgetItem(item,
                                                             ["Loading…"])
                    _placeholder.setData(
                        0, QtCore.Qt.ItemDataRole.UserRole, None)
            except Exception:
                pass

    def _on_expand(self, item):
        """Lazily load children when a container node is expanded."""
        path = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
        if path is None:
            return

        # Check if already loaded (more than one placeholder or real child)
        if item.childCount() == 1:
            child0 = item.child(0)
            placeholder_path = child0.data(0, QtCore.Qt.ItemDataRole.UserRole)
            if placeholder_path is None:
                # Remove placeholder and load real children
                item.removeChild(child0)
                try:
                    node = self._node_at_path(path)
                    self._populate_children(item, node, path)
                except Exception as e:
                    err = QtWidgets.QTreeWidgetItem(item,
                                                    [f"Error: {e}"])

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def _on_select(self):
        """Update the import name when the user selects a tree item."""
        items = self.tree_widget.selectedItems()
        if not items:
            return
        path = items[0].data(0, QtCore.Qt.ItemDataRole.UserRole)
        if path:
            self.import_name = "/".join(str(p) for p in path)

    # ------------------------------------------------------------------
    # Data conversion
    # ------------------------------------------------------------------

    def get_data(self):
        """Return a NeXus group built from the selected Tiled node."""
        items = self.tree_widget.selectedItems()
        if not items:
            raise NeXusError("No dataset selected")

        path = items[0].data(0, QtCore.Qt.ItemDataRole.UserRole)
        if not path:
            raise NeXusError("No dataset selected")

        if self._catalog is None:
            raise NeXusError("Not connected to a Tiled server")

        try:
            node = self._node_at_path(path)
        except Exception as e:
            raise NeXusError(f"Cannot access node: {e}")

        return self._node_to_nexus(node, name=str(path[-1]))

    def _node_to_nexus(self, node, name="data"):
        """Recursively convert a Tiled node to a NeXus object."""
        from tiled.client.array import ArrayClient
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

        # Leaf node — try to read as array
        try:
            data = node.read()
        except Exception as e:
            raise NeXusError(f"Cannot read data: {e}")

        # Pandas DataFrame → multiple NXfields in an NXcollection
        try:
            import pandas as pd
            if isinstance(data, pd.DataFrame):
                group = NXcollection()
                self._attach_metadata(group, node)
                for col in data.columns:
                    group[str(col)] = NXfield(data[col].values,
                                              name=str(col))
                return group
        except ImportError:
            pass

        arr = np.asarray(data)
        field = NXfield(arr, name=name)
        result = NXdata(field)
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
