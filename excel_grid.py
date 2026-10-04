# -*- coding: utf-8 -*-
"""
Editable Excel grid view for the preview pane.

Uses `tksheet` (https://pypi.org/project/tksheet/) for a real Excel-like
spreadsheet widget with in-place cell editing, multi-select, copy/paste,
sortable rows, and column resizing. If `tksheet` is not installed, the
frame shows a one-time install hint plus a button to open the file in
the system's default Excel-like app.

Dependencies:
  - openpyxl (already required by the rest of the app)
  - tksheet (NEW — install once with: pip install tksheet)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import tkinter
from tkinter import ttk, messagebox, simpledialog, colorchooser, font as tkfont

try:
    from tksheet import Sheet  # type: ignore
    _TKSHEET_OK = True
except ImportError:
    Sheet = None  # type: ignore
    _TKSHEET_OK = False

try:
    import openpyxl  # type: ignore
    _OPENPYXL_OK = True
except ImportError:
    openpyxl = None  # type: ignore
    _OPENPYXL_OK = False


def tksheet_available() -> bool:
    return _TKSHEET_OK


def openpyxl_available() -> bool:
    return _OPENPYXL_OK


class ExcelGridFrame(ttk.Frame):
    """A self-contained Tkinter Frame with sheet selector, save buttons, and
    an editable Excel-like grid. Embed it as a tab in your preview notebook.

    Public API:
        load(path)       — load an .xlsx/.xlsm into the grid
        clear()          — empty the grid
        is_dirty()       — True if user has edited cells without saving
        current_path     — the file currently loaded (or None)
    """

    def __init__(self, master: tkinter.Misc, on_status_change=None) -> None:
        super().__init__(master)
        self._on_status_change = on_status_change  # optional callback(text: str)
        self._wb = None  # type: ignore[assignment]
        self._path: str | None = None
        self._sheet_name: str | None = None
        self._dirty: bool = False
        self._loading: bool = False
        self._sheet: Sheet | None = None  # type: ignore[assignment]
        self._fallback_label = None
        self._fallback_text_var: tkinter.StringVar | None = None
        self._build_ui()

    # ----- public API -----

    @property
    def current_path(self) -> str | None:
        return self._path

    def load(self, path: str) -> None:
        """Load an .xlsx/.xlsm file into the grid."""
        self.clear()
        self._path = path
        self._loading = True
        try:
            if not _OPENPYXL_OK:
                self._show_fallback_message(
                    "openpyxl is not installed.\n\nRun:  pip install openpyxl"
                )
                return
            try:
                self._wb = openpyxl.load_workbook(path, data_only=True)
            except Exception as exc:
                self._show_fallback_message(f"Could not open Excel file:\n{exc}")
                self._wb = None
                return
            names = list(self._wb.sheetnames)
            if not names:
                self._show_fallback_message("Workbook has no sheets.")
                return
            self.sheet_combo.configure(values=names)
            self.sheet_combo.set(names[0])
            self._sheet_name = names[0]
            self._load_sheet_data(names[0])
        finally:
            self._loading = False

    def clear(self) -> None:
        self._wb = None
        self._path = None
        self._sheet_name = None
        self._dirty = False
        self.sheet_combo.configure(values=[])
        self.sheet_combo.set("")
        if _TKSHEET_OK and self._sheet is not None:
            try:
                self._sheet.headers([])
                self._sheet.set_sheet_data(
                    [], reset_col_positions=True, reset_row_positions=True
                )
            except Exception:
                pass
        if self._fallback_text_var is not None:
            self._fallback_text_var.set(
                "Open an Excel file (.xlsx / .xlsm) from the file tree to view it here."
            )
        self._update_status("No file loaded.")

    def is_dirty(self) -> bool:
        return self._dirty

    # ----- UI construction -----

    def _build_ui(self) -> None:
        # Top toolbar
        bar = ttk.Frame(self)
        bar.pack(side="top", fill="x")

        ttk.Label(bar, text="Sheet:").pack(side="left", padx=(8, 4), pady=4)
        self.sheet_combo = ttk.Combobox(bar, state="readonly", width=24)
        self.sheet_combo.pack(side="left", padx=(0, 8), pady=4)
        self.sheet_combo.bind("<<ComboboxSelected>>", self._on_sheet_changed)

        self.save_copy_btn = ttk.Button(
            bar, text="Save copy (_edited.xlsx)", command=self._save_copy
        )
        self.save_copy_btn.pack(side="left", padx=2, pady=4)

        self.save_inplace_btn = ttk.Button(
            bar, text="Save in place", command=self._save_in_place
        )
        self.save_inplace_btn.pack(side="left", padx=2, pady=4)

        self.open_excel_btn = ttk.Button(
            bar, text="Open in Excel", command=self._open_in_default_app
        )
        self.open_excel_btn.pack(side="left", padx=(8, 2), pady=4)

        self.status_var = tkinter.StringVar(value="No file loaded.")
        ttk.Label(bar, textvariable=self.status_var, foreground="#555").pack(
            side="left", padx=14
        )

        # Excel-style Home ribbon (font / alignment / number / styles / cells / editing)
        self._build_format_ribbon()

        # Find-in-page bar (Ctrl+F focuses it; type to highlight live)
        self._build_find_bar()

        # Grid area
        grid_holder = ttk.Frame(self)
        grid_holder.pack(side="top", fill="both", expand=True)

        if _TKSHEET_OK:
            self._sheet = Sheet(  # type: ignore[call-arg]
                grid_holder,
                headers=[],
                data=[],
                show_x_scrollbar=True,
                show_y_scrollbar=True,
            )
            try:
                # Enable the most useful interactions: select, copy/paste,
                # in-place edit, drag-select, column/row resize, undo.
                self._sheet.enable_bindings(
                    (
                        "single_select",
                        "drag_select",
                        "row_select",
                        "column_select",
                        "column_width_resize",
                        "row_height_resize",
                        "double_click_column_resize",
                        "double_click_row_resize",
                        "arrowkeys",
                        "right_click_popup_menu",
                        "rc_select",
                        "rc_insert_row",
                        "rc_delete_row",
                        "rc_insert_column",
                        "rc_delete_column",
                        "copy",
                        "cut",
                        "paste",
                        "delete",
                        "undo",
                        "edit_cell",
                        "edit_header",
                        "edit_index",
                        "ctrl_select",
                        "ctrl_click_select",
                        "shift_select",
                        "select_all",
                        "find",
                        "replace",
                    )
                )
            except Exception:
                # Older tksheet API: try the catch-all
                try:
                    self._sheet.enable_bindings("all")
                except Exception:
                    pass
            try:
                self._sheet.extra_bindings(
                    [
                        ("end_edit_cell", self._on_cell_edited),
                        ("end_paste", self._on_cell_edited),
                        ("end_delete", self._on_cell_edited),
                    ]
                )
            except Exception:
                pass
            self._sheet.pack(fill="both", expand=True)
            self._fallback_text_var = None
            # Install the full Excel-style right-click menu and shortcuts
            self._install_full_excel_menu()
        else:
            self._sheet = None
            self._fallback_text_var = tkinter.StringVar(
                value=(
                    "Editable spreadsheet view requires the 'tksheet' package.\n\n"
                    "Install once:\n"
                    "    pip install tksheet\n\n"
                    "Then restart the app. Until then, you can still:\n"
                    "  • View the table in the 'Single preview' tab\n"
                    "  • Click 'Open in Excel' below to edit it externally"
                )
            )
            self._fallback_label = ttk.Label(
                grid_holder,
                textvariable=self._fallback_text_var,
                justify="left",
                padding=24,
                font=("Segoe UI", 10),
            )
            self._fallback_label.pack(fill="both", expand=True)

    # ----- handlers -----

    def _on_sheet_changed(self, _event=None) -> None:
        if self._loading or self._wb is None:
            return
        new_sheet = self.sheet_combo.get()
        if not new_sheet:
            return
        if new_sheet == self._sheet_name:
            return
        if self._dirty:
            keep = messagebox.askyesno(
                "Switch sheet",
                "You have unsaved changes on this sheet.\n"
                "Switching sheets will discard them. Continue?",
                parent=self.winfo_toplevel(),
            )
            if not keep:
                self.sheet_combo.set(self._sheet_name or "")
                return
            self._dirty = False
        self._sheet_name = new_sheet
        self._load_sheet_data(new_sheet)

    def _load_sheet_data(self, sheet_name: str) -> None:
        if self._wb is None:
            return
        ws = self._wb[sheet_name]
        rows = list(ws.iter_rows(values_only=True))
        # Trim trailing empty rows
        while rows and all(
            v is None or (isinstance(v, str) and not v.strip()) for v in rows[-1]
        ):
            rows.pop()
        ncols = max((len(r) for r in rows), default=0) or (ws.max_column or 1)
        data: list[list[object]] = []
        for r in rows:
            r_list = list(r)
            if len(r_list) < ncols:
                r_list += [None] * (ncols - len(r_list))
            data.append(["" if v is None else v for v in r_list])
        headers = [self._col_letters(i + 1) for i in range(ncols)]

        if _TKSHEET_OK and self._sheet is not None:
            try:
                self._sheet.headers(headers)
                self._sheet.set_sheet_data(
                    data, reset_col_positions=True, reset_row_positions=True
                )
            except Exception as exc:
                self._show_fallback_message(f"Could not populate grid: {exc}")
                return
            try:
                self._sheet.set_all_cell_sizes_to_text()
            except Exception:
                pass
            # Apply Excel-side formatting (cell colours, column widths,
            # merged cells, bold-ish header). Best-effort: each piece is
            # wrapped so a single failure doesn\'t block the rest.
            self._apply_column_widths(ws, ncols)
            self._apply_cell_formatting(ws, len(data), ncols)
            self._apply_merged_cells(ws, len(data), ncols)
            self._apply_header_emphasis(ws, len(data), ncols)
        elif self._fallback_text_var is not None:
            fname = os.path.basename(self._path or "")
            self._fallback_text_var.set(
                f"File loaded: {fname}\n"
                f"Sheet: {sheet_name}  ·  {len(data)} rows × {ncols} cols\n\n"
                "The interactive grid needs the \'tksheet\' package, which isn\'t\n"
                "installed yet. From a Command Prompt, run:\n\n"
                "    pip install tksheet openpyxl\n\n"
                "…then close and reopen this app.\n\n"
                "Until then, you can still:\n"
                "  • View the table in the \'Single preview\' tab\n"
                "  • Click \'Open in Excel\' (top-right) to edit it externally"
            )

        self._update_status(f"{sheet_name} · {len(data)} rows × {ncols} cols")
        self._dirty = False

    # ---------- formatting helpers ----------

    @staticmethod
    def _argb_to_hex(rgb: object) -> str | None:
        """Convert openpyxl ARGB / RGB / theme colour into ``#RRGGBB`` or None."""
        if rgb is None:
            return None
        s = str(rgb).strip()
        if not s:
            return None
        # Drop alpha channel from ARGB ("FFRRGGBB") – it is alpha-on-white in
        # most spreadsheets and tksheet wants opaque colours.
        if len(s) == 8:
            s = s[2:]
        if len(s) != 6:
            return None
        try:
            int(s, 16)
        except ValueError:
            return None
        # Skip pure black-as-default (00000000) which is the no-fill sentinel
        if s.lower() == "000000":
            return None
        return "#" + s.upper()

    def _cell_bg(self, cell) -> str | None:
        try:
            fill = cell.fill
            if not fill:
                return None
            patt = getattr(fill, "patternType", None) or getattr(fill, "fill_type", None)
            if not patt or patt == "none":
                return None
            colour = getattr(fill, "fgColor", None) or getattr(fill, "start_color", None)
            if colour is None:
                return None
            if getattr(colour, "type", None) == "rgb":
                return self._argb_to_hex(colour.rgb)
            # Indexed / theme colours -> openpyxl resolves these via .value sometimes
            val = getattr(colour, "value", None)
            if val is not None:
                return self._argb_to_hex(val)
        except Exception:
            return None
        return None

    def _cell_fg(self, cell) -> str | None:
        try:
            font = cell.font
            if not font or font.color is None:
                return None
            colour = font.color
            if getattr(colour, "type", None) == "rgb":
                return self._argb_to_hex(colour.rgb)
            val = getattr(colour, "value", None)
            if val is not None:
                return self._argb_to_hex(val)
        except Exception:
            return None
        return None

    def _apply_cell_formatting(self, ws, nrows: int, ncols: int) -> None:
        if self._sheet is None:
            return
        try:
            for ri, row in enumerate(ws.iter_rows()):
                if ri >= nrows:
                    break
                for ci, cell in enumerate(row):
                    if ci >= ncols:
                        break
                    bg = self._cell_bg(cell)
                    fg = self._cell_fg(cell)
                    if not bg and not fg:
                        continue
                    try:
                        kw = {}
                        if bg:
                            kw["bg"] = bg
                        if fg:
                            kw["fg"] = fg
                        # tksheet 7.x signature
                        self._sheet.highlight_cells(row=ri, column=ci, **kw)
                    except Exception:
                        # Older API fallback: highlight_cells(row, column, bg, fg)
                        try:
                            self._sheet.highlight_cells(
                                row=ri, column=ci, bg=bg or "white", fg=fg or "black"
                            )
                        except Exception:
                            pass
        except Exception:
            pass

    def _apply_column_widths(self, ws, ncols: int) -> None:
        if self._sheet is None:
            return
        try:
            # Map openpyxl column letter -> width (in character units); convert
            # to pixels at the conventional rate of ~7 px per character for
            # 11pt Calibri. Cap so absurdly wide columns (300+) don\'t blow out.
            for col_letter, dim in ws.column_dimensions.items():
                if not getattr(dim, "width", None):
                    continue
                col_idx = self._col_letters_to_index(col_letter)
                if col_idx < 0 or col_idx >= ncols:
                    continue
                px = max(40, min(int(float(dim.width) * 7.2), 360))
                try:
                    self._sheet.column_width(column=col_idx, width=px)
                except TypeError:
                    # Older API takes positional args
                    try:
                        self._sheet.column_width(col_idx, px)
                    except Exception:
                        pass
                except Exception:
                    pass
        except Exception:
            pass

    def _apply_merged_cells(self, ws, nrows: int, ncols: int) -> None:
        if self._sheet is None:
            return
        try:
            ranges = list(getattr(ws.merged_cells, "ranges", []))
        except Exception:
            return
        for rng in ranges:
            try:
                # openpyxl uses 1-indexed inclusive bounds; tksheet uses
                # 0-indexed half-open for boxes (r1, c1, r2, c2).
                r1 = max(0, rng.min_row - 1)
                c1 = max(0, rng.min_col - 1)
                r2 = min(nrows, rng.max_row)
                c2 = min(ncols, rng.max_col)
                if r1 >= r2 or c1 >= c2:
                    continue
                try:
                    self._sheet.merge_cells(r1, c1, r2, c2)
                except TypeError:
                    # Newer API takes a tuple
                    try:
                        self._sheet.merge_cells((r1, c1, r2, c2))
                    except Exception:
                        pass
                except Exception:
                    pass
            except Exception:
                continue

    def _apply_header_emphasis(self, ws, nrows: int, ncols: int) -> None:
        """Give the first row a subtle grey background ONLY for cells that
        had no fill colour in the source workbook. Cells with their own fill
        keep it intact."""
        if self._sheet is None or nrows < 1 or ncols < 1:
            return
        try:
            first_row = next(ws.iter_rows(min_row=1, max_row=1, max_col=ncols))
        except Exception:
            return
        for ci, cell in enumerate(first_row):
            if ci >= ncols:
                break
            existing = self._cell_bg(cell)
            if existing:  # source already has a header colour — leave it alone
                continue
            try:
                self._sheet.highlight_cells(row=0, column=ci, bg="#F2F2F2", fg="#000000")
            except Exception:
                pass

    @staticmethod
    def _col_letters_to_index(letters: str) -> int:
        """Convert 'A' -> 0, 'Z' -> 25, 'AA' -> 26, etc. Returns -1 on bad input."""
        s = str(letters).strip().upper()
        if not s or not s.isalpha():
            return -1
        n = 0
        for ch in s:
            n = n * 26 + (ord(ch) - ord("A") + 1)
        return n - 1

    def _on_cell_edited(self, _event=None) -> None:
        self._dirty = True
        base = (self.status_var.get().split(" · ")[:2])
        head = " · ".join(base) if base else "Modified"
        self._update_status(f"{head} · Modified (unsaved)")

    # ----- saving -----

    def _can_save(self) -> bool:
        if not _OPENPYXL_OK:
            messagebox.showerror(
                "Save",
                "openpyxl is required to save Excel files.",
                parent=self.winfo_toplevel(),
            )
            return False
        if self._wb is None or not self._path or not self._sheet_name:
            messagebox.showerror(
                "Save", "No file loaded.", parent=self.winfo_toplevel()
            )
            return False
        if not _TKSHEET_OK or self._sheet is None:
            messagebox.showinfo(
                "Save",
                "Editable grid (tksheet) isn't installed; nothing to save here.",
                parent=self.winfo_toplevel(),
            )
            return False
        return True

    def _save_copy(self) -> None:
        if not self._can_save():
            return
        out_path = self._next_safe_out(self._edited_path())
        try:
            self._apply_grid_to_workbook()
            self._wb.save(out_path)  # type: ignore[union-attr]
        except Exception as exc:
            messagebox.showerror(
                "Save", f"Could not save: {exc}", parent=self.winfo_toplevel()
            )
            return
        self._dirty = False
        self._update_status(f"Saved copy: {os.path.basename(out_path)}")
        messagebox.showinfo(
            "Save copy",
            f"Saved to:\n{out_path}",
            parent=self.winfo_toplevel(),
        )

    def _save_in_place(self) -> None:
        if not self._can_save():
            return
        confirmed = messagebox.askyesno(
            "Save in place",
            "This will overwrite the original file.\n"
            "A .bak copy of the original is kept alongside.\n\nContinue?",
            parent=self.winfo_toplevel(),
        )
        if not confirmed:
            return
        bak = (self._path or "") + ".bak"
        try:
            if self._path and not os.path.exists(bak):
                shutil.copy2(self._path, bak)
            self._apply_grid_to_workbook()
            self._wb.save(self._path)  # type: ignore[union-attr]
        except Exception as exc:
            messagebox.showerror(
                "Save", f"Could not save: {exc}", parent=self.winfo_toplevel()
            )
            return
        self._dirty = False
        self._update_status("Saved in place (backup at .bak)")

    def _apply_grid_to_workbook(self) -> None:
        if (
            self._wb is None
            or not self._sheet_name
            or self._sheet is None
        ):
            return
        ws = self._wb[self._sheet_name]
        try:
            data = self._sheet.get_sheet_data()
        except Exception:
            data = []
        for ri, row in enumerate(data, start=1):
            for ci, v in enumerate(row, start=1):
                ws.cell(row=ri, column=ci, value=self._coerce_cell(v))

    @staticmethod
    def _coerce_cell(v):
        if v is None:
            return None
        if not isinstance(v, str):
            return v
        s = v.strip()
        if not s:
            return None
        # Don't coerce things that look like phone numbers / leading zeros / dates.
        # Just try int then float; otherwise keep as string.
        if s.startswith("0") and len(s) > 1 and s[1] != ".":
            return v  # Preserve "08801..." etc as text
        try:
            return int(s)
        except ValueError:
            try:
                return float(s)
            except ValueError:
                return v

    # ----- helpers -----

    def _edited_path(self) -> str:
        p = Path(self._path or "edited.xlsx")
        return str(p.with_name(f"{p.stem}_edited{p.suffix}"))

    @staticmethod
    def _next_safe_out(path: str) -> str:
        p = Path(path)
        if not p.exists():
            return str(p)
        n = 1
        while True:
            cand = p.with_name(f"{p.stem}-{n}{p.suffix}")
            if not cand.exists():
                return str(cand)
            n += 1

    @staticmethod
    def _col_letters(n: int) -> str:
        out = ""
        while n > 0:
            n, r = divmod(n - 1, 26)
            out = chr(ord("A") + r) + out
        return out

    def _show_fallback_message(self, text: str) -> None:
        if self._fallback_text_var is not None:
            self._fallback_text_var.set(text)
        else:
            self._update_status(text)

    def _update_status(self, text: str) -> None:
        try:
            self.status_var.set(text)
        except tkinter.TclError:
            pass
        if self._on_status_change:
            try:
                self._on_status_change(text)
            except Exception:
                pass

    def _open_in_default_app(self) -> None:
        if not self._path:
            return
        if sys.platform == "win32":
            try:
                os.startfile(self._path)  # type: ignore[attr-defined]
                return
            except OSError:
                pass
        if sys.platform == "darwin":
            try:
                subprocess.Popen(["open", self._path])
                return
            except OSError:
                pass
        # Linux / fallback
        for cmd in (["xdg-open"], ["gio", "open"]):
            try:
                subprocess.Popen(cmd + [self._path])
                return
            except OSError:
                continue

    # ============================================================
    # Full Excel-style right-click menu + keyboard shortcuts
    # ============================================================

    def _install_full_excel_menu(self) -> None:
        """Add a comprehensive Excel-like context menu and keyboard shortcuts.

        All commands are added on top of tksheet's built-in popup; the built-in
        Cut / Copy / Paste / Clear / Undo entries remain. Each handler is
        defensive — if a tksheet API isn't present in the installed version we
        fall back to a no-op rather than crashing.
        """
        if not _TKSHEET_OK or self._sheet is None:
            return
        sh = self._sheet

        def _add(label, cmd):
            """Try every flavour of tksheet's add-command API."""
            for kwargs in (
                dict(table_menu=True, index_menu=True, header_menu=True,
                     empty_space_menu=True),
                dict(),
            ):
                try:
                    sh.popup_menu_add_command(label, cmd, **kwargs)
                    return
                except TypeError:
                    continue
                except Exception:
                    return

        def _sep():
            _add("─" * 28, lambda: None)

        # ---- Insert / Delete ------------------------------------------------
        _sep()
        _add("Insert row above",      self._insert_row_above)
        _add("Insert row below",      self._insert_row_below)
        _add("Insert column to left", self._insert_col_left)
        _add("Insert column to right", self._insert_col_right)
        _add("Delete row(s)",         self._delete_rows)
        _add("Delete column(s)",      self._delete_cols)

        # ---- Sort & Filter --------------------------------------------------
        _sep()
        _add("Sort A → Z (this column)", lambda: self._sort_by_column(False))
        _add("Sort Z → A (this column)", lambda: self._sort_by_column(True))
        _add("Toggle filter row",        self._toggle_filter_row)

        # ---- Format ---------------------------------------------------------
        _sep()
        _add("Bold (Ctrl+B)",       self._toggle_bold)
        _add("Italic (Ctrl+I)",     self._toggle_italic)
        _add("Align left",          lambda: self._set_alignment("w"))
        _add("Align center",        lambda: self._set_alignment("center"))
        _add("Align right",         lambda: self._set_alignment("e"))
        _add("Fill colour…",        self._pick_fill_colour)
        _add("Font colour…",        self._pick_font_colour)
        _add("Number format…",      self._set_number_format)
        _add("Clear formatting",    self._clear_formatting)

        # ---- Sizing & visibility -------------------------------------------
        _sep()
        _add("Row height…",         self._set_row_height_dialog)
        _add("Column width…",       self._set_col_width_dialog)
        _add("AutoFit column width", self._autofit_columns)
        _add("Hide row(s)",         self._hide_rows)
        _add("Hide column(s)",      self._hide_cols)
        _add("Unhide all",          self._unhide_all)

        # ---- Freeze ---------------------------------------------------------
        _sep()
        _add("Freeze top row",      lambda: self._freeze("rows", 1))
        _add("Freeze first column", lambda: self._freeze("cols", 1))
        _add("Freeze panes here",   self._freeze_at_selection)
        _add("Unfreeze panes",      self._unfreeze)

        # ---- Find / Replace / Hyperlink ------------------------------------
        _sep()
        _add("Find & Replace… (Ctrl+H)", lambda: self._open_find_replace(True))
        _add("Find… (Ctrl+F)",      lambda: self._open_find_replace(False))
        _add("Hyperlink…",          self._set_hyperlink)
        _add("Insert comment / note", self._insert_comment)

        # ---- Formulas / quick calc -----------------------------------------
        _sep()
        _add("AutoSum below selection", self._autosum_below)
        _add("AutoSum to the right",    self._autosum_right)
        _add("Average below selection", self._autoaverage_below)
        _add("Count of selection",      self._count_selection)
        _add("Insert function…",        self._insert_function)

        # ---- Korean transliteration ----------------------------------------
        _sep()
        _add("한 Translate to Korean (replace)",
             lambda: self._translate_to_korean("replace"))
        _add("한 Translate to Korean (next column)",
             lambda: self._translate_to_korean("next_col"))

        # ---- Edit ----------------------------------------------------------
        _sep()
        _add("Undo (Ctrl+Z)",       self._undo)
        _add("Redo (Ctrl+Y)",       self._redo)
        _add("Select all (Ctrl+A)", self._select_all)

        # ---- Keyboard shortcuts --------------------------------------------
        for seq, fn in (
            ("<Control-b>",  self._toggle_bold),
            ("<Control-B>",  self._toggle_bold),
            ("<Control-i>",  self._toggle_italic),
            ("<Control-I>",  self._toggle_italic),
            ("<Control-f>",  lambda e=None: self._open_find_replace(False)),
            ("<Control-h>",  lambda e=None: self._open_find_replace(True)),
            ("<Control-z>",  lambda e=None: self._undo()),
            ("<Control-Z>",  lambda e=None: self._undo()),
            ("<Control-y>",  lambda e=None: self._redo()),
            ("<Control-Y>",  lambda e=None: self._redo()),
            ("<Control-a>",  lambda e=None: self._select_all()),
            ("<Control-l>",  lambda e=None: self._toggle_filter_row()),
            ("<F2>",          lambda e=None: self._begin_edit_active()),
        ):
            try:
                sh.bind(seq, lambda e, f=fn: (f(), "break")[1])
            except Exception:
                pass

    # ---------- selection helpers ---------------------------------------

    def _active_cell(self):
        """Return (row, col) of the active cell, or (0, 0) as a safe default."""
        sh = self._sheet
        try:
            sel = sh.get_currently_selected()
        except Exception:
            sel = None
        if sel is None:
            return 0, 0
        # tksheet 7.x returns a namedtuple-like with .row/.column;
        # older versions return a tuple (type, row, col) or (row, col, type).
        for r_attr, c_attr in (("row", "column"), ("from_r", "from_c")):
            r = getattr(sel, r_attr, None)
            c = getattr(sel, c_attr, None)
            if r is not None and c is not None:
                return int(r), int(c)
        if isinstance(sel, (tuple, list)) and len(sel) >= 2:
            nums = [x for x in sel if isinstance(x, int)]
            if len(nums) >= 2:
                return int(nums[0]), int(nums[1])
        return 0, 0

    def _selected_rows(self):
        try:
            return sorted(self._sheet.get_selected_rows()) or [self._active_cell()[0]]
        except Exception:
            return [self._active_cell()[0]]

    def _selected_cols(self):
        try:
            return sorted(self._sheet.get_selected_columns()) or [self._active_cell()[1]]
        except Exception:
            return [self._active_cell()[1]]

    def _selected_cells(self):
        try:
            cells = list(self._sheet.get_selected_cells())
            if cells:
                return cells
        except Exception:
            pass
        try:
            box = self._sheet.get_all_selection_boxes()
            if box:
                # box: list of (r1,c1,r2,c2)
                out = []
                for r1, c1, r2, c2 in box:
                    for r in range(r1, r2):
                        for c in range(c1, c2):
                            out.append((r, c))
                return out
        except Exception:
            pass
        return [self._active_cell()]

    # ---------- insert / delete -----------------------------------------

    def _insert_row_above(self):
        r = self._active_cell()[0]
        self._try_call(
            (lambda: self._sheet.insert_row(idx=r)),
            (lambda: self._sheet.insert_row(row=r)),
            (lambda: self._sheet.insert_row(idx=r, row=None)),
        )
        self._mark_dirty()

    def _insert_row_below(self):
        r = self._active_cell()[0] + 1
        self._try_call(
            (lambda: self._sheet.insert_row(idx=r)),
            (lambda: self._sheet.insert_row(row=r)),
        )
        self._mark_dirty()

    def _insert_col_left(self):
        c = self._active_cell()[1]
        self._try_call(
            (lambda: self._sheet.insert_column(idx=c)),
            (lambda: self._sheet.insert_column(column=c)),
        )
        self._refresh_headers()
        self._mark_dirty()

    def _insert_col_right(self):
        c = self._active_cell()[1] + 1
        self._try_call(
            (lambda: self._sheet.insert_column(idx=c)),
            (lambda: self._sheet.insert_column(column=c)),
        )
        self._refresh_headers()
        self._mark_dirty()

    def _delete_rows(self):
        rows = self._selected_rows()
        if not rows:
            return
        for r in sorted(rows, reverse=True):
            self._try_call(
                (lambda r=r: self._sheet.del_row(r)),
                (lambda r=r: self._sheet.del_rows(rows=[r])),
            )
        self._mark_dirty()

    def _delete_cols(self):
        cols = self._selected_cols()
        if not cols:
            return
        for c in sorted(cols, reverse=True):
            self._try_call(
                (lambda c=c: self._sheet.del_column(c)),
                (lambda c=c: self._sheet.del_columns(columns=[c])),
            )
        self._refresh_headers()
        self._mark_dirty()

    def _refresh_headers(self):
        try:
            data = self._sheet.get_sheet_data()
            ncols = max((len(r) for r in data), default=0)
            self._sheet.headers([self._col_letters(i + 1) for i in range(ncols)])
        except Exception:
            pass

    # ---------- sort / filter -------------------------------------------

    def _sort_by_column(self, descending: bool):
        col = self._active_cell()[1]
        try:
            data = self._sheet.get_sheet_data()
        except Exception:
            return
        if not data:
            return

        def key(row):
            v = row[col] if col < len(row) else ""
            try:
                return (0, float(v))
            except (TypeError, ValueError):
                return (1, str(v).lower() if v is not None else "")

        new_data = sorted(data, key=key, reverse=descending)
        try:
            self._sheet.set_sheet_data(new_data, reset_col_positions=False,
                                       reset_row_positions=False)
        except Exception:
            try:
                self._sheet.set_sheet_data(new_data)
            except Exception:
                return
        self._mark_dirty()

    def _toggle_filter_row(self):
        """Toggle a frozen header that acts as a one-click sort row."""
        try:
            current = getattr(self._sheet, "_cw_filter_on", False)
            if current:
                self._sheet.dehighlight_rows([0])
                self._sheet._cw_filter_on = False
                self._update_status("Filter row: off")
            else:
                self._sheet.highlight_rows([0], bg="#FFF4CE", fg="#000000")
                self._sheet._cw_filter_on = True
                self._update_status("Filter row: on (right-click a column to sort)")
        except Exception:
            pass

    # ---------- formatting ----------------------------------------------

    def _toggle_bold(self):
        self._toggle_font_style("bold")

    def _toggle_italic(self):
        self._toggle_font_style("italic")

    def _toggle_font_style(self, which: str):
        sh = self._sheet
        # tksheet exposes .font() for the whole grid; per-cell overrides via
        # the named-spans API (7.x) or the older set_cell_options API.
        cells = self._selected_cells()
        if not cells:
            return
        # Pull a sensible default font family/size from the widget.
        family, size = "Calibri", 11
        try:
            f = sh.font()
            if f:
                family = f[0] or family
                size = f[1] or size
        except Exception:
            pass
        # Track per-cell style on a private dict so we can toggle.
        styles = getattr(sh, "_cw_cell_styles", None)
        if styles is None:
            styles = {}
            sh._cw_cell_styles = styles
        for r, c in cells:
            cur = styles.get((r, c), set())
            if which in cur:
                cur.discard(which)
            else:
                cur.add(which)
            styles[(r, c)] = cur
            mods = " ".join(sorted(cur)) if cur else ""
            try:
                # tksheet 7.x: span().font(...) is the canonical API
                span = sh.span(r, c)
                span.font((family, size, mods)) if mods else span.font((family, size, ""))
            except Exception:
                # Older API fallback
                try:
                    sh.set_cell_options(r, c, font=(family, size, mods))
                except Exception:
                    pass
        try:
            sh.refresh()
        except Exception:
            pass
        self._mark_dirty()

    def _set_alignment(self, where: str):
        cells = self._selected_cells()
        for r, c in cells:
            try:
                sh = self._sheet
                try:
                    sh.align_cells(row=r, column=c, align=where)
                except Exception:
                    sh.span(r, c).align(where)
            except Exception:
                pass
        try:
            self._sheet.refresh()
        except Exception:
            pass
        self._mark_dirty()

    def _pick_fill_colour(self):
        clr = colorchooser.askcolor(title="Pick fill colour", parent=self.winfo_toplevel())
        if not clr or not clr[1]:
            return
        hex_color = clr[1]
        for r, c in self._selected_cells():
            try:
                self._sheet.highlight_cells(row=r, column=c, bg=hex_color)
            except Exception:
                pass
        self._mark_dirty()

    def _pick_font_colour(self):
        clr = colorchooser.askcolor(title="Pick font colour", parent=self.winfo_toplevel())
        if not clr or not clr[1]:
            return
        hex_color = clr[1]
        for r, c in self._selected_cells():
            try:
                self._sheet.highlight_cells(row=r, column=c, fg=hex_color)
            except Exception:
                pass
        self._mark_dirty()

    def _set_number_format(self):
        fmt = simpledialog.askstring(
            "Number format",
            "Enter a Python format spec (e.g. ',.2f' for thousands+2dp, "
            "'.0%' for percent, '$#,##0.00' is shown as currency):",
            parent=self.winfo_toplevel(),
        )
        if fmt is None:
            return
        cells = self._selected_cells()
        for r, c in cells:
            try:
                raw = self._sheet.get_cell_data(r, c)
            except Exception:
                continue
            try:
                num = float(str(raw).replace(",", "").replace("$", "").rstrip("%"))
            except (TypeError, ValueError):
                continue
            try:
                if fmt.startswith("$"):
                    rendered = "$" + format(num, fmt[1:] or ",.2f")
                else:
                    rendered = format(num, fmt)
                self._sheet.set_cell_data(r, c, rendered)
            except Exception:
                pass
        self._mark_dirty()

    def _clear_formatting(self):
        for r, c in self._selected_cells():
            try:
                self._sheet.dehighlight_cells(row=r, column=c)
            except Exception:
                pass
            try:
                self._sheet.span(r, c).font(("Calibri", 11, ""))
            except Exception:
                pass
        try:
            self._sheet.refresh()
        except Exception:
            pass
        self._mark_dirty()

    # ---------- sizing / visibility -------------------------------------

    def _set_row_height_dialog(self):
        h = simpledialog.askinteger(
            "Row height", "Pixels:", minvalue=12, maxvalue=600,
            initialvalue=22, parent=self.winfo_toplevel(),
        )
        if h is None:
            return
        for r in self._selected_rows():
            try:
                self._sheet.row_height(row=r, height=h)
            except Exception:
                try:
                    self._sheet.row_height(r, h)
                except Exception:
                    pass

    def _set_col_width_dialog(self):
        w = simpledialog.askinteger(
            "Column width", "Pixels:", minvalue=20, maxvalue=1200,
            initialvalue=120, parent=self.winfo_toplevel(),
        )
        if w is None:
            return
        for c in self._selected_cols():
            try:
                self._sheet.column_width(column=c, width=w)
            except Exception:
                try:
                    self._sheet.column_width(c, w)
                except Exception:
                    pass

    def _autofit_columns(self):
        try:
            self._sheet.set_all_cell_sizes_to_text()
        except Exception:
            pass

    def _hide_rows(self):
        rows = self._selected_rows()
        try:
            self._sheet.hide_rows(rows)
        except Exception:
            try:
                for r in rows:
                    self._sheet.hide_rows(r)
            except Exception:
                pass

    def _hide_cols(self):
        cols = self._selected_cols()
        try:
            self._sheet.hide_columns(cols)
        except Exception:
            try:
                for c in cols:
                    self._sheet.hide_columns(c)
            except Exception:
                pass

    def _unhide_all(self):
        for fn in ("display_rows", "show_rows"):
            try:
                getattr(self._sheet, fn)("all")
                break
            except Exception:
                continue
        for fn in ("display_columns", "show_columns"):
            try:
                getattr(self._sheet, fn)("all")
                break
            except Exception:
                continue

    # ---------- freeze panes --------------------------------------------

    def _freeze(self, axis: str, n: int):
        sh = self._sheet
        try:
            if axis == "rows":
                for fn in ("freeze_rows", "set_frozen_rows"):
                    if hasattr(sh, fn):
                        getattr(sh, fn)(n)
                        return
            else:
                for fn in ("freeze_columns", "set_frozen_columns"):
                    if hasattr(sh, fn):
                        getattr(sh, fn)(n)
                        return
        except Exception:
            pass

    def _freeze_at_selection(self):
        r, c = self._active_cell()
        self._freeze("rows", max(0, r))
        self._freeze("cols", max(0, c))

    def _unfreeze(self):
        self._freeze("rows", 0)
        self._freeze("cols", 0)

    # ---------- find / replace / hyperlink / comment --------------------

    def _open_find_replace(self, with_replace: bool):
        top = tkinter.Toplevel(self.winfo_toplevel())
        top.title("Find & Replace" if with_replace else "Find")
        top.transient(self.winfo_toplevel())
        top.resizable(False, False)
        ttk.Label(top, text="Find:").grid(row=0, column=0, padx=6, pady=6, sticky="e")
        find_var = tkinter.StringVar()
        ttk.Entry(top, textvariable=find_var, width=32).grid(row=0, column=1, padx=6, pady=6)
        repl_var = tkinter.StringVar()
        if with_replace:
            ttk.Label(top, text="Replace:").grid(row=1, column=0, padx=6, pady=6, sticky="e")
            ttk.Entry(top, textvariable=repl_var, width=32).grid(row=1, column=1, padx=6, pady=6)
        match_case = tkinter.BooleanVar(value=False)
        ttk.Checkbutton(top, text="Match case", variable=match_case).grid(
            row=2, column=1, sticky="w", padx=6
        )

        def do_find_next():
            needle = find_var.get()
            if not needle:
                return
            data = self._sheet.get_sheet_data()
            r0, c0 = self._active_cell()
            ncols = max((len(r) for r in data), default=0)
            total = len(data) * (ncols or 1)
            start = r0 * (ncols or 1) + c0 + 1
            for k in range(total):
                idx = (start + k) % total
                rr = idx // (ncols or 1)
                cc = idx % (ncols or 1)
                cell = data[rr][cc] if cc < len(data[rr]) else ""
                hay = str(cell) if cell is not None else ""
                if (needle in hay) if match_case.get() else (needle.lower() in hay.lower()):
                    try:
                        self._sheet.see(row=rr, column=cc)
                        self._sheet.select_cell(row=rr, column=cc)
                    except Exception:
                        pass
                    return
            messagebox.showinfo("Find", f"'{needle}' not found.", parent=top)

        def do_replace_all():
            needle = find_var.get()
            repl = repl_var.get()
            if not needle:
                return
            data = self._sheet.get_sheet_data()
            count = 0
            for rr, row in enumerate(data):
                for cc, val in enumerate(row):
                    s = "" if val is None else str(val)
                    if match_case.get():
                        if needle in s:
                            new = s.replace(needle, repl)
                            self._sheet.set_cell_data(rr, cc, new)
                            count += s.count(needle)
                    else:
                        # case-insensitive replace
                        idx = s.lower().find(needle.lower())
                        if idx >= 0:
                            new_chunks = []
                            i = 0
                            ln = len(needle)
                            while i < len(s):
                                j = s.lower().find(needle.lower(), i)
                                if j < 0:
                                    new_chunks.append(s[i:])
                                    break
                                new_chunks.append(s[i:j])
                                new_chunks.append(repl)
                                i = j + ln
                                count += 1
                            self._sheet.set_cell_data(rr, cc, "".join(new_chunks))
            try:
                self._sheet.refresh()
            except Exception:
                pass
            if count:
                self._mark_dirty()
            messagebox.showinfo("Replace", f"Made {count} replacement(s).", parent=top)

        btns = ttk.Frame(top)
        btns.grid(row=3, column=0, columnspan=2, pady=(0, 8))
        ttk.Button(btns, text="Find next", command=do_find_next).pack(side="left", padx=4)
        if with_replace:
            ttk.Button(btns, text="Replace all", command=do_replace_all).pack(side="left", padx=4)
        ttk.Button(btns, text="Close", command=top.destroy).pack(side="left", padx=4)

    def _set_hyperlink(self):
        url = simpledialog.askstring(
            "Hyperlink", "URL or path:",
            parent=self.winfo_toplevel(),
        )
        if not url:
            return
        for r, c in self._selected_cells():
            try:
                self._sheet.create_hyperlink(row=r, column=c, link=url)
            except Exception:
                # Fallback: just paint blue + underlined text
                try:
                    self._sheet.highlight_cells(row=r, column=c, fg="#0563C1")
                    cur = self._sheet.get_cell_data(r, c)
                    if not cur:
                        self._sheet.set_cell_data(r, c, url)
                except Exception:
                    pass
        self._mark_dirty()

    def _insert_comment(self):
        note = simpledialog.askstring(
            "Comment / note", "Text:", parent=self.winfo_toplevel()
        )
        if note is None:
            return
        notes = getattr(self._sheet, "_cw_notes", None)
        if notes is None:
            notes = {}
            self._sheet._cw_notes = notes
        for r, c in self._selected_cells():
            notes[(r, c)] = note
            try:
                self._sheet.create_note(row=r, column=c, note=note)
            except Exception:
                # Visual fallback: a yellow background means "has note".
                try:
                    self._sheet.highlight_cells(row=r, column=c, bg="#FFF4CE")
                except Exception:
                    pass

    # ---------- formulas ------------------------------------------------

    def _autosum_below(self):
        rows = self._selected_rows()
        cols = self._selected_cols()
        if not rows or not cols:
            return
        try:
            data = self._sheet.get_sheet_data()
        except Exception:
            return
        target_row = max(rows) + 1
        # Ensure the row exists
        while len(data) <= target_row:
            data.append([])
        self._undo_push(self._capture_cells([(target_row, c) for c in cols]))
        for c in cols:
            total = 0.0
            for r in rows:
                v = data[r][c] if r < len(data) and c < len(data[r]) else ""
                try:
                    total += float(str(v).replace(",", ""))
                except (TypeError, ValueError):
                    pass
            try:
                self._sheet.set_cell_data(target_row, c, total)
            except Exception:
                pass
        self._mark_dirty()

    def _autosum_right(self):
        rows = self._selected_rows()
        cols = self._selected_cols()
        if not rows or not cols:
            return
        try:
            data = self._sheet.get_sheet_data()
        except Exception:
            return
        target_col = max(cols) + 1
        for r in rows:
            total = 0.0
            for c in cols:
                v = data[r][c] if r < len(data) and c < len(data[r]) else ""
                try:
                    total += float(str(v).replace(",", ""))
                except (TypeError, ValueError):
                    pass
            try:
                self._sheet.set_cell_data(r, target_col, total)
            except Exception:
                pass
        self._mark_dirty()

    def _autoaverage_below(self):
        rows = self._selected_rows()
        cols = self._selected_cols()
        if not rows or not cols:
            return
        try:
            data = self._sheet.get_sheet_data()
        except Exception:
            return
        target_row = max(rows) + 1
        while len(data) <= target_row:
            data.append([])
        for c in cols:
            vals = []
            for r in rows:
                v = data[r][c] if r < len(data) and c < len(data[r]) else ""
                try:
                    vals.append(float(str(v).replace(",", "")))
                except (TypeError, ValueError):
                    pass
            if vals:
                try:
                    self._sheet.set_cell_data(
                        target_row, c, round(sum(vals) / len(vals), 6)
                    )
                except Exception:
                    pass
        self._mark_dirty()

    def _count_selection(self):
        cells = self._selected_cells()
        non_empty = 0
        numeric = 0
        for r, c in cells:
            try:
                v = self._sheet.get_cell_data(r, c)
            except Exception:
                continue
            if v not in (None, ""):
                non_empty += 1
                try:
                    float(str(v).replace(",", ""))
                    numeric += 1
                except (TypeError, ValueError):
                    pass
        messagebox.showinfo(
            "Count",
            f"Selected: {len(cells)}\n"
            f"Non-empty: {non_empty}\n"
            f"Numeric: {numeric}",
            parent=self.winfo_toplevel(),
        )

    _FUNCTIONS = {
        "SUM":     "=SUM({range})",
        "AVERAGE": "=AVERAGE({range})",
        "MIN":     "=MIN({range})",
        "MAX":     "=MAX({range})",
        "COUNT":   "=COUNT({range})",
        "COUNTA":  "=COUNTA({range})",
        "IF":      "=IF(condition, value_if_true, value_if_false)",
        "VLOOKUP": "=VLOOKUP(lookup_value, table_array, col_index, FALSE)",
        "HLOOKUP": "=HLOOKUP(lookup_value, table_array, row_index, FALSE)",
        "INDEX":   "=INDEX(array, row_num, col_num)",
        "MATCH":   "=MATCH(lookup_value, lookup_array, 0)",
        "CONCAT":  "=CONCAT({range})",
        "LEFT":    "=LEFT(text, num_chars)",
        "RIGHT":   "=RIGHT(text, num_chars)",
        "MID":     "=MID(text, start_num, num_chars)",
        "LEN":     "=LEN(text)",
        "TRIM":    "=TRIM(text)",
        "UPPER":   "=UPPER(text)",
        "LOWER":   "=LOWER(text)",
        "PROPER":  "=PROPER(text)",
        "TODAY":   "=TODAY()",
        "NOW":     "=NOW()",
        "ROUND":   "=ROUND(number, num_digits)",
        "ABS":     "=ABS(number)",
        "SUMIF":   "=SUMIF(range, criteria, [sum_range])",
        "COUNTIF": "=COUNTIF(range, criteria)",
        "IFERROR": "=IFERROR(value, value_if_error)",
    }

    def _insert_function(self):
        names = sorted(self._FUNCTIONS)
        prompt = (
            "Type a function name to insert its formula skeleton:\n\n"
            + ", ".join(names)
        )
        choice = simpledialog.askstring(
            "Insert function", prompt, parent=self.winfo_toplevel()
        )
        if not choice:
            return
        key = choice.strip().upper()
        if key not in self._FUNCTIONS:
            messagebox.showwarning(
                "Insert function",
                f"Unknown function: {choice}",
                parent=self.winfo_toplevel(),
            )
            return
        template = self._FUNCTIONS[key]
        # Substitute {range} with the selected box in A1 notation, when present.
        if "{range}" in template:
            try:
                box = self._sheet.get_all_selection_boxes()
            except Exception:
                box = None
            if box:
                r1, c1, r2, c2 = box[0]
                a1 = (
                    f"{self._col_letters(c1 + 1)}{r1 + 1}:"
                    f"{self._col_letters(c2)}{r2}"
                )
            else:
                a1 = "A1:A10"
            template = template.replace("{range}", a1)
        r, c = self._active_cell()
        try:
            self._sheet.set_cell_data(r, c, template)
        except Exception:
            pass
        self._mark_dirty()

    # ---------- misc -----------------------------------------------------

    # ---------- internal undo/redo stack (programmatic mutations) -----

    def _undo_init(self):
        if not hasattr(self, "_undo_stack"):
            self._undo_stack: list[dict] = []
            self._redo_stack: list[dict] = []
            self._undo_max = 100

    def _capture_cells(self, cells):
        """Return {(r, c): value} for the given iterable of (r, c)."""
        snap: dict = {}
        if not cells:
            return snap
        try:
            data = self._sheet.get_sheet_data()
        except Exception:
            data = []
        for r, c in cells:
            if 0 <= r < len(data) and 0 <= c < len(data[r]):
                snap[(r, c)] = data[r][c]
            else:
                snap[(r, c)] = ""
        return snap

    def _undo_push(self, before: dict):
        """Push a pre-mutation snapshot onto the undo stack."""
        self._undo_init()
        if not before:
            return
        self._undo_stack.append(before)
        self._redo_stack.clear()
        while len(self._undo_stack) > self._undo_max:
            self._undo_stack.pop(0)

    def _apply_snapshot(self, snap: dict):
        """Write the values in a snapshot back into the sheet."""
        for (r, c), v in snap.items():
            try:
                self._sheet.set_cell_data(r, c, v)
            except Exception:
                pass
        try:
            self._sheet.refresh()
        except Exception:
            pass

    def _undo(self):
        """Undo: try internal stack first, then tksheet's own undo."""
        self._undo_init()
        if self._undo_stack:
            snap = self._undo_stack.pop()
            # Capture the current values so redo can re-apply
            self._redo_stack.append(self._capture_cells(snap.keys()))
            self._apply_snapshot(snap)
            self._update_status(f"Undone — {len(self._undo_stack)} step(s) left.")
            return
        # Fall back to tksheet's built-in undo
        sh = self._sheet
        for fn in ("undo", "ctrl_z", "undo_event"):
            if hasattr(sh, fn):
                try:
                    getattr(sh, fn)()
                    self._update_status("Undone (tksheet).")
                    return
                except Exception:
                    continue
        self._update_status("Nothing to undo.")

    def _redo(self):
        """Redo: try internal stack first, then tksheet's own redo."""
        self._undo_init()
        if self._redo_stack:
            snap = self._redo_stack.pop()
            self._undo_stack.append(self._capture_cells(snap.keys()))
            self._apply_snapshot(snap)
            self._update_status(f"Redone — {len(self._redo_stack)} step(s) left.")
            return
        sh = self._sheet
        for fn in ("redo", "ctrl_y", "redo_event"):
            if hasattr(sh, fn):
                try:
                    getattr(sh, fn)()
                    self._update_status("Redone (tksheet).")
                    return
                except Exception:
                    continue
        self._update_status("Nothing to redo.")

    # ---------- Korean (Hangul) transliterator -----------------------

    # Hangul Jamo composition: syllable = 0xAC00 + (initial * 588) + (medial * 28) + final
    # Initial (choseong) indices ㄱ=0, ㄲ=1, ㄴ=2, ㄷ=3, ㄸ=4, ㄹ=5, ㅁ=6, ㅂ=7,
    #   ㅃ=8, ㅅ=9, ㅆ=10, ㅇ=11(silent), ㅈ=12, ㅉ=13, ㅊ=14, ㅋ=15, ㅌ=16, ㅍ=17, ㅎ=18
    # Medial (jungseong)  ㅏ=0, ㅐ=1, ㅑ=2, ㅒ=3, ㅓ=4, ㅔ=5, ㅕ=6, ㅖ=7, ㅗ=8,
    #   ㅘ=9, ㅙ=10, ㅚ=11, ㅛ=12, ㅜ=13, ㅝ=14, ㅞ=15, ㅟ=16, ㅠ=17, ㅡ=18, ㅢ=19, ㅣ=20
    # Final (jongseong)   none=0, ㄱ=1, ㄴ=4, ㄷ=7, ㄹ=8, ㅁ=16, ㅂ=17,
    #   ㅅ=19, ㅇ=21, ㅈ=22, ㅊ=23, ㅋ=24, ㅌ=25, ㅍ=26, ㅎ=27

    _CHOSEONG = {
        "":   11, "ng": 11,
        "g": 0,  "k": 15, "kk": 1, "c": 15, "q": 0, "x": 15,
        "n": 2,
        "d": 3,  "t": 16, "tt": 4,  "th": 16,
        "r": 5,  "l": 5,  "ll": 5,
        "m": 6,
        "b": 7,  "p": 17, "pp": 8,  "ph": 17, "f": 17, "v": 7,
        "s": 9,  "ss": 10, "sh": 9, "z": 12,
        "j": 12, "jj": 13,
        "ch": 14,
        "h": 18, "wh": 18,
        "w": 11, "y": 11,
    }
    _JUNGSEONG = {
        "a": 0,   "ae": 1,   "ya": 2,  "yae": 3,
        "eo": 4,  "e": 5,    "yeo": 6, "ye": 7,
        "o": 8,   "wa": 9,   "wae": 10, "oe": 11, "yo": 12,
        "u": 13,  "wo": 14,  "we": 15, "wi": 16, "yu": 17,
        "eu": 18, "ui": 19,  "i": 20,
        # Common roman digraphs
        "aa": 0,  "ee": 20,  "oo": 13, "ou": 8,  "ow": 8,  "au": 8,  "aw": 8,
        "ai": 1,  "ei": 5,   "ay": 1,  "ey": 5,  "oy": 11, "uy": 16,
        "ia": 2,  "ie": 7,   "io": 12, "iu": 17,
        "ua": 9,  "ue": 5,
    }
    _JONGSEONG = {
        "":  0,   "g": 1,  "k": 24, "ng": 21,
        "n": 4,
        "d": 7,  "t": 25,  "th": 25,
        "l": 8,  "r": 8,
        "m": 16,
        "b": 17, "p": 26,  "f": 26, "v": 17, "ph": 26,
        "s": 19, "z": 22,  "ss": 20,
        "j": 22, "ch": 23,
        # 'h' deliberately omitted — Korean transliteration treats word-final
        # 'h' as silent (e.g. "abdullah" → 압둘라, not 압둘랗).
        "x": 24, "c": 24, "q": 24,
    }
    # Initials that should palatalise the following vowel:
    # 'sh', 'ch', 'j' + a/e/o/u → ya/ye/yo/yu (so "sha"=샤, "cho"=쵸, "ju"=쥬)
    _PALATALISERS = {"sh", "ch", "j", "y"}
    _PALATALISE = {0: 2, 1: 3, 4: 6, 5: 7, 8: 12, 13: 17}

    @staticmethod
    def _is_vowel(ch: str) -> bool:
        return ch in "aeiouAEIOU"

    @classmethod
    def _syllabify(cls, word: str):
        """Split an ASCII-ish word into (initial, vowel, final) triples."""
        word = word.lower()
        i = 0
        out = []
        while i < len(word):
            if not word[i].isalpha():
                # Non-letter: emit literally as a "passthrough" marker.
                out.append(("__pass__", word[i], ""))
                i += 1
                continue
            # initial consonants (longest match in CHOSEONG)
            init = ""
            j = i
            while j < len(word) and word[j].isalpha() and not cls._is_vowel(word[j]):
                init += word[j]
                j += 1
            # collapse to longest known cluster (sh, ch, th, ph, kk, ll, ss, ng, wh, jj, pp, tt)
            if len(init) >= 2:
                head = init[:2]
                if head in ("sh", "ch", "th", "ph", "wh", "kk", "ll", "ss",
                            "ng", "jj", "pp", "tt"):
                    leftover = init[2:]
                    init = head
                else:
                    leftover = init[1:]
                    init = init[:1]
            else:
                leftover = ""
            # If we had leftover consonants, they each form a syllable with silent vowel "eu"
            for c in leftover:
                out.append((c, "eu", ""))
            i = j
            # vowel cluster
            vowel = ""
            while i < len(word) and cls._is_vowel(word[i]):
                vowel += word[i]
                i += 1
            if not vowel:
                # Trailing consonant-only: attach as final to previous syllable if possible
                if out and out[-1][0] != "__pass__":
                    pi, pv, pf = out[-1]
                    out[-1] = (pi, pv, pf + init)
                    continue
                vowel = "eu"
            # Decide final: any consonants between current vowel and the next vowel
            j = i
            cons_after = ""
            while j < len(word) and word[j].isalpha() and not cls._is_vowel(word[j]):
                cons_after += word[j]
                j += 1
            final = ""
            if j == len(word):
                # End of word — all trailing consonants become final (just take the longest mapped one)
                if cons_after:
                    if cons_after[:2] == "ng":
                        final = "ng"
                    else:
                        final = cons_after[0]
                    i = j
            else:
                # There's another vowel coming. If 2+ consonants, the FIRST is final.
                if len(cons_after) >= 2:
                    if cons_after[:2] == "ng":
                        final = "ng"
                        i += 2
                    else:
                        final = cons_after[0]
                        i += 1
                # else: single consonant goes entirely to next syllable's initial
            out.append((init, vowel, final))
        return out

    @classmethod
    def _romanize_to_hangul(cls, text) -> str:
        """Phonetic English/Bengali roman → Hangul. Word-by-word."""
        if text is None:
            return ""
        s = str(text)
        if not s.strip():
            return s
        words_out = []
        # Split on whitespace, keep separators
        import re as _re
        tokens = _re.split(r"(\s+)", s)
        for tok in tokens:
            if not tok or tok.isspace():
                words_out.append(tok)
                continue
            # Words may be separated by punctuation too
            sub_tokens = _re.split(r"([^A-Za-z']+)", tok)
            buf = []
            for sub in sub_tokens:
                if not sub or not sub[0].isalpha():
                    buf.append(sub)
                    continue
                # Strip trailing apostrophes
                core = sub.replace("'", "")
                if not core:
                    buf.append(sub)
                    continue
                hangul_chars = []
                for init, vowel, final in cls._syllabify(core):
                    if init == "__pass__":
                        hangul_chars.append(vowel)
                        continue
                    cho = cls._CHOSEONG.get(init, cls._CHOSEONG.get(init[:1], 11))
                    jung = cls._JUNGSEONG.get(vowel, cls._JUNGSEONG.get(vowel[:1], 0))
                    if init in cls._PALATALISERS and jung in cls._PALATALISE:
                        jung = cls._PALATALISE[jung]
                    jong = cls._JONGSEONG.get(final, 0)
                    code = 0xAC00 + cho * 588 + jung * 28 + jong
                    if 0xAC00 <= code <= 0xD7A3:
                        hangul_chars.append(chr(code))
                    else:
                        hangul_chars.append(sub)  # fall back to original
                buf.append("".join(hangul_chars))
            words_out.append("".join(buf))
        return "".join(words_out)

    # ── OpenAI API key loader ────────────────────────────────────────────────

    @staticmethod
    def _load_openai_api_key() -> str:
        """Load OpenAI API key: .env file first, then OPENAI_API_KEY env var."""
        import os as _os
        # 1. Try .env file next to this script
        for base in (
            _os.path.dirname(_os.path.abspath(__file__)),
            _os.getcwd(),
        ):
            env_path = _os.path.join(base, ".env")
            if _os.path.isfile(env_path):
                try:
                    with open(env_path, encoding="utf-8") as _f:
                        for line in _f:
                            line = line.strip()
                            if line.startswith("OPENAI_API_KEY"):
                                _, _, val = line.partition("=")
                                val = val.strip().strip('"\'\' ')
                                if val:
                                    return val
                except OSError:
                    pass
        # 2. Environment variable fallback
        return _os.environ.get("OPENAI_API_KEY", "")

    @staticmethod
    def _openai_translate_batch(texts: list, api_key: str) -> dict:
        """Send a list of name strings to OpenAI and return {original: korean} dict.
        Uses gpt-4o-mini with a tight prompt. Falls back to original on error."""
        if not texts or not api_key:
            return {t: t for t in texts}
        try:
            import openai as _openai
        except ImportError:
            return {t: t for t in texts}

        unique = list(dict.fromkeys(str(t) for t in texts if str(t).strip()))
        if not unique:
            return {t: t for t in texts}

        numbered = "\n".join(f"{i+1}. {name}" for i, name in enumerate(unique))
        system_prompt = (
            "You are an expert Korean transliteration assistant. "
            "The user will give you a numbered list of person names written in English "
            "(they are mostly South Asian / Bangladeshi names). "
            "Return ONLY a numbered list of the same names transliterated into Korean Hangul, "
            "one per line, in the exact same order. "
            "Rules:\n"
            "- Write ONLY the Korean Hangul — no romanisation, no explanation.\n"
            "- Keep the same spacing between name parts as the input.\n"
            "- Expand common abbreviations: MD = 모하메드, MR = 미스터, etc.\n"
            "- If a name part has no standard Korean equivalent, use phonetic approximation.\n"
            "Example input:\n1. MIA MAMUN\n2. SULTANA MARJIA\n3. MOLLAH MD SIFAT\n"
            "Example output:\n1. 미아 마문\n2. 술타나 마르지아\n3. 몰라 모하메드 시파트"
        )
        try:
            client = _openai.OpenAI(api_key=api_key)
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": numbered},
                ],
                temperature=0.1,
                max_tokens=len(unique) * 40 + 100,
            )
            raw = resp.choices[0].message.content or ""
            import re as _re
            result_map = {}
            for line in raw.splitlines():
                line = line.strip()
                m = _re.match(r"^(\d+)[.)\s]\s*(.+)$", line)
                if m:
                    idx = int(m.group(1)) - 1
                    korean = m.group(2).strip()
                    if 0 <= idx < len(unique) and korean:
                        result_map[unique[idx]] = korean
            # Fill missing
            for u in unique:
                if u not in result_map:
                    result_map[u] = u
            return result_map
        except Exception:
            return {u: u for u in unique}

    def _translate_to_korean(self, mode: str = "replace"):
        """Translate selected cells via OpenAI GPT-4o-mini (key from .env).

        mode:
          'replace'  — overwrite the cells with their Korean
          'next_col' — write Korean into the cell immediately to the right
          'next_row' — write Korean into the cell immediately below
        """
        cells = self._selected_cells()
        if not cells:
            self._update_status("Nothing selected to translate.")
            return

        # Collect source texts
        cell_texts = {}
        for r, c in cells:
            try:
                val = self._sheet.get_cell_data(r, c)
            except Exception:
                val = ""
            cell_texts[(r, c)] = str(val) if val is not None else ""

        # Load API key
        api_key = self._load_openai_api_key()
        if not api_key:
            self._update_status("No OpenAI API key found in .env — add OPENAI_API_KEY=sk-... to .env")
            import tkinter.messagebox as _mb
            _mb.showwarning(
                "Korean Translate",
                "No OpenAI API key found.\n\n"
                "Add this line to the .env file next to the app:\n\n"
                "OPENAI_API_KEY=sk-...",
            )
            return

        self._update_status(f"Translating {len(cells)} cell(s) via OpenAI…")
        try:
            self.update_idletasks()
        except Exception:
            pass

        # Batch translate (one API call for all unique values)
        unique_texts = list({v for v in cell_texts.values() if v.strip()})
        translation_map = self._openai_translate_batch(unique_texts, api_key)

        # Build targets
        targets = {}
        new_values = {}
        for (r, c), text in cell_texts.items():
            korean = translation_map.get(text, text) if text.strip() else text
            if mode == "next_col":
                tr, tc = r, c + 1
            elif mode == "next_row":
                tr, tc = r + 1, c
            else:
                tr, tc = r, c
            targets[(tr, tc)] = korean
            new_values[(tr, tc)] = korean

        before = self._capture_cells(targets.keys())
        self._undo_push(before)
        for (tr, tc), v in new_values.items():
            try:
                self._sheet.set_cell_data(tr, tc, v)
            except Exception:
                pass
        try:
            self._sheet.refresh()
        except Exception:
            pass
        self._mark_dirty()
        self._update_status(f"Translated {len(new_values)} cell(s) to Korean via OpenAI.")

    def _select_all(self):
        try:
            self._sheet.select_all()
        except Exception:
            try:
                data = self._sheet.get_sheet_data()
                if data:
                    self._sheet.create_selection_box(
                        0, 0, len(data), max(len(r) for r in data), "cells"
                    )
            except Exception:
                pass

    def _begin_edit_active(self):
        r, c = self._active_cell()
        try:
            self._sheet.open_cell(row=r, column=c)
        except Exception:
            try:
                self._sheet.create_text_editor(r=r, c=c)
            except Exception:
                pass

    def _mark_dirty(self):
        self._dirty = True
        try:
            base = self.status_var.get().split(" · ")[0]
            self._update_status(f"{base} · Modified (unsaved)")
        except Exception:
            pass

    @staticmethod
    def _try_call(*callables):
        """Try each zero-arg callable in order; stop at the first one that
        doesn't raise. Used to paper over tksheet API drift between versions."""
        for fn in callables:
            try:
                fn()
                return True
            except TypeError:
                continue
            except Exception:
                return False
        return False

    # ============================================================
    # Excel-style Home ribbon
    # ============================================================

    # Cell-style presets (label -> (bg, fg, bold))
    _CELL_STYLES = {
        "Normal":      ("#FFFFFF", "#000000", False),
        "Bad":         ("#FFC7CE", "#9C0006", False),
        "Good":        ("#C6EFCE", "#006100", False),
        "Neutral":     ("#FFEB9C", "#9C5700", False),
        "Calculation": ("#F2F2F2", "#FA7D00", True),
        "Check Cell":  ("#A5A5A5", "#FFFFFF", True),
        "Explanatory": ("#FFFFFF", "#7F7F7F", False),
        "Followed Hy": ("#FFFFFF", "#800080", False),
        "Hyperlink":   ("#FFFFFF", "#0563C1", False),
        "Input":       ("#FFCC99", "#3F3F76", False),
        "Heading 1":   ("#FFFFFF", "#1F4E78", True),
        "Heading 2":   ("#FFFFFF", "#2E75B6", True),
        "Heading 3":   ("#FFFFFF", "#5B9BD5", True),
        "Title":       ("#FFFFFF", "#1F4E78", True),
        "Total":       ("#FFFFFF", "#000000", True),
    }

    # Cached so we don't rebuild Tkinter PhotoImages every call
    _ICON_CACHE: dict = {}

    def _build_format_ribbon(self) -> None:
        """Compact single-row Excel-style ribbon. Most operations live inside
        Menubuttons so the bar stays uncluttered."""
        ribbon = ttk.Frame(self, padding=(4, 2, 4, 2))
        ribbon.pack(side="top", fill="x")
        self._ribbon = ribbon

        def sep():
            ttk.Separator(ribbon, orient="vertical").pack(
                side="left", fill="y", padx=4
            )

        def mb(text, items):
            """Create a Menubutton with the given items: list of tuples
            (label, command) or ('—',) for a separator."""
            btn = ttk.Menubutton(ribbon, text=text)
            menu = tkinter.Menu(btn, tearoff=False)
            for item in items:
                if item == ('—',):
                    menu.add_separator()
                else:
                    label, cmd = item
                    menu.add_command(label=label, command=cmd)
            btn["menu"] = menu
            btn.pack(side="left", padx=1)
            return btn

        # ----- Undo / Redo (always visible)
        ttk.Button(ribbon, text="↶ Undo", width=8,
                   command=self._undo).pack(side="left", padx=1)
        ttk.Button(ribbon, text="↷ Redo", width=8,
                   command=self._redo).pack(side="left", padx=1)
        sep()

        # ----- Clipboard
        mb("📋 Clipboard ▾", [
            ("Cut (Ctrl+X)",   self._do_cut),
            ("Copy (Ctrl+C)",  self._do_copy),
            ("Paste (Ctrl+V)", self._do_paste),
            ('—',),
            ("Format Painter", self._format_painter),
        ])
        sep()

        # ----- Font (combobox + size + B/I/U/S inline)
        font_families = sorted(set(tkfont.families()))
        common = ["Calibri", "Arial", "Times New Roman", "Verdana",
                  "Tahoma", "Segoe UI", "Courier New", "Cambria",
                  "Malgun Gothic", "맑은 고딕", "나눔고딕"]
        ordered = [f for f in common if f in font_families] + \
                  [f for f in font_families if f not in common]
        self._font_family_var = tkinter.StringVar(value="Calibri")
        ttk.Combobox(ribbon, textvariable=self._font_family_var,
                     values=ordered, width=14).pack(side="left", padx=1)
        self._font_family_var.trace_add(
            "write", lambda *_: self._apply_font_family()
        )

        self._font_size_var = tkinter.StringVar(value="11")
        ttk.Combobox(ribbon, textvariable=self._font_size_var,
                     values=[8, 9, 10, 11, 12, 14, 16, 18, 20, 24, 28, 36, 48, 72],
                     width=3).pack(side="left", padx=1)
        self._font_size_var.trace_add(
            "write", lambda *_: self._apply_font_size()
        )
        ttk.Button(ribbon, text="B", width=2,
                   command=self._toggle_bold).pack(side="left", padx=1)
        ttk.Button(ribbon, text="I", width=2,
                   command=self._toggle_italic).pack(side="left", padx=1)
        ttk.Button(ribbon, text="U", width=2,
                   command=self._toggle_underline).pack(side="left", padx=1)
        sep()

        # ----- Format dropdown (alignment / colours / borders / styles)
        mb("🎨 Format ▾", [
            ("Bold",                  self._toggle_bold),
            ("Italic",                self._toggle_italic),
            ("Underline",             self._toggle_underline),
            ("Strikethrough",         self._toggle_strikethrough),
            ('—',),
            ("Align left",            lambda: self._set_alignment("w")),
            ("Align center",          lambda: self._set_alignment("center")),
            ("Align right",           lambda: self._set_alignment("e")),
            ("Top",                   lambda: self._set_valignment("n")),
            ("Middle",                lambda: self._set_valignment("center")),
            ("Bottom",                lambda: self._set_valignment("s")),
            ('—',),
            ("Wrap text",             self._toggle_wrap_text),
            ("Merge & center",        self._merge_center),
            ('—',),
            ("Fill colour…",          self._pick_fill_colour),
            ("Font colour…",          self._pick_font_colour),
            ("Borders…",              self._borders_dialog),
            ('—',),
            ("Conditional formatting", self._conditional_format_dialog),
            ("Format as table",       self._format_as_table),
            ("Clear formatting",      self._clear_formatting),
        ])

        # ----- Cell styles (Excel preset palette)
        styles_btn = ttk.Menubutton(ribbon, text="🎨 Styles ▾")
        styles_menu = tkinter.Menu(styles_btn, tearoff=False)
        for name in self._CELL_STYLES:
            styles_menu.add_command(
                label=name, command=lambda n=name: self._apply_cell_style(n)
            )
        styles_btn["menu"] = styles_menu
        styles_btn.pack(side="left", padx=1)
        sep()

        # ----- Number dropdown
        self._num_format_var = tkinter.StringVar(value="General")
        mb("🔢 Number ▾", [
            ("Currency $",     lambda: self._apply_number_format("currency", "$")),
            ("Currency €",     lambda: self._apply_number_format("currency", "€")),
            ("Currency ₩",     lambda: self._apply_number_format("currency", "₩")),
            ("Percent",        lambda: self._apply_number_format("percent")),
            ("Comma",          lambda: self._apply_number_format("comma")),
            ("Scientific",     lambda: self._apply_number_format("scientific")),
            ("Date",           lambda: self._apply_number_format("date")),
            ("Time",           lambda: self._apply_number_format("time")),
            ("Fraction",       lambda: self._apply_number_format("fraction")),
            ('—',),
            ("Increase decimals", lambda: self._step_decimal(+1)),
            ("Decrease decimals", lambda: self._step_decimal(-1)),
            ("Number format…",    self._set_number_format),
        ])
        sep()

        # ----- Cells dropdown (insert/delete/size/visibility)
        mb("📑 Cells ▾", [
            ("Insert row above",     self._insert_row_above),
            ("Insert row below",     self._insert_row_below),
            ("Insert column left",   self._insert_col_left),
            ("Insert column right",  self._insert_col_right),
            ('—',),
            ("Delete row(s)",        self._delete_rows),
            ("Delete column(s)",     self._delete_cols),
            ('—',),
            ("Row height…",          self._set_row_height_dialog),
            ("Column width…",        self._set_col_width_dialog),
            ("AutoFit columns",      self._autofit_columns),
            ('—',),
            ("Hide row(s)",          self._hide_rows),
            ("Hide column(s)",       self._hide_cols),
            ("Unhide all",           self._unhide_all),
            ('—',),
            ("Freeze top row",       lambda: self._freeze("rows", 1)),
            ("Freeze first column",  lambda: self._freeze("cols", 1)),
            ("Freeze panes here",    self._freeze_at_selection),
            ("Unfreeze panes",       self._unfreeze),
        ])

        # ----- Editing dropdown (autosum/fill/clear/sort/find)
        mb("✎ Editing ▾", [
            ("AutoSum below",        self._autosum_below),
            ("AutoSum right",        self._autosum_right),
            ("Average below",        self._autoaverage_below),
            ("Count selection",      self._count_selection),
            ("Insert function…",     self._insert_function),
            ('—',),
            ("Fill down",            self._fill_down),
            ("Fill right",           self._fill_right),
            ("Fill series…",         self._fill_series),
            ('—',),
            ("Clear contents",       self._clear_contents),
            ("Clear formatting",     self._clear_formatting),
            ("Clear all",            self._clear_all),
            ('—',),
            ("Sort A → Z",           lambda: self._sort_by_column(False)),
            ("Sort Z → A",           lambda: self._sort_by_column(True)),
            ("Toggle filter row",    self._toggle_filter_row),
            ('—',),
            ("Find… (Ctrl+F)",       self._focus_find_bar),
            ("Replace… (Ctrl+H)",    lambda: self._open_find_replace(True)),
            ("Go to…",               self._goto_dialog),
            ("Hyperlink…",           self._set_hyperlink),
            ("Insert comment / note", self._insert_comment),
            ("Select all (Ctrl+A)",  self._select_all),
        ])
        sep()

        # ----- Translate to Korean (always visible — that was the request)
        mb("한 Korean ▾", [
            ("Translate selection → Hangul (replace)",
                lambda: self._translate_to_korean("replace")),
            ("Translate selection → Hangul (next column)",
                lambda: self._translate_to_korean("next_col")),
            ("Translate selection → Hangul (next row)",
                lambda: self._translate_to_korean("next_row")),
            ('—',),
            ("Quick test: shafiqul → 샤피굴",
                lambda: messagebox.showinfo(
                    "Hangul preview",
                    f"shafiqul → {self._romanize_to_hangul('shafiqul')}\n"
                    f"abdullah → {self._romanize_to_hangul('abdullah')}\n"
                    f"mohammed → {self._romanize_to_hangul('mohammed')}\n"
                    f"hossain → {self._romanize_to_hangul('hossain')}\n"
                    f"jakir → {self._romanize_to_hangul('jakir')}",
                    parent=self.winfo_toplevel(),
                )),
        ])

    # ============================================================
    # Find-in-page bar
    # ============================================================

    def _build_find_bar(self) -> None:
        bar = ttk.Frame(self, padding=(4, 2, 4, 2))
        bar.pack(side="top", fill="x")
        self._find_bar = bar
        ttk.Label(bar, text="Find in page:").pack(side="left", padx=(0, 4))
        self._find_var = tkinter.StringVar()
        self._find_entry = ttk.Entry(bar, textvariable=self._find_var, width=30)
        self._find_entry.pack(side="left", padx=(0, 6))
        self._find_var.trace_add("write", lambda *_: self._update_find_highlights())
        ttk.Button(bar, text="Next ↓", width=8,
                   command=lambda: self._jump_to_match(+1)).pack(side="left", padx=1)
        ttk.Button(bar, text="Prev ↑", width=8,
                   command=lambda: self._jump_to_match(-1)).pack(side="left", padx=1)
        self._find_match_var = tkinter.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="Match case",
                        variable=self._find_match_var,
                        command=self._update_find_highlights).pack(side="left", padx=6)
        self._find_count_var = tkinter.StringVar(value="")
        ttk.Label(bar, textvariable=self._find_count_var,
                  foreground="#666").pack(side="left", padx=8)
        ttk.Button(bar, text="✕ Clear", width=8,
                   command=self._clear_find).pack(side="right", padx=1)
        # Esc clears, Enter advances
        self._find_entry.bind("<Return>", lambda e: self._jump_to_match(+1))
        self._find_entry.bind("<Shift-Return>", lambda e: self._jump_to_match(-1))
        self._find_entry.bind("<Escape>", lambda e: self._clear_find())
        # Track current match index
        self._find_matches: list = []
        self._find_idx: int = -1

    def _focus_find_bar(self):
        try:
            self._find_entry.focus_set()
            self._find_entry.select_range(0, "end")
        except Exception:
            pass

    def _update_find_highlights(self):
        if not _TKSHEET_OK or self._sheet is None:
            return
        # First: clear any previous yellow highlights we set
        prev = getattr(self, "_find_prev_cells", [])
        for r, c in prev:
            try:
                self._sheet.dehighlight_cells(row=r, column=c)
            except Exception:
                pass
        self._find_prev_cells = []
        self._find_matches = []
        self._find_idx = -1
        needle = self._find_var.get()
        if not needle:
            self._find_count_var.set("")
            try:
                self._sheet.refresh()
            except Exception:
                pass
            return
        try:
            data = self._sheet.get_sheet_data()
        except Exception:
            return
        case = self._find_match_var.get()
        n = needle if case else needle.lower()
        for r, row in enumerate(data):
            for c, v in enumerate(row):
                s = "" if v is None else str(v)
                hay = s if case else s.lower()
                if n in hay:
                    self._find_matches.append((r, c))
                    try:
                        self._sheet.highlight_cells(row=r, column=c, bg="#FFEB3B",
                                                    fg="#000000")
                    except Exception:
                        pass
        self._find_prev_cells = list(self._find_matches)
        self._find_count_var.set(
            f"{len(self._find_matches)} match{'es' if len(self._find_matches) != 1 else ''}"
        )
        try:
            self._sheet.refresh()
        except Exception:
            pass
        if self._find_matches:
            self._find_idx = 0
            self._goto_match(0)

    def _goto_match(self, idx: int):
        if not self._find_matches:
            return
        idx %= len(self._find_matches)
        self._find_idx = idx
        r, c = self._find_matches[idx]
        try:
            self._sheet.see(row=r, column=c)
            self._sheet.select_cell(row=r, column=c)
        except Exception:
            pass

    def _jump_to_match(self, delta: int):
        if not self._find_matches:
            return
        self._goto_match(self._find_idx + delta)

    def _clear_find(self):
        self._find_var.set("")
        self._update_find_highlights()
        try:
            self._sheet.focus_set()
        except Exception:
            pass

    # ============================================================
    # Ribbon-button handlers
    # ============================================================

    # ----- clipboard -----

    def _do_cut(self):
        for fn in ("ctrl_x", "cut", "_cut"):
            if hasattr(self._sheet, fn):
                try:
                    getattr(self._sheet, fn)()
                    return
                except Exception:
                    continue

    def _do_copy(self):
        for fn in ("ctrl_c", "copy", "_copy"):
            if hasattr(self._sheet, fn):
                try:
                    getattr(self._sheet, fn)()
                    return
                except Exception:
                    continue

    def _do_paste(self):
        for fn in ("ctrl_v", "paste", "_paste"):
            if hasattr(self._sheet, fn):
                try:
                    getattr(self._sheet, fn)()
                    self._mark_dirty()
                    return
                except Exception:
                    continue

    def _format_painter(self):
        """Copy the active cell's bg/fg to whatever you click next."""
        sh = self._sheet
        r, c = self._active_cell()
        try:
            # tksheet 7.x exposes get_cell_kwargs / cell options
            opts = sh.get_cell_options(r=r, c=c) if hasattr(sh, "get_cell_options") else {}
        except Exception:
            opts = {}
        bg = opts.get("highlight", {}).get("bg") if isinstance(opts.get("highlight"), dict) else None
        fg = opts.get("highlight", {}).get("fg") if isinstance(opts.get("highlight"), dict) else None
        # Fallback: read from our own cached styles
        if not bg and not fg:
            cache = getattr(sh, "_cw_cell_styles", {})
            bg = cache.get((r, c, "bg"))
            fg = cache.get((r, c, "fg"))
        self._update_status("Format Painter armed — click a cell to apply.")

        def _on_click(_event=None):
            tr, tc = self._active_cell()
            try:
                if bg or fg:
                    self._sheet.highlight_cells(row=tr, column=tc,
                                                bg=bg or "#FFFFFF",
                                                fg=fg or "#000000")
            except Exception:
                pass
            self._update_status("Format Painter: applied.")
            self._mark_dirty()
            try:
                self._sheet.unbind("<<SheetSelect>>")
            except Exception:
                pass

        try:
            self._sheet.bind("<<SheetSelect>>", _on_click)
        except Exception:
            # If the event isn't supported, fall back to a single Button-1
            try:
                self._sheet.bind("<Button-1>", lambda e: (
                    self.after(50, _on_click), "break")[1], add="+")
            except Exception:
                pass

    # ----- font -----

    def _apply_font_family(self):
        family = self._font_family_var.get().strip() or "Calibri"
        try:
            size = int(self._font_size_var.get())
        except (ValueError, TypeError):
            size = 11
        for r, c in self._selected_cells():
            try:
                self._sheet.span(r, c).font((family, size, ""))
            except Exception:
                try:
                    self._sheet.set_cell_options(r, c, font=(family, size, ""))
                except Exception:
                    pass
        try:
            self._sheet.refresh()
        except Exception:
            pass
        self._mark_dirty()

    def _apply_font_size(self):
        try:
            size = int(self._font_size_var.get())
        except (ValueError, TypeError):
            return
        family = self._font_family_var.get().strip() or "Calibri"
        for r, c in self._selected_cells():
            try:
                self._sheet.span(r, c).font((family, size, ""))
            except Exception:
                try:
                    self._sheet.set_cell_options(r, c, font=(family, size, ""))
                except Exception:
                    pass
        try:
            self._sheet.refresh()
        except Exception:
            pass
        self._mark_dirty()

    def _bump_font_size(self, delta: int):
        try:
            cur = int(self._font_size_var.get())
        except (ValueError, TypeError):
            cur = 11
        new = max(6, min(96, cur + delta))
        self._font_size_var.set(str(new))
        self._apply_font_size()

    def _toggle_underline(self):
        # tksheet 7.x doesn't carry an underline flag; emulate with Tk font.
        self._toggle_font_decoration("underline")

    def _toggle_strikethrough(self):
        self._toggle_font_decoration("overstrike")

    def _toggle_font_decoration(self, which: str):
        sh = self._sheet
        cache = getattr(sh, "_cw_cell_decor", None)
        if cache is None:
            cache = {}
            sh._cw_cell_decor = cache
        family = self._font_family_var.get().strip() or "Calibri"
        try:
            size = int(self._font_size_var.get())
        except (ValueError, TypeError):
            size = 11
        for r, c in self._selected_cells():
            cur = cache.get((r, c), set())
            if which in cur:
                cur.discard(which)
            else:
                cur.add(which)
            cache[(r, c)] = cur
            mods = " ".join(sorted(cur)) if cur else ""
            try:
                sh.span(r, c).font((family, size, mods))
            except Exception:
                pass
        try:
            sh.refresh()
        except Exception:
            pass
        self._mark_dirty()

    # ----- alignment -----

    def _set_valignment(self, where: str):
        for r, c in self._selected_cells():
            try:
                self._sheet.span(r, c).align(where)
            except Exception:
                try:
                    self._sheet.align_cells(row=r, column=c, align=where)
                except Exception:
                    pass
        try:
            self._sheet.refresh()
        except Exception:
            pass
        self._mark_dirty()

    def _toggle_wrap_text(self):
        cells = self._selected_cells()
        cache = getattr(self._sheet, "_cw_wrap_cells", None)
        if cache is None:
            cache = set()
            self._sheet._cw_wrap_cells = cache
        for r, c in cells:
            key = (r, c)
            wrap = key not in cache
            try:
                self._sheet.span(r, c).options(wrap="w" if wrap else "")
            except Exception:
                pass
            if wrap:
                cache.add(key)
            else:
                cache.discard(key)
        try:
            self._sheet.refresh()
        except Exception:
            pass

    def _merge_center(self):
        try:
            box = self._sheet.get_all_selection_boxes()
        except Exception:
            box = None
        if not box:
            return
        r1, c1, r2, c2 = box[0]
        if r2 - r1 <= 0 or c2 - c1 <= 0:
            return
        try:
            self._sheet.merge_cells(r1, c1, r2, c2)
        except TypeError:
            try:
                self._sheet.merge_cells((r1, c1, r2, c2))
            except Exception:
                pass
        except Exception:
            pass
        # Center the top-left of the merge
        try:
            self._sheet.span(r1, c1).align("center")
        except Exception:
            pass
        self._mark_dirty()

    # ----- numbers -----

    _DECIMAL_KEY = "_cw_decimal_count"

    def _apply_number_preset(self):
        preset = (self._num_format_var.get() or "").lower()
        if preset.startswith("currency"):
            sym = "$"
            if "€" in self._num_format_var.get():
                sym = "€"
            elif "₩" in self._num_format_var.get():
                sym = "₩"
            self._apply_number_format("currency", sym)
        elif preset == "percentage":
            self._apply_number_format("percent")
        elif preset == "number" or preset == "accounting":
            self._apply_number_format("comma")
        elif preset == "scientific":
            self._apply_number_format("scientific")
        elif preset == "date":
            self._apply_number_format("date")
        elif preset == "time":
            self._apply_number_format("time")
        elif preset == "fraction":
            self._apply_number_format("fraction")
        elif preset == "text":
            pass  # leave as-is (text format = stored as-is)
        elif preset == "general":
            pass

    def _apply_number_format(self, kind: str, sym: str = "$"):
        cells = self._selected_cells()
        if not cells:
            return
        decimal_cache = getattr(self, "_decimal_cache", {})
        self._decimal_cache = decimal_cache
        for r, c in cells:
            try:
                raw = self._sheet.get_cell_data(r, c)
            except Exception:
                continue
            try:
                num = float(str(raw).replace(",", "").replace("$", "")
                            .replace("€", "").replace("₩", "").rstrip("%"))
            except (TypeError, ValueError):
                continue
            dp = decimal_cache.get((r, c), 2)
            if kind == "currency":
                rendered = f"{sym}{num:,.{dp}f}"
            elif kind == "percent":
                # If the value is between 0 and 1 treat as ratio
                shown = num * 100 if abs(num) <= 1 else num
                rendered = f"{shown:,.{dp}f}%"
            elif kind == "comma":
                rendered = f"{num:,.{dp}f}"
            elif kind == "scientific":
                rendered = f"{num:.{dp}e}"
            elif kind == "fraction":
                from fractions import Fraction
                rendered = str(Fraction(num).limit_denominator(100))
            elif kind == "date":
                # Treat as Excel-serial (1900 epoch) if integer-ish, else leave
                try:
                    import datetime as _dt
                    if 0 < num < 100000:
                        d = _dt.date(1899, 12, 30) + _dt.timedelta(days=int(num))
                        rendered = d.strftime("%Y-%m-%d")
                    else:
                        continue
                except Exception:
                    continue
            elif kind == "time":
                try:
                    h = int(num) % 24
                    m = int((num - int(num)) * 60)
                    rendered = f"{h:02d}:{m:02d}"
                except Exception:
                    continue
            else:
                continue
            try:
                self._sheet.set_cell_data(r, c, rendered)
            except Exception:
                pass
        self._mark_dirty()

    def _step_decimal(self, delta: int):
        decimal_cache = getattr(self, "_decimal_cache", {})
        self._decimal_cache = decimal_cache
        for r, c in self._selected_cells():
            cur = decimal_cache.get((r, c), 2)
            decimal_cache[(r, c)] = max(0, min(10, cur + delta))
        # Re-apply current preset / fall back to comma
        preset = (self._num_format_var.get() or "general").lower()
        if "currency" in preset:
            self._apply_number_format("currency",
                                      "€" if "€" in preset else
                                      "₩" if "₩" in preset else "$")
        elif "percent" in preset:
            self._apply_number_format("percent")
        elif preset in ("number", "accounting", "general"):
            self._apply_number_format("comma")
        elif preset == "scientific":
            self._apply_number_format("scientific")

    # ----- cell styles / conditional / format-as-table -----

    def _apply_cell_style(self, name: str):
        spec = self._CELL_STYLES.get(name)
        if not spec:
            return
        bg, fg, bold = spec
        for r, c in self._selected_cells():
            try:
                self._sheet.highlight_cells(row=r, column=c, bg=bg, fg=fg)
            except Exception:
                pass
            if bold:
                cache = getattr(self._sheet, "_cw_cell_styles", None)
                if cache is None:
                    cache = {}
                    self._sheet._cw_cell_styles = cache
                cur = cache.get((r, c), set())
                cur.add("bold")
                cache[(r, c)] = cur
                try:
                    self._sheet.span(r, c).font(("Calibri", 11, "bold"))
                except Exception:
                    pass
        try:
            self._sheet.refresh()
        except Exception:
            pass
        self._mark_dirty()

    def _conditional_format_dialog(self):
        """A simple conditional format: highlight cells whose numeric value
        meets a chosen comparator + threshold."""
        top = tkinter.Toplevel(self.winfo_toplevel())
        top.title("Conditional Formatting")
        top.transient(self.winfo_toplevel())
        ttk.Label(top, text="When the cell value is").grid(row=0, column=0,
                                                            padx=6, pady=6, sticky="e")
        op_var = tkinter.StringVar(value=">")
        ttk.Combobox(top, textvariable=op_var,
                     values=(">", ">=", "<", "<=", "=", "!="),
                     width=4, state="readonly").grid(row=0, column=1, padx=2)
        thresh_var = tkinter.StringVar(value="0")
        ttk.Entry(top, textvariable=thresh_var,
                  width=10).grid(row=0, column=2, padx=2)
        ttk.Label(top, text="Highlight").grid(row=1, column=0,
                                              padx=6, pady=6, sticky="e")
        clr_var = tkinter.StringVar(value="#FFC7CE")
        ttk.Entry(top, textvariable=clr_var, width=10).grid(row=1, column=1, padx=2)

        def pick():
            c = colorchooser.askcolor(parent=top, color=clr_var.get())
            if c and c[1]:
                clr_var.set(c[1])

        ttk.Button(top, text="…", width=3, command=pick).grid(row=1, column=2, padx=2)

        def apply_rule():
            try:
                t = float(thresh_var.get())
            except ValueError:
                messagebox.showerror("Conditional", "Threshold must be a number.",
                                     parent=top)
                return
            op = op_var.get()
            colour = clr_var.get()
            cmp = {
                ">":  lambda x: x > t,  ">=": lambda x: x >= t,
                "<":  lambda x: x < t,  "<=": lambda x: x <= t,
                "=":  lambda x: x == t, "!=": lambda x: x != t,
            }[op]
            for r, c in self._selected_cells():
                try:
                    raw = self._sheet.get_cell_data(r, c)
                    num = float(str(raw).replace(",", "").replace("$", "")
                                .rstrip("%"))
                except (TypeError, ValueError):
                    continue
                if cmp(num):
                    try:
                        self._sheet.highlight_cells(row=r, column=c, bg=colour)
                    except Exception:
                        pass
            self._mark_dirty()
            top.destroy()

        ttk.Button(top, text="Apply", command=apply_rule).grid(row=2, column=0,
                                                               columnspan=3, pady=8)

    def _format_as_table(self):
        """Apply zebra-stripe + bold header to the selected box."""
        try:
            box = self._sheet.get_all_selection_boxes()
        except Exception:
            box = None
        if not box:
            return
        r1, c1, r2, c2 = box[0]
        # Header row
        for c in range(c1, c2):
            try:
                self._sheet.highlight_cells(row=r1, column=c,
                                            bg="#4472C4", fg="#FFFFFF")
            except Exception:
                pass
            try:
                self._sheet.span(r1, c).font(("Calibri", 11, "bold"))
            except Exception:
                pass
        # Body zebra
        for i, r in enumerate(range(r1 + 1, r2)):
            zebra = "#FFFFFF" if i % 2 == 0 else "#D9E1F2"
            for c in range(c1, c2):
                try:
                    self._sheet.highlight_cells(row=r, column=c, bg=zebra)
                except Exception:
                    pass
        try:
            self._sheet.refresh()
        except Exception:
            pass
        self._mark_dirty()

    # ----- borders -----

    def _borders_dialog(self):
        top = tkinter.Toplevel(self.winfo_toplevel())
        top.title("Borders")
        top.transient(self.winfo_toplevel())
        ttk.Label(top, text="Apply border:").grid(row=0, column=0, columnspan=4,
                                                   padx=6, pady=(8, 4))
        which = tkinter.StringVar(value="all")
        choices = (("All", "all"), ("Outside", "outside"),
                   ("Top", "top"), ("Bottom", "bottom"),
                   ("Left", "left"), ("Right", "right"),
                   ("None", "none"))
        for i, (lbl, val) in enumerate(choices):
            ttk.Radiobutton(top, text=lbl, variable=which, value=val).grid(
                row=1 + i // 4, column=i % 4, padx=4, pady=2, sticky="w"
            )

        def apply_border():
            kind = which.get()
            try:
                box = self._sheet.get_all_selection_boxes()
            except Exception:
                box = None
            if not box:
                return
            r1, c1, r2, c2 = box[0]
            # Visual border via 1-pixel highlight on the relevant edge cells.
            # tksheet 7.x has gridline support but inconsistent across versions,
            # so we colour the outermost cells with a subtle dark fg.
            def edge(r, c, where):
                try:
                    self._sheet.highlight_cells(row=r, column=c, fg="#000000")
                except Exception:
                    pass
            if kind in ("all", "outside", "top"):
                for c in range(c1, c2):
                    edge(r1, c, "top")
            if kind in ("all", "outside", "bottom"):
                for c in range(c1, c2):
                    edge(r2 - 1, c, "bottom")
            if kind in ("all", "outside", "left"):
                for r in range(r1, r2):
                    edge(r, c1, "left")
            if kind in ("all", "outside", "right"):
                for r in range(r1, r2):
                    edge(r, c2 - 1, "right")
            if kind == "none":
                for r in range(r1, r2):
                    for c in range(c1, c2):
                        try:
                            self._sheet.dehighlight_cells(row=r, column=c)
                        except Exception:
                            pass
            try:
                self._sheet.refresh()
            except Exception:
                pass
            self._mark_dirty()
            top.destroy()

        ttk.Button(top, text="Apply", command=apply_border).grid(
            row=4, column=0, columnspan=4, pady=8
        )

    # ----- fill / clear / goto -----

    def _fill_down(self):
        try:
            box = self._sheet.get_all_selection_boxes()
        except Exception:
            box = None
        if not box:
            return
        r1, c1, r2, c2 = box[0]
        if r2 - r1 < 2:
            return
        target_cells = [(r, c) for c in range(c1, c2) for r in range(r1 + 1, r2)]
        self._undo_push(self._capture_cells(target_cells))
        for c in range(c1, c2):
            try:
                src = self._sheet.get_cell_data(r1, c)
            except Exception:
                src = ""
            for r in range(r1 + 1, r2):
                try:
                    self._sheet.set_cell_data(r, c, src)
                except Exception:
                    pass
        self._mark_dirty()

    def _fill_right(self):
        try:
            box = self._sheet.get_all_selection_boxes()
        except Exception:
            box = None
        if not box:
            return
        r1, c1, r2, c2 = box[0]
        if c2 - c1 < 2:
            return
        target_cells = [(r, c) for r in range(r1, r2) for c in range(c1 + 1, c2)]
        self._undo_push(self._capture_cells(target_cells))
        for r in range(r1, r2):
            try:
                src = self._sheet.get_cell_data(r, c1)
            except Exception:
                src = ""
            for c in range(c1 + 1, c2):
                try:
                    self._sheet.set_cell_data(r, c, src)
                except Exception:
                    pass
        self._mark_dirty()

    def _fill_series(self):
        step = simpledialog.askfloat(
            "Fill series", "Step value (e.g. 1 for 1,2,3…):",
            initialvalue=1.0, parent=self.winfo_toplevel(),
        )
        if step is None:
            return
        try:
            box = self._sheet.get_all_selection_boxes()
        except Exception:
            box = None
        if not box:
            return
        r1, c1, r2, c2 = box[0]
        for c in range(c1, c2):
            try:
                start_raw = self._sheet.get_cell_data(r1, c)
                start = float(str(start_raw).replace(",", ""))
            except (TypeError, ValueError):
                start = 0.0
            for i, r in enumerate(range(r1, r2)):
                try:
                    self._sheet.set_cell_data(r, c, start + i * step)
                except Exception:
                    pass
        self._mark_dirty()

    def _clear_contents(self):
        cells = self._selected_cells()
        self._undo_push(self._capture_cells(cells))
        for r, c in cells:
            try:
                self._sheet.set_cell_data(r, c, "")
            except Exception:
                pass
        self._mark_dirty()

    def _clear_all(self):
        for r, c in self._selected_cells():
            try:
                self._sheet.set_cell_data(r, c, "")
            except Exception:
                pass
            try:
                self._sheet.dehighlight_cells(row=r, column=c)
            except Exception:
                pass
        try:
            self._sheet.refresh()
        except Exception:
            pass
        self._mark_dirty()

    def _goto_dialog(self):
        ref = simpledialog.askstring(
            "Go to", "Cell reference (e.g. C12):",
            parent=self.winfo_toplevel(),
        )
        if not ref:
            return
        ref = ref.strip().upper()
        # Split letters / digits
        letters, digits = "", ""
        for ch in ref:
            if ch.isalpha():
                letters += ch
            elif ch.isdigit():
                digits += ch
        if not letters or not digits:
            return
        c = self._col_letters_to_index(letters)
        try:
            r = int(digits) - 1
        except ValueError:
            return
        if r < 0 or c < 0:
            return
        try:
            self._sheet.see(row=r, column=c)
            self._sheet.select_cell(row=r, column=c)
        except Exception:
            pass
