from __future__ import annotations

import csv
import html
import io
import os
import threading
from pathlib import Path

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .core import (
    APP_DIR,
    MAX_LOG_BLOCKS,
    atomic_write_text,
)
from .themes import application_icon


class UiShellMixin:
    def _btn(self, text: str, tooltip: str = "", obj: str = "mini") -> QPushButton:
        b = QPushButton(text)
        b.setObjectName(obj)
        b.setMinimumWidth(68)
        b.setMinimumHeight(26)
        b.setToolTip(tooltip)
        b.setStatusTip(tooltip)
        b.setAccessibleDescription(tooltip)
        return b

    def _install_shortcuts(self) -> None:
        """Install discoverable shortcuts for the primary workflow."""
        bindings = [
            ("Ctrl+O", self.import_file),
            ("Ctrl+S", self.save_session),
            ("Ctrl+Return", self.start_download),
            ("Ctrl+Shift+P", self.command_preview),
            ("Ctrl+L", lambda: self.txt_commands.setFocus()),
            ("F5", self.health_check),
        ]
        self._shortcuts: list[QShortcut] = []
        for sequence, callback in bindings:
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(callback)  # type: ignore[arg-type]
            self._shortcuts.append(shortcut)

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(22, 16, 22, 18)
        root.setSpacing(10)

        header = QHBoxLayout()
        self.lbl_brand_mark = QLabel()
        self.lbl_brand_mark.setObjectName("brandMark")
        self.lbl_brand_mark.setPixmap(application_icon().pixmap(38, 38))
        self.lbl_brand_mark.setFixedSize(42, 42)
        self.lbl_brand_mark.setAlignment(Qt.AlignCenter)
        self.lbl_brand_mark.setAccessibleName("GdlScrape logo")
        self.lbl_title = QLabel("GDL")
        self.lbl_title.setObjectName("title")
        self.lbl_subtitle = QLabel("SCRAPE")
        self.lbl_subtitle.setObjectName("subtle")
        self.lbl_subtitle.setStyleSheet("font-size: 16px;")
        self.lbl_ready = QLabel("Ready")
        self.lbl_ready.setObjectName("good")
        header.addWidget(self.lbl_brand_mark)
        header.addWidget(self.lbl_title)
        header.addWidget(self.lbl_subtitle)
        header.addStretch(1)
        self.lbl_language = QLabel("Lang")
        self.lbl_language.setObjectName("subtle")
        self.combo_help_language = QComboBox()
        self.combo_help_language.addItems(["English", "Indonesia"])
        self.combo_help_language.setCurrentText(getattr(self, "help_language", "English"))
        self.combo_help_language.setToolTip("UI language for the main window, dialogs, Help Center, and exported quick guide.")
        self.combo_help_language.setFixedWidth(112)
        self.combo_help_language.setFixedHeight(26)
        self.combo_help_language.currentTextChanged.connect(self.set_help_language)
        header.addWidget(self.lbl_language)
        header.addWidget(self.combo_help_language)
        header.addWidget(self.lbl_ready)
        root.addLayout(header)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setObjectName("separatorLine")
        root.addWidget(line)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        root.addWidget(splitter, 1)

        left = QWidget()
        left_l = QVBoxLayout(left)
        left_l.setContentsMargins(0, 0, 10, 0)
        left_l.setSpacing(10)
        splitter.addWidget(left)

        # URL / commands card
        input_box = QGroupBox("URL / Commands")
        input_l = QVBoxLayout(input_box)
        top_input = QHBoxLayout()
        self.lbl_entry_count = QLabel("0 entry")
        self.lbl_entry_count.setObjectName("subtle")
        top_input.addStretch(1)
        top_input.addWidget(self.lbl_entry_count)
        input_l.addLayout(top_input)

        self.txt_commands = QPlainTextEdit()
        self.txt_commands.setPlaceholderText(
            'Paste URL or full gallery-dl command here. One entry per line.\nExample: gallery-dl -d "F:\\Rips\\Download\\Creator" https://example.com/post/123 --no-check-certificate'
        )
        self.txt_commands.setMinimumHeight(235)
        self.txt_commands.setMaximumBlockCount(20000)
        self.txt_commands.textChanged.connect(self.on_text_changed)
        input_l.addWidget(self.txt_commands, 1)

        input_buttons = QHBoxLayout()
        self.btn_import = self._btn("Import", "Load .txt/.csv/.xlsx database")
        self.btn_paste = self._btn("Paste", "Paste commands from clipboard")
        self.btn_clear_input = self._btn("Clear", "Clear command input")
        self.btn_import.clicked.connect(self.import_file)
        self.btn_paste.clicked.connect(self.paste_clipboard)
        self.btn_clear_input.clicked.connect(self.clear_input)
        input_buttons.addWidget(self.btn_import)
        input_buttons.addWidget(self.btn_paste)
        input_buttons.addWidget(self.btn_clear_input)
        input_buttons.addStretch(1)
        self.lbl_loaded_file = QLabel("No file loaded")
        self.lbl_loaded_file.setObjectName("subtle")
        input_buttons.addWidget(self.lbl_loaded_file)
        input_l.addLayout(input_buttons)
        left_l.addWidget(input_box, 2)

        # Tabs under input: log, queue, workers, history
        self.tabs = QTabWidget()
        left_l.addWidget(self.tabs, 2)

        log_page = QWidget()
        log_l = QVBoxLayout(log_page)
        log_l.setContentsMargins(10, 10, 10, 10)
        log_top = QHBoxLayout()
        log_top.addWidget(QLabel("Log filter:"))
        self.combo_log_filter = QComboBox()
        self.combo_log_filter.addItems(["All", "Error", "Warning", "Done", "Current worker lines"])
        self.combo_log_filter.currentTextChanged.connect(self.refresh_log_view)
        log_top.addWidget(self.combo_log_filter)
        log_top.addStretch(1)
        self.lbl_log_counts = QLabel("Done 0  Failed 0")
        self.lbl_log_counts.setObjectName("metric")
        log_top.addWidget(self.lbl_log_counts)
        log_l.addLayout(log_top)
        self.log_all = QPlainTextEdit()
        self.log_all.setReadOnly(True)
        self.log_all.setMaximumBlockCount(MAX_LOG_BLOCKS)
        self.log_all.setPlaceholderText("Combined logs will appear here.")
        log_l.addWidget(self.log_all, 1)
        self.tabs.addTab(log_page, "Log")

        queue_page = QWidget()
        queue_l = QVBoxLayout(queue_page)
        queue_l.setContentsMargins(10, 10, 10, 10)
        filter_l = QHBoxLayout()
        filter_l.addWidget(QLabel("Filter:"))
        self.combo_filter = QComboBox()
        self.combo_filter.addItems(["All", "Queued", "Running", "Done", "Failed", "Stopped", "Cancelled"])
        self.combo_filter.currentTextChanged.connect(self.apply_queue_filter)
        filter_l.addWidget(self.combo_filter)
        self.search_queue = QLineEdit()
        self.search_queue.setPlaceholderText("Search URL, service, ID, destination, tag...")
        self.search_queue.textChanged.connect(self.apply_queue_filter)
        filter_l.addWidget(self.search_queue, 1)
        queue_l.addLayout(filter_l)
        self.tbl_queue = QTableWidget(0, 7)
        self.tbl_queue.setHorizontalHeaderLabels(["#", "Service", "ID", "Destination", "Tag", "Status", "Stats"])
        self.tbl_queue.verticalHeader().setVisible(False)
        self.tbl_queue.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl_queue.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_queue.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tbl_queue.customContextMenuRequested.connect(self.show_queue_context_menu)
        h = self.tbl_queue.horizontalHeader()
        h.setSectionResizeMode(self.COL_DEST, QHeaderView.Stretch)
        self.tbl_queue.setColumnWidth(self.COL_NUM, 42)
        self.tbl_queue.setColumnWidth(self.COL_SERVICE, 88)
        self.tbl_queue.setColumnWidth(self.COL_ID, 105)
        self.tbl_queue.setColumnWidth(self.COL_TAG, 90)
        self.tbl_queue.setColumnWidth(self.COL_STATUS, 90)
        self.tbl_queue.setColumnWidth(self.COL_STATS, 100)
        queue_l.addWidget(self.tbl_queue, 1)
        self.tabs.addTab(queue_page, "Queue")

        worker_page = QWidget()
        worker_l = QVBoxLayout(worker_page)
        worker_l.setContentsMargins(10, 10, 10, 10)
        self.tbl_workers = QTableWidget(0, 3)
        self.tbl_workers.setHorizontalHeaderLabels(["Worker", "Status", "Current Link"])
        self.tbl_workers.verticalHeader().setVisible(False)
        self.tbl_workers.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_workers.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.tbl_workers.setColumnWidth(0, 80)
        self.tbl_workers.setColumnWidth(1, 110)
        worker_l.addWidget(self.tbl_workers)
        self.tabs.addTab(worker_page, "Workers")

        history_page = QWidget()
        history_l = QVBoxLayout(history_page)
        history_l.setContentsMargins(10, 10, 10, 10)
        hist_btns = QHBoxLayout()
        self.btn_load_history = self._btn("Refresh", "Refresh job history")
        self.btn_clear_history = self._btn("Clear", "Clear saved job history")
        self.btn_load_history.clicked.connect(self.load_history_table)
        self.btn_clear_history.clicked.connect(self.clear_history)
        hist_btns.addWidget(self.btn_load_history)
        hist_btns.addWidget(self.btn_clear_history)
        hist_btns.addStretch(1)
        history_l.addLayout(hist_btns)
        self.tbl_history = QTableWidget(0, 5)
        self.tbl_history.setHorizontalHeaderLabels(["Time", "Status", "Service", "ID", "URL"])
        self.tbl_history.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.tbl_history.verticalHeader().setVisible(False)
        self.tbl_history.setEditTriggers(QAbstractItemView.NoEditTriggers)
        history_l.addWidget(self.tbl_history)
        self.tabs.addTab(history_page, "History")

        # Right side panel - balanced dashboard
        # The previous compact sidebar could squeeze widgets and make button text disappear
        # on narrow screens or high-DPI scaling. This version uses a tabbed sidebar and
        # scrollable Run page, so controls stay readable instead of overlapping.
        side = QWidget()
        self.side_panel = side
        side.setMinimumWidth(350)
        side.setMaximumWidth(490)
        side_l = QVBoxLayout(side)
        side_l.setContentsMargins(12, 0, 0, 0)
        side_l.setSpacing(8)
        splitter.addWidget(side)
        splitter.setSizes([860, 360])

        self.side_tabs = QTabWidget()
        side_l.addWidget(self.side_tabs, 1)

        def make_card(title: str) -> tuple[QFrame, QVBoxLayout]:
            card = QFrame()
            card.setObjectName("card")
            lay = QVBoxLayout(card)
            lay.setContentsMargins(10, 8, 10, 10)
            lay.setSpacing(6)
            title_label = QLabel(title)
            title_label.setObjectName("sectionTitle")
            lay.addWidget(title_label)
            return card, lay

        # ----- Run tab --------------------------------------------------- #
        run_scroll = QScrollArea()
        run_scroll.setWidgetResizable(True)
        run_scroll.setFrameShape(QFrame.NoFrame)
        run_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        run_page = QWidget()
        run_l = QVBoxLayout(run_page)
        run_l.setContentsMargins(6, 6, 6, 6)
        run_l.setSpacing(9)
        run_scroll.setWidget(run_page)

        config_card, config_l = make_card("Run Config")
        config_l.setSpacing(6)

        config_grid = QGridLayout()
        config_grid.setContentsMargins(0, 0, 0, 0)
        config_grid.setHorizontalSpacing(8)
        config_grid.setVerticalSpacing(5)

        def add_grid_label(text: str, row: int, col: int) -> QLabel:
            label = QLabel(text)
            label.setObjectName("fieldLabel")
            label.setFixedHeight(16)
            config_grid.addWidget(label, row, col)
            return label

        self.spin_workers = QSpinBox()
        self.spin_workers.setButtonSymbols(QAbstractSpinBox.UpDownArrows)
        self.spin_workers.setRange(1, 32)
        self.spin_workers.setValue(3)
        self.spin_workers.setFixedHeight(28)
        self.spin_workers.setMinimumWidth(82)
        self.spin_workers.setToolTip("Number of parallel download workers.")
        add_grid_label("Workers", 0, 0)
        config_grid.addWidget(self.spin_workers, 1, 0)

        self.combo_cookies = QComboBox()
        self.combo_cookies.addItems(["none", "chrome", "firefox", "edge", "brave", "chromium", "opera"])
        self.combo_cookies.setFixedHeight(28)
        self.combo_cookies.setToolTip("Adds --cookies-from-browser when a browser is selected.")
        add_grid_label("Cookies", 0, 1)
        config_grid.addWidget(self.combo_cookies, 1, 1)

        add_grid_label("Output dir", 2, 0)
        self.edit_output = QLineEdit("./downloads")
        self.edit_output.setFixedHeight(28)
        self.edit_output.setToolTip("Used only when a line does not already contain -d/--destination.")
        self.btn_output = self._btn("…", "Choose output directory")
        self.btn_output.setFixedSize(34, 28)
        self.btn_output.clicked.connect(self.choose_output_dir)
        out_row_widget = QWidget()
        out_row = QHBoxLayout(out_row_widget)
        out_row.setContentsMargins(0, 0, 0, 0)
        out_row.setSpacing(6)
        out_row.addWidget(self.edit_output, 1)
        out_row.addWidget(self.btn_output)
        config_grid.addWidget(out_row_widget, 3, 0, 1, 2)

        self.spin_retries = QSpinBox()
        self.spin_retries.setButtonSymbols(QAbstractSpinBox.UpDownArrows)
        self.spin_retries.setRange(0, 20)
        self.spin_retries.setValue(0)
        self.spin_retries.setFixedHeight(28)
        self.spin_retries.setMinimumWidth(82)
        self.spin_retries.setToolTip("0 means do not add --retries and keep each command unchanged.")
        add_grid_label("Retries", 4, 0)
        config_grid.addWidget(self.spin_retries, 5, 0)

        self.spin_delay = QSpinBox()
        self.spin_delay.setButtonSymbols(QAbstractSpinBox.UpDownArrows)
        self.spin_delay.setRange(0, 1440)
        self.spin_delay.setValue(0)
        self.spin_delay.setFixedHeight(28)
        self.spin_delay.setMinimumWidth(82)
        self.spin_delay.setSuffix(" min")
        self.spin_delay.setToolTip("Delay start in minutes. 0 starts immediately.")
        add_grid_label("Delay", 4, 1)
        config_grid.addWidget(self.spin_delay, 5, 1)
        config_grid.setColumnStretch(0, 1)
        config_grid.setColumnStretch(1, 1)
        config_l.addLayout(config_grid)
        run_l.addWidget(config_card)

        progress_card, progress_l = make_card("Progress")
        self.progress = QProgressBar()
        self.progress.setValue(0)
        self.progress.setMinimumHeight(18)
        self.lbl_summary = QLabel("0/0 - Done 0 - Failed 0 - Stopped 0")
        self.lbl_summary.setObjectName("metric")
        self.lbl_eta = QLabel("ETA: -")
        self.lbl_eta.setObjectName("subtle")
        progress_l.addWidget(self.progress)
        row_sum = QHBoxLayout()
        row_sum.addWidget(self.lbl_summary)
        row_sum.addStretch(1)
        row_sum.addWidget(self.lbl_eta)
        progress_l.addLayout(row_sum)
        run_l.addWidget(progress_card)

        control_card, control_l = make_card("Controls")
        self.btn_start = QPushButton("Start")
        self.btn_start.setObjectName("primary")
        self.btn_start.setFixedHeight(32)
        self.btn_pause = self._btn("Pause", "Pause queue after current active jobs finish")
        self.btn_pause.setFixedHeight(28)
        self.btn_cancel = self._btn("Cancel Row", "Cancel selected queue row")
        self.btn_cancel.setFixedHeight(28)
        self.btn_stop = QPushButton("Stop")
        self.btn_stop.setObjectName("danger")
        self.btn_stop.setFixedHeight(30)
        self.btn_retry = QPushButton("Retry Failed")
        self.btn_retry.setObjectName("warn")
        self.btn_retry.setFixedHeight(30)
        self.btn_start.clicked.connect(self.start_download)
        self.btn_pause.clicked.connect(self.toggle_pause)
        self.btn_cancel.clicked.connect(self.cancel_selected)
        self.btn_stop.clicked.connect(self.stop_download)
        self.btn_retry.clicked.connect(self.retry_failed)
        self.btn_stop.setEnabled(False)
        self.btn_pause.setEnabled(False)
        self.btn_retry.setEnabled(False)
        control_l.addWidget(self.btn_start)
        grid_control = QGridLayout()
        grid_control.setContentsMargins(0, 0, 0, 0)
        grid_control.setHorizontalSpacing(8)
        grid_control.setVerticalSpacing(8)
        grid_control.addWidget(self.btn_pause, 0, 0)
        grid_control.addWidget(self.btn_cancel, 0, 1)
        grid_control.addWidget(self.btn_stop, 1, 0)
        grid_control.addWidget(self.btn_retry, 1, 1)
        control_l.addLayout(grid_control)
        run_l.addWidget(control_card)

        quick_card, quick_l = make_card("Quick Tools")
        quick_l.setSpacing(6)
        quick_grid = QGridLayout()
        quick_grid.setContentsMargins(0, 0, 0, 0)
        quick_grid.setHorizontalSpacing(6)
        quick_grid.setVerticalSpacing(6)
        quick_actions = [
            ("Help", self.open_help_center, "Open the usage guide."),
            ("Composer", self.open_download_composer, "Create jobs and reusable gallery-dl defaults in one place."),
            ("Templates", self.export_template_pack, "Export TXT/CSV/XLSX examples and README files."),
            ("Import Preview", self.import_preview_dialog, "Preview TXT/CSV/XLSX before importing."),
            ("Preview", self.command_preview, "Preview final commands before running."),
            ("Dedupe", self.remove_exact_duplicates, "Find and remove exact duplicate jobs without merging different options."),
            ("Save Session", self.save_session, "Save the current queue and GUI settings."),
            ("Load Session", self.load_session, "Load a saved GUI session."),
            ("Health Check", self.health_check, "Check gallery-dl, config, output, and available disk space."),
            ("Feature Hub", self.open_feature_hub, "Open secondary project/system/interface helpers."),
        ]
        for i, (text, func, tip) in enumerate(quick_actions):
            b = QPushButton(text)
            b.setFixedHeight(28)
            # Let the two-column dashboard shrink on 100-125% DPI instead of
            # forcing a horizontal scrollbar across the whole Run panel.
            b.setMinimumWidth(0)
            b.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            b.setToolTip(tip)
            b.clicked.connect(func)  # type: ignore[arg-type]
            quick_grid.addWidget(b, i // 2, i % 2)
        quick_grid.setColumnStretch(0, 1)
        quick_grid.setColumnStretch(1, 1)
        quick_l.addLayout(quick_grid)
        # Stored as an instance attribute so set_compact_mode() can hide it. It was
        # previously a local, leaving the compact-mode hasattr guard permanently
        # False (dead branch).
        self.lbl_feature_hint = QLabel("Daily tools are here. Queue and Reports are in Tools. Archive and Compression are in Feature Hub.")
        self.lbl_feature_hint.setObjectName("subtle")
        self.lbl_feature_hint.setWordWrap(True)
        quick_l.addWidget(self.lbl_feature_hint)
        run_l.addWidget(quick_card)
        run_l.addStretch(1)
        self.side_tab_run = self.side_tabs.addTab(run_scroll, "Run")

        # ----- System tab ------------------------------------------------ #
        system_page = QWidget()
        system_l = QVBoxLayout(system_page)
        system_l.setContentsMargins(8, 8, 8, 8)
        system_l.setSpacing(12)

        system_card, system_card_l = make_card("System")
        system_grid = QGridLayout()
        system_grid.setContentsMargins(0, 0, 0, 0)
        system_grid.setHorizontalSpacing(8)
        system_grid.setVerticalSpacing(10)
        system_grid.setColumnMinimumWidth(0, 105)
        system_grid.setColumnStretch(1, 1)
        system_card_l.addLayout(system_grid)

        self.edit_gdl_cmd = QLineEdit(self.gdl_cmd or "")
        self.edit_gdl_cmd.setReadOnly(True)
        self.edit_gdl_cmd.setMinimumHeight(28)
        self.edit_gdl_cmd.setToolTip("Detected or manually selected gallery-dl executable/command.")
        self.btn_set_gdl = self._btn("Set", "Set gallery-dl executable path")
        self.btn_set_gdl.setMinimumHeight(28)
        self.btn_set_gdl.clicked.connect(self.set_gallery_dl_path)
        gdl_row = QHBoxLayout()
        gdl_row.setSpacing(8)
        gdl_row.addWidget(self.edit_gdl_cmd, 1)
        gdl_row.addWidget(self.btn_set_gdl)
        system_grid.addWidget(QLabel("gallery-dl"), 0, 0)
        system_grid.addLayout(gdl_row, 0, 1)

        self.edit_config_path = QLineEdit(self.config_path or "")
        self.edit_config_path.setReadOnly(True)
        self.edit_config_path.setMinimumHeight(28)
        self.edit_config_path.setToolTip("Current gallery-dl config path.")
        self.btn_choose_config = self._btn("Choose", "Choose gallery-dl config file")
        self.btn_choose_config.setMinimumHeight(28)
        self.btn_choose_config.clicked.connect(self.choose_config_path)
        cfg_row = QHBoxLayout()
        cfg_row.setSpacing(8)
        cfg_row.addWidget(self.edit_config_path, 1)
        cfg_row.addWidget(self.btn_choose_config)
        system_grid.addWidget(QLabel("Config"), 1, 0)
        system_grid.addLayout(cfg_row, 1, 1)

        system_l.addWidget(system_card)

        quick_card, quick_l = make_card("Quick Checks")
        self.btn_health_side = QPushButton("Health Check")
        self.btn_health_side.setMinimumHeight(30)
        self.btn_health_side.clicked.connect(self.health_check)
        self.btn_version_side = QPushButton("Check gallery-dl version")
        self.btn_version_side.setMinimumHeight(30)
        self.btn_version_side.setToolTip("Only checks the installed gallery-dl version using gallery-dl --version. It does not update gallery-dl or this GUI.")
        self.btn_version_side.clicked.connect(self.check_version)
        self.btn_validate_side = QPushButton("Validate Config")
        self.btn_validate_side.setMinimumHeight(30)
        self.btn_validate_side.clicked.connect(self.validate_config)
        self.btn_open_config_side = QPushButton("Open/Create Config")
        self.btn_open_config_side.setMinimumHeight(30)
        self.btn_open_config_side.setToolTip("Open the selected gallery-dl config. If missing, create an empty JSON config first.")
        self.btn_open_config_side.clicked.connect(self.open_or_create_config)
        self.btn_config_guide_side = QPushButton("Download Composer")
        self.btn_config_guide_side.setMinimumHeight(30)
        self.btn_config_guide_side.setToolTip("Create jobs, reusable defaults, and site presets in one workspace.")
        self.btn_config_guide_side.clicked.connect(self.open_download_composer)
        self.btn_open_output_side = QPushButton("Open Default Output")
        self.btn_open_output_side.setMinimumHeight(30)
        self.btn_open_output_side.setToolTip("Open the GUI default output folder from the Output dir field. It is used only when a command does not already include -d/--destination.")
        self.btn_open_output_side.clicked.connect(self.open_output_folder)
        quick_l.addWidget(self.btn_health_side)
        quick_l.addWidget(self.btn_version_side)
        quick_l.addWidget(self.btn_validate_side)
        quick_l.addWidget(self.btn_open_config_side)
        quick_l.addWidget(self.btn_config_guide_side)
        quick_l.addWidget(self.btn_open_output_side)
        system_l.addWidget(quick_card)
        system_l.addStretch(1)
        self.side_tab_tools = self.side_tabs.addTab(self._build_main_tools_page(), "Tools")
        self.side_tab_system = self.side_tabs.addTab(system_page, "System")

        self._build_feature_hub_dialog()
        self.apply_language_to_ui()

    def _build_main_tools_page(self) -> QWidget:
        """Main-frame advanced tools page.

        Queue and Reports live here for fast access. Archive and Compression
        are intentionally kept inside Feature Hub to keep the main frame lighter.
        """
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(10)
        scroll.setWidget(page)

        title = QLabel("Main Tools")
        title.setObjectName("sectionTitle")
        root.addWidget(title)
        note = QLabel("Queue and Reports are placed here in the main frame. Archive and Compression are back inside Feature Hub.")
        note.setObjectName("subtle")
        note.setWordWrap(True)
        root.addWidget(note)

        def add_action_card(section_title: str, actions: list[tuple[str, object, str]]) -> None:
            card = QFrame()
            card.setObjectName("card")
            lay = QGridLayout(card)
            lay.setContentsMargins(10, 9, 10, 10)
            lay.setHorizontalSpacing(8)
            lay.setVerticalSpacing(6)
            hdr = QLabel(section_title)
            hdr.setObjectName("sectionTitle")
            lay.addWidget(hdr, 0, 0, 1, 2)
            for i, (text, func, tip) in enumerate(actions, start=1):
                btn = QPushButton(text)
                btn.setMinimumHeight(28)
                btn.setMinimumWidth(136)
                btn.setToolTip(tip)
                btn.clicked.connect(func)  # type: ignore[arg-type]
                hint = QLabel(tip)
                hint.setObjectName("subtle")
                hint.setWordWrap(True)
                lay.addWidget(btn, i, 0)
                lay.addWidget(hint, i, 1)
            lay.setColumnStretch(0, 0)
            lay.setColumnStretch(1, 1)
            root.addWidget(card)

        add_action_card("Queue", [
            ("Resume Unfinished", self.resume_unfinished_queue, "Run queued, failed, stopped, or cancelled rows that are not done."),
            ("Group/Sort", self.group_queue_dialog, "Show counts by service/tag and sort queue safely."),
            ("Dry-run", self.dry_run_validator, "Validate final commands without starting downloads."),
            ("Retry Strategy", self.open_retry_strategy_editor, "Choose which error classes are safe to retry automatically."),
            ("Rate Policy", self.open_service_policy_editor, "Set optional per-service worker limit, retry count, and delay seconds."),
            ("Output by Tag", self.apply_output_folder_by_tag, "Convert tagged URL rows into commands with tag-based destination folders."),
        ])

        add_action_card("Reports", [
            ("Export Failed", self.export_failed, "Export failed, stopped, or cancelled commands."),
            ("Export Logs", self.export_logs, "Export combined logs to a text file."),
            ("Analyze Logs", self.show_log_analyzer, "Classify current combined logs by likely error type."),
            ("Error CSV", self.export_error_summary_csv, "Export failed/stopped/cancelled result summary to CSV."),
            ("Error HTML", self.error_report, "Create an HTML error report."),
            ("Audit HTML", self.export_audit_report, "Export a portable HTML troubleshooting report."),
        ])

        root.addStretch(1)
        return scroll

    def _make_action_tool_page(self, title: str, subtitle: str, sections: list[tuple[str, list[tuple[str, object, str]]]]) -> QWidget:
        """Build a scrollable main-frame tool page.

        These pages keep frequently used advanced tools visible in the main
        frame instead of hiding them inside Feature Hub.
        """
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(10)
        scroll.setWidget(page)

        title_label = QLabel(title)
        title_label.setObjectName("sectionTitle")
        root.addWidget(title_label)
        desc = QLabel(subtitle)
        desc.setObjectName("subtle")
        desc.setWordWrap(True)
        root.addWidget(desc)

        for section_title, actions in sections:
            card = QFrame()
            card.setObjectName("card")
            lay = QGridLayout(card)
            lay.setContentsMargins(10, 9, 10, 10)
            lay.setHorizontalSpacing(8)
            lay.setVerticalSpacing(6)
            hdr = QLabel(section_title)
            hdr.setObjectName("sectionTitle")
            lay.addWidget(hdr, 0, 0, 1, 2)
            for i, (text, func, tip) in enumerate(actions, start=1):
                btn = QPushButton(text)
                btn.setMinimumHeight(28)
                btn.setMinimumWidth(145)
                btn.setToolTip(tip)
                btn.clicked.connect(func)  # type: ignore[arg-type]
                hint = QLabel(tip)
                hint.setObjectName("subtle")
                hint.setWordWrap(True)
                lay.addWidget(btn, i, 0)
                lay.addWidget(hint, i, 1)
            lay.setColumnStretch(0, 0)
            lay.setColumnStretch(1, 1)
            root.addWidget(card)
        root.addStretch(1)
        return scroll

    def _build_archive_main_page(self) -> QWidget:
        return self._make_action_tool_page(
            "Archive & Backups",
            "gallery-dl archive skips already downloaded media. GUI backups/session files remain separate.",
            [
                ("gallery-dl Archive", [
                    ("Archive Help", self.open_gallery_dl_archive_help, "Explain --download-archive and extractor.archive behavior."),
                    ("Archive Path Assistant", self.archive_path_assistant, "Build a gallery-dl archive path snippet for config or command use."),
                    ("Export Archive Config", self.export_archive_config_example, "Create a safe config snippet that enables gallery-dl archive SQLite."),
                ]),
                ("Backups", [
                    ("Backup Config", self.backup_config_file, "Copy the active gallery-dl config to the backup folder."),
                    ("Backup App Data", self.backup_app_data, "Create a zip backup of sessions, history, profiles, and reports."),
                    ("Scan Output Folder", self.scan_output_folder, "Count files and estimate size in the GUI default output folder."),
                ]),
            ],
        )

    def _build_compression_main_page(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(10)
        scroll.setWidget(page)

        title = QLabel("Compression")
        title.setObjectName("sectionTitle")
        root.addWidget(title)
        note = QLabel("Optional post-processing. The main download flow still uses gallery-dl through subprocess.")
        note.setObjectName("subtle")
        note.setWordWrap(True)
        root.addWidget(note)

        comp_card = QFrame()
        comp_card.setObjectName("card")
        comp_l = QGridLayout(comp_card)
        comp_l.setContentsMargins(12, 12, 12, 12)
        comp_l.setHorizontalSpacing(10)
        comp_l.setVerticalSpacing(10)
        self.chk_compress = QCheckBox("Compress after successful download")
        self.chk_compress.setToolTip("Create an archive from the job destination folder after gallery-dl finishes.")
        self.chk_convert_webp = QCheckBox("Convert PNG to WebP")
        self.chk_convert_webp.setToolTip("Requires Pillow. If missing, the program logs a warning and continues.")
        self.combo_archive = QComboBox()
        self.combo_archive.addItems(["zip", "7z", "tar"])
        self.combo_archive.setCurrentText("zip")
        self.combo_archive.setFixedHeight(30)
        self.combo_part = QComboBox()
        self.combo_part.addItems(["none", "2 GB", "4 GB", "8 GB"])
        self.combo_part.setEnabled(False)
        self.combo_part.setFixedHeight(30)
        comp_l.addWidget(self.chk_compress, 0, 0, 1, 2)
        comp_l.addWidget(QLabel("Archive format"), 1, 0)
        comp_l.addWidget(self.combo_archive, 1, 1)
        comp_l.addWidget(QLabel("Max per part"), 2, 0)
        comp_l.addWidget(self.combo_part, 2, 1)
        comp_l.addWidget(self.chk_convert_webp, 3, 0, 1, 2)
        tip = QLabel("ZIP works with Python standard library. 7z/tar requires 7z/7za/7zz in PATH. PNG to WebP requires Pillow.")
        tip.setObjectName("subtle")
        tip.setWordWrap(True)
        comp_l.addWidget(tip, 4, 0, 1, 2)
        comp_l.setColumnStretch(1, 1)
        root.addWidget(comp_card)
        root.addStretch(1)
        return scroll

    def _build_feature_hub_dialog(self) -> None:
        """Modern Feature Hub.

        It uses a compact left navigation and readable feature cards. This avoids
        dozens of tiny buttons in the main window while keeping all features one
        click away.
        """
        self.feature_dialog = QDialog(self)
        self.feature_dialog.setWindowTitle("Feature Hub - Tools")
        self.feature_dialog.resize(740, 480)
        self.feature_dialog.setMinimumSize(650, 430)
        root = QHBoxLayout(self.feature_dialog)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(9)

        nav = QFrame()
        nav.setObjectName("card")
        nav.setFixedWidth(142)
        nav_l = QVBoxLayout(nav)
        nav_l.setContentsMargins(8, 8, 8, 8)
        nav_l.setSpacing(5)
        hub_title = QLabel("Feature Hub")
        hub_title.setObjectName("sectionTitle")
        nav_l.addWidget(hub_title)
        hub_note = QLabel("Project, archive, compression, system, and interface helpers. Queue and Reports stay in the main Tools tab.")
        hub_note.setObjectName("subtle")
        hub_note.setWordWrap(True)
        nav_l.addWidget(hub_note)

        self.feature_stack = QStackedWidget()

        def make_nav_button(text: str, page_index: int) -> QPushButton:
            btn = QPushButton(text)
            btn.setCheckable(True)
            btn.setFixedHeight(26)
            btn.setToolTip(f"Open {text} tools")
            btn.clicked.connect(lambda _checked=False, i=page_index: self._show_feature_page(i))
            nav_l.addWidget(btn)
            return btn

        self.feature_nav_buttons: list[QPushButton] = []

        def make_page(title: str, subtitle: str, specs: list[tuple[str, object, str]]) -> QWidget:
            """Create a compact Feature Hub page.

            v12 cleanup rule:
            - one compact action list, not one large card per button
            - buttons exist only for real actions
            - explanatory text stays inline as short descriptions
            - no duplicate queue/database tools.
            """
            page = QWidget()
            page_l = QVBoxLayout(page)
            page_l.setContentsMargins(0, 0, 0, 0)
            page_l.setSpacing(5)

            title_label = QLabel(title)
            title_label.setStyleSheet("font-size: 15px; font-weight: 900;")
            desc_label = QLabel(subtitle)
            desc_label.setObjectName("subtle")
            desc_label.setWordWrap(True)
            page_l.addWidget(title_label)
            page_l.addWidget(desc_label)

            action_panel = QFrame()
            action_panel.setObjectName("card")
            panel_l = QGridLayout(action_panel)
            panel_l.setContentsMargins(8, 7, 8, 7)
            panel_l.setHorizontalSpacing(8)
            panel_l.setVerticalSpacing(4)

            for i, (text, func, tip) in enumerate(specs):
                b = QPushButton(text)
                b.setFixedHeight(25)
                b.setMinimumWidth(160)
                b.setMaximumWidth(205)
                b.setToolTip(tip or text)
                b.clicked.connect(func)  # type: ignore[arg-type]

                hint = QLabel(tip or "")
                hint.setObjectName("subtle")
                hint.setWordWrap(True)
                hint.setTextInteractionFlags(Qt.TextSelectableByMouse)

                panel_l.addWidget(b, i, 0)
                panel_l.addWidget(hint, i, 1)

            panel_l.setColumnStretch(0, 0)
            panel_l.setColumnStretch(1, 1)
            page_l.addWidget(action_panel)
            page_l.addStretch(1)
            return page

        pages: list[tuple[str, QWidget]] = []

        # Feature Hub v15: advanced tools only. Daily-use tools moved to the Run panel.
        pages.append(("Profiles", make_page("Profiles & Data", "Advanced project data tools. Basic Save/Load Session is now on the Run panel.", [
            ("Export Current TXT", self.export_txt, "Export current commands to a .txt input database."),
            ("Save Project Profile", self.save_project_profile, "Save current settings and commands as a reusable project profile."),
            ("Load Project Profile", self.load_project_profile, "Load a project profile from the profile folder or any JSON file."),
            ("Open App Data Folder", self.open_app_data_dir, "Open the GUI folder used for autosave, sessions, reports, backups, and profiles."),
        ])))

        pages.append(("Archive", self._build_archive_main_page()))
        pages.append(("Compress", self._build_compression_main_page()))

        pages.append(("System", make_page("System Advanced", "Advanced local setup tools. Quick checks remain in the right System panel.", [
            ("Open/Create Config", self.open_or_create_config, "Open selected config, or create it if missing."),
            ("Config Template", self.generate_config_template, "Create a starter gallery-dl JSON config template."),
            ("Dependency Helper", self.show_dependency_helper, "Show install commands for optional and required dependencies."),
        ])))

        interface_page = QWidget()
        interface_l = QVBoxLayout(interface_page)
        interface_l.setContentsMargins(0, 0, 0, 0)
        interface_l.setSpacing(6)
        interface_title = QLabel("Interface Helpers")
        interface_title.setStyleSheet("font-size: 17px; font-weight: 900;")
        interface_desc = QLabel("Action buttons stay here. Info-only guidance is shown directly so users do not need to click extra popups.")
        interface_desc.setObjectName("subtle")
        interface_desc.setWordWrap(True)
        interface_l.addWidget(interface_title)
        interface_l.addWidget(interface_desc)

        info_card = QFrame()
        info_card.setObjectName("card")
        info_l = QVBoxLayout(info_card)
        info_l.setContentsMargins(8, 8, 8, 8)
        info_l.setSpacing(5)
        info_text = QLabel(
            "<b>GUI scaling:</b> If the UI looks too large or too small on high-DPI screens, start Python with "
            "<code>QT_SCALE_FACTOR=1.1</code> or <code>QT_AUTO_SCREEN_SCALE_FACTOR=1</code> before launching.<br><br>"
            f"<b>Portable workflow:</b> automatic app state is stored in <code>{html.escape(str(APP_DIR))}</code>. "
            "For project portability, keep the .py file, TXT/CSV/XLSX database, exported sessions, profiles, and config backups in one project folder.<br><br>"
            "<b>Tray/notification:</b> notification is optional. The app may show a finish notification if the system tray is available. "
            "It does not force minimize-to-tray."
        )
        info_text.setWordWrap(True)
        info_l.addWidget(info_text)
        interface_l.addWidget(info_card)

        action_card = QFrame()
        action_card.setObjectName("card")
        action_l = QGridLayout(action_card)
        action_l.setContentsMargins(8, 8, 8, 8)
        action_l.setHorizontalSpacing(8)
        action_l.setVerticalSpacing(6)
        interface_actions = [
            ("Toggle Theme", self.toggle_theme, "Switch dark/light theme."),
            ("Toggle Compact Mode", self.toggle_compact_mode, "Switch between normal and compact side layout."),
            # Restores a lost control: audit_mode was saved/loaded in sessions
            # and checked at Start, but no UI ever set it (only the "Audit
            # Mode" translation string survived past refactors) — the
            # preflight feature was unreachable without hand-editing JSON.
            ("Audit Mode", self.toggle_audit_mode, "Toggle preflight audit before every start (disk space, worker sanity)."),
            ("Settings Snapshot", self.export_settings_snapshot, "Export current GUI settings as JSON."),
        ]
        for i, (text, func, tip) in enumerate(interface_actions):
            btn = QPushButton(text)
            btn.setFixedHeight(26)
            btn.setToolTip(tip)
            btn.clicked.connect(func)  # type: ignore[arg-type]
            action_l.addWidget(btn, i // 2, i % 2)
        action_l.setColumnStretch(0, 1)
        action_l.setColumnStretch(1, 1)
        interface_l.addWidget(action_card)
        interface_l.addStretch(1)
        pages.append(("Interface", interface_page))

        preferred_feature_order = ["Profiles", "Archive", "Compress", "System", "Interface"]
        order_index = {name: i for i, name in enumerate(preferred_feature_order)}
        pages.sort(key=lambda item: order_index.get(item[0], 999))

        for idx, (name, page) in enumerate(pages):
            self.feature_stack.addWidget(page)
            self.feature_nav_buttons.append(make_nav_button(name, idx))

        nav_l.addStretch(1)

        content = QFrame()
        content.setObjectName("card")
        content_l = QVBoxLayout(content)
        content_l.setContentsMargins(10, 10, 10, 10)
        content_l.addWidget(self.feature_stack, 1)

        root.addWidget(nav)
        root.addWidget(content, 1)
        self._show_feature_page(0)

    def set_help_language(self, language: str) -> None:
        language = "Indonesia" if str(language).lower().startswith("indo") else "English"
        self.help_language = language
        if hasattr(self, "combo_help_language") and self.combo_help_language.currentText() != language:
            self.combo_help_language.blockSignals(True)
            self.combo_help_language.setCurrentText(language)
            self.combo_help_language.blockSignals(False)
        self.apply_language_to_ui()
        self.autosave_session()

    def _ui_is_indonesian(self) -> bool:
        return getattr(self, "help_language", "English") == "Indonesia"

    def apply_language_to_ui(self) -> None:
        """Apply the selected UI language to visible main-frame controls.

        This is intentionally lightweight: it updates labels, buttons, tabs,
        placeholders, and status text without rebuilding the window or changing
        download behavior.
        """
        ind = self._ui_is_indonesian()
        if hasattr(self, "lbl_language"):
            self.lbl_language.setText("Bahasa" if ind else "Lang")
        if hasattr(self, "lbl_subtitle"):
            self.lbl_subtitle.setText("SCRAPE")
        if hasattr(self, "lbl_brand_tagline"):
            self.lbl_brand_tagline.setText(
                "GALLERY-DL  •  BATCH PIPELINE"
                if not ind
                else "GALLERY-DL  •  ALUR BATCH"
            )
        if hasattr(self, "btn_import"):
            self.btn_import.setText("Impor" if ind else "Import")
            self.btn_paste.setText("Tempel" if ind else "Paste")
            self.btn_clear_input.setText("Bersihkan" if ind else "Clear")
        if hasattr(self, "btn_start"):
            self.btn_start.setText("Mulai" if ind else "Start")
            if self.pause_event.is_set():
                self.btn_pause.setText("Lanjut" if ind else "Resume")
            else:
                self.btn_pause.setText("Jeda" if ind else "Pause")
            self.btn_cancel.setText("Batal Baris" if ind else "Cancel Row")
            self.btn_stop.setText("Stop" if not ind else "Hentikan")
            self.btn_retry.setText("Ulangi" if ind else "Retry")
        if hasattr(self, "btn_health_side"):
            self.btn_health_side.setText("Cek Sistem" if ind else "Health Check")
            self.btn_version_side.setText("Cek Versi gallery-dl" if ind else "Check gallery-dl version")
            self.btn_validate_side.setText("Validasi Config" if ind else "Validate Config")
            self.btn_open_config_side.setText("Buka/Buat Config" if ind else "Open/Create Config")
            if hasattr(self, "btn_config_guide_side"):
                self.btn_config_guide_side.setText("Perancang Download" if ind else "Download Composer")
            self.btn_open_output_side.setText("Buka Output Default" if ind else "Open Default Output")
        if hasattr(self, "side_tabs"):
            names = {
                "side_tab_run": "Jalankan" if ind else "Run",
                "side_tab_tools": "Alat" if ind else "Tools",
                "side_tab_system": "Sistem" if ind else "System",
            }
            for attr, text in names.items():
                if hasattr(self, attr):
                    self.side_tabs.setTabText(getattr(self, attr), text)
        if hasattr(self, "tabs"):
            main_tabs = ["Log", "Queue", "Workers", "History"] if not ind else ["Log", "Antrean", "Worker", "Riwayat"]
            for i, text in enumerate(main_tabs):
                if i < self.tabs.count():
                    self.tabs.setTabText(i, text)
        if hasattr(self, "txt_commands"):
            placeholder_id = """Tempel URL atau full command gallery-dl. Satu entri per baris.
Contoh: gallery-dl -d "F:\\Rips\\Download\\Creator" https://example.com/post/123 --range 1-10"""
            placeholder_en = """Paste URL or full gallery-dl command here. One entry per line.
Example: gallery-dl -d "F:\\Rips\\Download\\Creator" https://example.com/post/123 --range 1-10"""
            self.txt_commands.setPlaceholderText(placeholder_id if ind else placeholder_en)
        if hasattr(self, "log_all"):
            self.log_all.setPlaceholderText("Log gabungan akan muncul di sini." if ind else "Combined logs will appear here.")
        if hasattr(self, "search_queue"):
            self.search_queue.setPlaceholderText("Cari URL, layanan, ID, tujuan, tag..." if ind else "Search URL, service, ID, destination, tag...")
        if hasattr(self, "lbl_eta") and self.active_workers <= 0:
            self.lbl_eta.setText("Sisa waktu: -" if ind else "ETA: -")
        if hasattr(self, "tbl_queue"):
            self.tbl_queue.setHorizontalHeaderLabels((["#", "Layanan", "ID", "Tujuan", "Tag", "Status", "Statistik"] if ind else ["#", "Service", "ID", "Destination", "Tag", "Status", "Stats"]))
        if hasattr(self, "tbl_workers"):
            self.tbl_workers.setHorizontalHeaderLabels((["Worker", "Status", "Link Aktif"] if ind else ["Worker", "Status", "Current Link"]))
        if hasattr(self, "tbl_history"):
            self.tbl_history.setHorizontalHeaderLabels((["Waktu", "Status", "Layanan", "ID", "URL"] if ind else ["Time", "Status", "Service", "ID", "URL"]))
        def set_combo_items(combo: QComboBox, en_items: list[str], id_items: list[str]) -> None:
            current = combo.currentText()
            en_to_id = dict(zip(en_items, id_items, strict=False))
            id_to_en = dict(zip(id_items, en_items, strict=False))
            key = id_to_en.get(current, current)
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(id_items if ind else en_items)
            target = en_to_id.get(key, key) if ind else key
            idx = combo.findText(target)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            combo.blockSignals(False)
        if hasattr(self, "combo_log_filter"):
            set_combo_items(self.combo_log_filter, ["All", "Error", "Warning", "Done", "Current worker lines"], ["Semua", "Galat", "Peringatan", "Selesai", "Baris worker aktif"])
        if hasattr(self, "combo_filter"):
            set_combo_items(self.combo_filter, ["All", "Queued", "Running", "Done", "Failed", "Stopped", "Cancelled"], ["Semua", "Antre", "Berjalan", "Selesai", "Gagal", "Dihentikan", "Dibatalkan"])
        self._translate_visible_texts(ind)
        self._refresh_env()

    def _translation_pairs(self) -> dict[str, str]:
        """Exact-text Indonesian translations for visible static UI strings.

        This keeps the language toggle useful without rebuilding the window. It
        translates labels/buttons/tooltips created locally inside pages, including
        Feature Hub pages.
        """
        return {
            "0 entry": "0 entri",
            "Ready": "Siap",
            "Running": "Berjalan",
            "Batch Downloader": "Pengunduh Batch",
            "Cancel": "Batal",
            "Close": "Tutup",
            "Copy Guide": "Salin Panduan",
            "Export CSV": "Ekspor CSV",
            "Export Pack": "Ekspor Paket",
            "Export TXT": "Ekspor TXT",
            "Export XLSX": "Ekspor XLSX",
            "OK": "OK",
            "Pause": "Jeda",
            "Resume": "Lanjut",
            "Save": "Simpan",
            "Save Policy": "Simpan Kebijakan",
            "Sort by Service": "Urutkan berdasarkan Layanan",
            "Sort by Tag": "Urutkan berdasarkan Tag",
            "Stop": "Hentikan",
            "Tools": "Alat",
            "Lang": "Bahasa",
            "Config Guide": "Panduan Config",
            "Download Composer": "Perancang Download",

            "Site Presets": "Preset Situs",
            "Select common": "Pilih umum",
            "Select art/creator": "Pilih art/creator",
            "Select social": "Pilih sosial",
            "Select booru": "Pilih booru",
            "Use cookies for login-required presets": "Pakai cookies untuk preset yang butuh login",
            "Custom category": "Category custom",
            "Select common site presets. For other sites, add extractor categories in the Custom field.": "Pilih preset situs yang sering dipakai. Untuk situs lain, masukkan category extractor di kolom Custom.",
            "Open the complete gallery-dl config guide and create a starter JSON config.": "Buka panduan config gallery-dl lengkap dan buat config JSON awal.",
            "URL / Commands": "URL / Perintah",
            "No file loaded": "Belum ada file",
            "Log filter:": "Filter log:",
            "Filter:": "Filter:",
            "Refresh": "Muat ulang",
            "Clear": "Bersihkan",
            "Run Config": "Konfigurasi Jalankan",
            "Workers": "Worker",
            "Output dir": "Folder output",
            "Cookies browser": "Cookies browser",
            "Retries": "Coba ulang",
            "Delay": "Tunda",
            "Progress": "Progres",
            "Controls": "Kontrol",
            "Quick Tools": "Alat Cepat",
            "Help": "Bantuan",
            "Composer": "Perancang",
            "Dedupe": "Hapus Duplikat",
            "Find and remove exact duplicate jobs without merging different options.": "Cari dan hapus job duplikat persis tanpa menggabungkan opsi yang berbeda.",
            "Check gallery-dl, config, output, and available disk space.": "Periksa gallery-dl, config, output, dan ruang disk yang tersedia.",
            "Templates": "Template",
            "Import Preview": "Pratinjau Impor",
            "Preview": "Pratinjau",
            "Save Session": "Simpan Sesi",
            "Load Session": "Muat Sesi",
            "Feature Hub": "Hub Fitur",
            "System": "Sistem",
            "Quick Checks": "Cek Cepat",
            "Health Check": "Cek Sistem",
            "Check gallery-dl version": "Cek Versi gallery-dl",
            "Validate Config": "Validasi Config",
            "Open/Create Config": "Buka/Buat Config",
            "Open Default Output": "Buka Output Default",
            "Set": "Atur",
            "Choose": "Pilih",
            "Main Tools": "Alat Utama",
            "Queue": "Antrean",
            "Reports": "Laporan",
            "Resume Unfinished": "Lanjutkan yang Belum Selesai",
            "Group/Sort": "Kelompok/Urut",
            "Dry-run": "Validasi Kering",
            "Retry Strategy": "Strategi Coba Ulang",
            "Rate Policy": "Kebijakan Rate",
            "Output by Tag": "Output per Tag",
            "Export Failed": "Ekspor Gagal",
            "Export Logs": "Ekspor Log",
            "Analyze Logs": "Analisis Log",
            "Error CSV": "CSV Error",
            "Error HTML": "HTML Error",
            "Audit HTML": "HTML Audit",
            "Archive & Backups": "Arsip & Backup",
            "gallery-dl Archive": "Arsip gallery-dl",
            "Archive Help": "Bantuan Arsip",
            "Archive Path Assistant": "Asisten Path Arsip",
            "Export Archive Config": "Ekspor Config Arsip",
            "Backups": "Backup",
            "Backup Config": "Backup Config",
            "Backup App Data": "Backup Data App",
            "Scan Output Folder": "Pindai Folder Output",
            "Compression": "Kompresi",
            "Compress after successful download": "Kompres setelah download sukses",
            "Convert PNG to WebP": "Ubah PNG ke WebP",
            "Archive format": "Format arsip",
            "Max per part": "Maks per part",
            "Profiles & Data": "Profil & Data",
            "Export Current TXT": "Ekspor TXT Saat Ini",
            "Save Project Profile": "Simpan Profil Proyek",
            "Load Project Profile": "Muat Profil Proyek",
            "Open App Data Folder": "Buka Folder Data App",
            "System Advanced": "Sistem Lanjutan",
            "Config Template": "Template Config",
            "Dependency Helper": "Bantuan Dependensi",
            "Interface Helpers": "Bantuan Antarmuka",
            "Toggle Theme": "Ganti Tema",
            "Toggle Compact Mode": "Ganti Mode Ringkas",
            "Settings Snapshot": "Snapshot Pengaturan",

            "Autosave restored": "Autosave dipulihkan",
            "Basic Save/Load Session is now on the Run panel.": "Simpan/Muat Sesi dasar sekarang ada di panel Jalankan.",
            "Advanced project data tools. Basic Save/Load Session is now on the Run panel.": "Alat data proyek lanjutan. Simpan/Muat Sesi dasar sekarang ada di panel Jalankan.",
            "Export current commands to a .txt input database.": "Ekspor command saat ini ke database input .txt.",
            "Save current settings and commands as a reusable project profile.": "Simpan pengaturan dan command saat ini sebagai profil proyek yang bisa dipakai ulang.",
            "Load a project profile from the profile folder or any JSON file.": "Muat profil proyek dari folder profil atau file JSON apa pun.",
            "Open the GUI folder used for autosave, sessions, reports, backups, and profiles.": "Buka folder GUI untuk autosave, sesi, laporan, backup, dan profil.",
            "Project, archive, compression, system, and interface helpers. Queue and Reports stay in the main Tools tab.": "Bantuan profil, arsip, kompresi, sistem, dan antarmuka. Antrean dan Laporan tetap di tab Alat utama.",
            "Profiles": "Profil",
            "Archive": "Arsip",
            "Compress": "Kompres",
            "Interface": "Antarmuka",
            "Advanced local setup tools. Quick checks remain in the right System panel.": "Alat pengaturan lokal lanjutan. Cek cepat tetap ada di panel Sistem sebelah kanan.",
            "Open selected config, or create it if missing.": "Buka config terpilih, atau buat jika belum ada.",
            "Create a starter gallery-dl JSON config template.": "Buat template awal config JSON gallery-dl.",
            "Show install commands for optional and required dependencies.": "Tampilkan command instalasi untuk dependensi wajib dan opsional.",
            "Switch dark/light theme.": "Ganti tema gelap/terang.",
            "Switch between normal and compact side layout.": "Ganti layout samping normal/ringkas.",
            "Export current GUI settings as JSON.": "Ekspor pengaturan GUI saat ini sebagai JSON.",
            "Action buttons stay here. Info-only guidance is shown directly so users do not need to click extra popups.": "Tombol aksi ada di sini. Panduan info ditampilkan langsung agar pengguna tidak perlu membuka popup tambahan.",
            "Daily tools are here. Queue and Reports are in Tools. Archive and Compression are in Feature Hub.": "Alat harian ada di sini. Antrean dan Laporan ada di Alat. Arsip dan Kompresi ada di Hub Fitur.",
            "Queue and Reports are placed here in the main frame. Archive and Compression are back inside Feature Hub.": "Antrean dan Laporan ada di frame utama. Arsip dan Kompresi kembali ke Hub Fitur.",
            "Queue control, validation, retry policy, and output routing. These are now visible in the main frame.": "Kontrol antrean, validasi, kebijakan coba ulang, dan routing output. Bagian ini terlihat di frame utama.",
            "Export failed jobs, logs, audit output, and classified error summaries from the main frame.": "Ekspor job gagal, log, output audit, dan ringkasan error terklasifikasi dari frame utama.",
            "gallery-dl archive skips already downloaded media. GUI backups/session files remain separate.": "Arsip gallery-dl melewati media yang sudah pernah diunduh. Backup/sesi GUI tetap terpisah.",
            "Optional post-processing. The main download flow still uses gallery-dl through subprocess.": "Post-processing opsional. Alur download utama tetap memakai gallery-dl lewat subprocess.",
            "ZIP works with Python standard library. 7z/tar requires 7z/7za/7zz in PATH. PNG to WebP requires Pillow.": "ZIP memakai library standar Python. 7z/tar butuh 7z/7za/7zz di PATH. PNG ke WebP butuh Pillow.",
            "Open the usage guide.": "Buka panduan penggunaan.",
            "Build a safe gallery-dl command.": "Buat command gallery-dl yang aman.",
            "Export TXT/CSV/XLSX examples and README files.": "Ekspor contoh TXT/CSV/XLSX dan file README.",
            "Preview TXT/CSV/XLSX before importing.": "Pratinjau TXT/CSV/XLSX sebelum impor.",
            "Preview final commands before running.": "Pratinjau command final sebelum dijalankan.",
            "Save the current queue and GUI settings.": "Simpan antrean dan pengaturan GUI saat ini.",
            "Load a saved GUI session.": "Muat sesi GUI yang tersimpan.",
            "Open secondary project/system/interface helpers.": "Buka bantuan tambahan untuk proyek/sistem/antarmuka.",
            "Run queued, failed, stopped, or cancelled rows that are not done.": "Jalankan baris antre/gagal/dihentikan/dibatalkan yang belum selesai.",
            "Show counts by service/tag and sort queue safely.": "Tampilkan jumlah per layanan/tag dan urutkan antrean dengan aman.",
            "Validate final commands without starting downloads.": "Validasi command final tanpa mulai download.",
            "Choose which error classes are safe to retry automatically.": "Pilih kelas error yang aman untuk dicoba ulang otomatis.",
            "Set optional per-service worker limit, retry count, and delay seconds.": "Atur batas worker, jumlah retry, dan jeda per layanan.",
            "Convert tagged URL rows into commands with tag-based destination folders.": "Ubah baris URL bertag menjadi command dengan folder tujuan berbasis tag.",
            "Export failed, stopped, or cancelled commands.": "Ekspor command yang gagal, dihentikan, atau dibatalkan.",
            "Export combined logs to a text file.": "Ekspor log gabungan ke file teks.",
            "Classify current combined logs by likely error type.": "Klasifikasikan log gabungan berdasarkan kemungkinan jenis error.",
            "Export failed/stopped/cancelled result summary to CSV.": "Ekspor ringkasan hasil gagal/dihentikan/dibatalkan ke CSV.",
            "Create an HTML error report.": "Buat laporan error HTML.",
            "Export a portable HTML troubleshooting report.": "Ekspor laporan troubleshooting HTML portabel.",
            "Explain --download-archive and extractor.archive behavior.": "Jelaskan perilaku --download-archive dan extractor.archive.",
            "Build a gallery-dl archive path snippet for config or command use.": "Buat snippet path arsip gallery-dl untuk config atau command.",
            "Create a safe config snippet that enables gallery-dl archive SQLite.": "Buat snippet config aman untuk mengaktifkan arsip SQLite gallery-dl.",
            "Copy the active gallery-dl config to the backup folder.": "Salin config gallery-dl aktif ke folder backup.",
            "Create a zip backup of sessions, history, profiles, and reports.": "Buat backup ZIP untuk sesi, riwayat, profil, dan laporan.",
            "Count files and estimate size in the GUI default output folder.": "Hitung file dan estimasi ukuran di folder output default GUI.",
            "Create an archive from the job destination folder after gallery-dl finishes.": "Buat arsip dari folder tujuan job setelah gallery-dl selesai.",
            "Requires Pillow. If missing, the program logs a warning and continues.": "Butuh Pillow. Jika tidak ada, program menulis peringatan ke log dan lanjut.",
            "Detected or manually selected gallery-dl executable/command.": "gallery-dl executable/command yang terdeteksi atau dipilih manual.",
            "Set gallery-dl executable path": "Atur path executable gallery-dl",
            "Current gallery-dl config path.": "Path config gallery-dl saat ini.",
            "Choose gallery-dl config file": "Pilih file config gallery-dl",
            "Only checks the installed gallery-dl version using gallery-dl --version. It does not update gallery-dl or this GUI.": "Hanya mengecek versi gallery-dl terpasang memakai gallery-dl --version. Tidak mengupdate gallery-dl atau GUI ini.",
            "Open the selected gallery-dl config. If missing, create an empty JSON config first.": "Buka config gallery-dl terpilih. Jika belum ada, buat config JSON kosong lebih dulu.",
            "Open the GUI default output folder from the Output dir field. It is used only when a command does not already include -d/--destination.": "Buka folder output default GUI dari field Folder output. Ini hanya dipakai jika command belum punya -d/--destination.",
            "Number of parallel download workers.": "Jumlah worker download paralel.",
            "Adds --cookies-from-browser when a browser is selected.": "Menambahkan --cookies-from-browser jika browser dipilih.",
            "Used only when a line does not already contain -d/--destination.": "Dipakai hanya jika baris belum memiliki -d/--destination.",
            "Choose output directory": "Pilih folder output",
            "0 means do not add --retries and keep each command unchanged.": "0 berarti tidak menambahkan --retries dan command tetap apa adanya.",
            "Delay start in minutes. 0 starts immediately.": "Tunda mulai dalam menit. 0 berarti langsung mulai.",
        }

    def _translate_visible_texts(self, ind: bool) -> None:
        pairs = self._translation_pairs()
        reverse = {v: k for k, v in pairs.items()}
        def tr(text: str) -> str:
            if not text:
                return text
            return pairs.get(text, text) if ind else reverse.get(text, text)
        for widget in self.findChildren(QWidget):
            if isinstance(widget, QGroupBox):
                widget.setTitle(tr(widget.title()))
            elif isinstance(widget, (QPushButton, QCheckBox, QLabel)):
                try:
                    widget.setText(tr(widget.text()))
                except Exception:
                    pass
            try:
                tip = widget.toolTip()
                if tip:
                    widget.setToolTip(tr(tip))
            except Exception:
                pass
        if hasattr(self, "feature_dialog"):
            self.feature_dialog.setWindowTitle("Hub Fitur - Alat" if ind else "Feature Hub - Tools")
        self._update_counts()

    def database_help_text(self, language: str | None = None) -> str:
        language = language or getattr(self, "help_language", "English")
        if language == "Indonesia":
            return """Pusat Bantuan GdlScrape
=======================

1) Fungsi utama aplikasi
------------------------
Aplikasi ini adalah launcher batch untuk gallery-dl. Aplikasi tidak mengimpor gallery-dl sebagai library Python. Semua download tetap dijalankan lewat subprocess gallery-dl.

Input yang diterima:
- Satu URL per baris.
- Satu command gallery-dl lengkap per baris.
- Database TXT.
- Database CSV.
- Database XLSX jika openpyxl terpasang.

2) Format database TXT
----------------------
Gunakan satu URL atau satu command gallery-dl lengkap per baris.
Baris kosong diabaikan.
Baris yang diawali # dipakai sebagai tag/grup untuk baris setelahnya.

Contoh netral:
# Group A
https://example.com/user/123456
https://example.com/post/789012

# Full commands are allowed
gallery-dl -d "D:\\Rips\\Creator A" https://example.com/user/123456 --no-check-certificate
gallery-dl --destination "D:\\Rips\\Creator B" https://example.com/post/111
gallery-dl --destination="D:\\Rips\\Creator C" https://example.com/post/222

Catatan:
- Contoh memakai example.com hanya sebagai placeholder.
- Ganti example.com dengan URL asli yang didukung gallery-dl.
- Jika folder mengandung spasi, gunakan tanda kutip.

3) Format database CSV
----------------------
Header yang disarankan:
url,destination,extra_args,enabled,tag,notes

Arti kolom:
- url: URL biasa atau full command gallery-dl.
- destination: folder tujuan. GUI akan menambah -d jika url berisi URL biasa.
- extra_args: argumen tambahan gallery-dl, contoh --range 1-10.
- enabled: true/false. Jika false, baris tidak diimpor.
- tag: label grup, contoh Creator A, Batch 01, Project X.
- notes: catatan bebas untuk user.

Alias kolom yang didukung:
- url, URL, Link, link
- destination, Destination, Folder, folder
- extra_args, Extra Args, Args, args

Nilai enabled:
- true, yes, 1, on = diimpor.
- false, no, 0, off = dilewati.

Contoh CSV:
url,destination,extra_args,enabled,tag,notes
https://example.com/post/123,D:\\Rips\\Creator A,--range 1-10,true,Creator A,batch pertama
https://example.com/post/456,D:\\Rips\\Creator B,,true,Creator B,tanpa argumen tambahan
https://example.com/post/789,,,false,Skipped,baris ini tidak diimpor

Jika CSV tidak memiliki header yang dikenali, aplikasi mencoba membaca kolom pertama sebagai URL/command.

4) Format database XLSX
-----------------------
Gunakan kolom yang sama seperti CSV di worksheet pertama:
url | destination | extra_args | enabled | tag | notes

Aturan:
- Baris pertama harus berisi header.
- Setiap baris berikutnya adalah satu entry database.
- Jika openpyxl belum tersedia, install dengan:
  pip install openpyxl

5) Format full command
----------------------
Full command dipertahankan. GUI akan melepas awalan executable gallery-dl, lalu menjalankan argumen tersebut memakai path gallery-dl yang dipilih di GUI.

Contoh:
gallery-dl -d "D:\\Rips\\Creator A" https://example.com/post/123
gallery-dl --destination "D:\\Rips\\Creator B" https://example.com/post/456
gallery-dl --destination="D:\\Rips\\Creator C" https://example.com/post/789
python -m gallery_dl -d "D:\\Rips\\Creator D" https://example.com/post/999

6) Aturan Output dir
--------------------
Field Output dir adalah folder output default milik GUI.
Folder ini hanya dipakai jika baris input belum memiliki:
- -d
- --destination
- --destination=...

Contoh URL biasa:
https://example.com/post/123

Akan dijalankan kira-kira menjadi:
gallery-dl -d ./downloads https://example.com/post/123

Tetapi command ini tetap memakai foldernya sendiri:
gallery-dl -d "D:\\Rips\\Creator" https://example.com/post/123

7) Workflow aman yang disarankan
--------------------------------
1. Klik Help > Export Pack atau Run > Templates.
2. Edit template CSV/XLSX/TXT.
3. Import database.
4. Jalankan Import Preview jika memakai file besar.
5. Cek Command Preview sebelum Start.
6. Jalankan Health Check setelah mengubah path gallery-dl, config, atau output folder.
7. Mulai dengan 1 sampai 3 worker dulu.
8. Gunakan Retry Failed setelah masalah cookies, config, network, atau path diperbaiki.

8) Catatan penting
------------------
- Check gallery-dl Version hanya mengecek versi gallery-dl yang terpasang. Fitur ini tidak melakukan update.
- Archive gallery-dl berbeda dari session GUI.
- ZIP compression memakai library standar Python.
- Format 7z membutuhkan 7z/7za/7zz di PATH.
- Convert PNG to WebP membutuhkan Pillow.
"""
        return """GdlScrape Help Center
=====================

1) Core idea
------------
This GUI is a batch launcher for gallery-dl. It does not import gallery-dl as a Python library. Downloads still run through a gallery-dl subprocess.

Accepted input:
- One plain URL per line.
- One full gallery-dl command per line.
- TXT database.
- CSV database.
- XLSX database when openpyxl is installed.

2) TXT database format
----------------------
Use one URL or one full gallery-dl command per line.
Blank lines are ignored.
Lines starting with # are treated as a tag/group label for following rows.

Neutral example:
# Group A
https://example.com/user/123456
https://example.com/post/789012

# Full commands are allowed
gallery-dl -d "D:\\Rips\\Creator A" https://example.com/user/123456 --no-check-certificate
gallery-dl --destination "D:\\Rips\\Creator B" https://example.com/post/111
gallery-dl --destination="D:\\Rips\\Creator C" https://example.com/post/222

Notes:
- example.com is only a placeholder.
- Replace it with a real URL supported by gallery-dl.
- Quote destination folders that contain spaces.

3) CSV database format
----------------------
Recommended header:
url,destination,extra_args,enabled,tag,notes

Column meaning:
- url: plain URL or full gallery-dl command.
- destination: target folder. The GUI adds -d when url contains a plain URL.
- extra_args: extra gallery-dl args, for example --range 1-10.
- enabled: true/false. False rows are skipped during import.
- tag: group label, for example Creator A, Batch 01, Project X.
- notes: free user notes.

Supported aliases:
- url, URL, Link, link
- destination, Destination, Folder, folder
- extra_args, Extra Args, Args, args

Enabled values:
- true, yes, 1, on = imported.
- false, no, 0, off = skipped.

Example CSV:
url,destination,extra_args,enabled,tag,notes
https://example.com/post/123,D:\\Rips\\Creator A,--range 1-10,true,Creator A,first batch
https://example.com/post/456,D:\\Rips\\Creator B,,true,Creator B,no extra args
https://example.com/post/789,,,false,Skipped,this row will not be imported

If the CSV has no recognized header, the app tries to use the first column as URL/command fallback.

4) XLSX database format
-----------------------
Use the same columns as CSV in the first worksheet:
url | destination | extra_args | enabled | tag | notes

Rules:
- The first row must contain headers.
- Each next row is one database entry.
- If openpyxl is missing, install it with:
  pip install openpyxl

5) Full command format
----------------------
Full command lines are preserved. The GUI strips the leading gallery-dl executable and then runs the remaining arguments through the selected gallery-dl command.

Examples:
gallery-dl -d "D:\\Rips\\Creator A" https://example.com/post/123
gallery-dl --destination "D:\\Rips\\Creator B" https://example.com/post/456
gallery-dl --destination="D:\\Rips\\Creator C" https://example.com/post/789
python -m gallery_dl -d "D:\\Rips\\Creator D" https://example.com/post/999

6) Output folder rule
---------------------
The GUI field named Output dir is the GUI default output folder.
It is used only when a line does not already include:
- -d
- --destination
- --destination=...

Plain URL:
https://example.com/post/123

Runs approximately as:
gallery-dl -d ./downloads https://example.com/post/123

This command keeps its own destination:
gallery-dl -d "D:\\Rips\\Creator" https://example.com/post/123

7) Safe practical workflow
--------------------------
1. Click Help > Export Pack or Run > Templates.
2. Edit the CSV/XLSX/TXT template.
3. Import the database.
4. Use Import Preview for large files.
5. Use Command Preview before Start.
6. Run Health Check after changing gallery-dl path, config, or output folder.
7. Start with 1 to 3 workers first.
8. Use Retry Failed after fixing cookies, config, network, or path problems.

8) Notes
--------
- Check gallery-dl Version only checks the installed gallery-dl version. It does not update gallery-dl or the GUI.
- gallery-dl archive is separate from GUI session files.
- ZIP compression uses Python standard library.
- 7z/7za/7zz is required for external archive formats.
- Convert PNG to WebP requires Pillow.
"""

    def quick_guide_text(self, language: str | None = None) -> str:
        language = language or getattr(self, "help_language", "English")
        if language == "Indonesia":
            return """Panduan Singkat - GdlScrape

Format input:
1. URL biasa per baris:
   https://example.com/post/123

2. Command gallery-dl lengkap per baris:
   gallery-dl -d "D:\\Rips\\Creator" https://example.com/post/123

3. Database TXT:
   # Group A
   https://example.com/post/123
   gallery-dl --destination "D:\\Rips\\Creator A" https://example.com/post/456

4. Kolom CSV/XLSX:
   url,destination,extra_args,enabled,tag,notes

Output dir:
- Output dir adalah default output dari GUI.
- Dipakai hanya kalau command belum punya -d/--destination.
- Kalau command sudah punya -d atau --destination, folder dari command user tetap dipakai.

Cookies browser:
- Pilih browser di Run Config untuk menambah --cookies-from-browser otomatis.
- Pakai "none" kalau situs tidak butuh login.
- Tutup browser dulu bila ekstraksi cookies gagal (file terkunci).

Kontrol download:
- Workers: jumlah download paralel. Mulai 2-3, naikkan hati-hati agar tidak kena rate-limit.
- Retries: 0 = command tidak diubah. >0 menambah --retries.
- Pause: berhenti mengambil job baru
job aktif tetap selesai. Tekan lagi untuk lanjut.
- Stop: hentikan seluruh batch dan matikan proses aktif dengan aman.
- Cancel Row: batalkan hanya baris terpilih
batch lain tetap jalan.
- Retry Failed: ulang job gagal/dihentikan/dibatalkan sesuai Retry Strategy.

Troubleshooting umum:
- "gallery-dl not found": install gallery-dl, lalu set path di System.
- 401/403/login: pakai cookies browser atau cookies.txt.
- 429/too many requests: kurangi workers, naikkan sleep/retries.
- Timeout/connection: cek jaringan, coba Retry Failed.
- Folder/permission error: pastikan output dir ada dan bisa ditulis.

Workflow disarankan:
Export Template Pack > Edit template > Import Preview > Import database > Command Preview > Health Check > Start.
"""
        return """Quick Guide - GdlScrape

Input formats:
1. Plain URL per line:
   https://example.com/post/123

2. Full gallery-dl command per line:
   gallery-dl -d "D:\\Rips\\Creator" https://example.com/post/123

3. TXT database:
   # Group A
   https://example.com/post/123
   gallery-dl --destination "D:\\Rips\\Creator A" https://example.com/post/456

4. CSV/XLSX columns:
   url,destination,extra_args,enabled,tag,notes

Output dir:
- Output dir is the GUI default output folder.
- It is used only when the command has no -d/--destination.
- If a command already has -d or --destination, the user's command folder stays unchanged.

Browser cookies:
- Pick a browser in Run Config to add --cookies-from-browser automatically.
- Use "none" when the site needs no login.
- Close the browser first if cookie extraction fails (the cookie file can be locked).

Download controls:
- Workers: parallel downloads. Start with 2-3 and raise carefully to avoid rate limits.
- Retries: 0 leaves each command unchanged
>0 adds --retries.
- Pause: stops pulling new jobs
active jobs finish. Press again to resume.
- Stop: halts the whole batch and safely terminates active processes.
- Cancel Row: cancels only the selected row
the rest of the batch keeps running.
- Retry Failed: re-runs failed/stopped/cancelled jobs per the Retry Strategy.

Common troubleshooting:
- "gallery-dl not found": install gallery-dl, then set its path under System.
- 401/403/login: use browser cookies or a cookies.txt file.
- 429/too many requests: lower workers, raise sleep/retries.
- Timeout/connection: check the network, then Retry Failed.
- Folder/permission errors: make sure the output dir exists and is writable.

Recommended workflow:
Export Template Pack > Edit template > Import Preview > Import database > Command Preview > Health Check > Start.
"""

    def open_help_center(self) -> None:
        language = getattr(self, "help_language", "English")
        dlg = QDialog(self)
        dlg.setWindowTitle("Help Center")
        dlg.resize(760, 560)
        dlg.setMinimumSize(640, 430)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(7)

        title = QLabel("Help Center")
        title.setStyleSheet("font-size: 18px; font-weight: 900;")
        lay.addWidget(title)

        subtitle_text = (
            "Format database TXT/CSV/XLSX, full command, aturan output folder, dan workflow aman."
            if language == "Indonesia"
            else "TXT/CSV/XLSX database format, full commands, output folder rules, and safe workflow."
        )
        subtitle = QLabel(subtitle_text)
        subtitle.setObjectName("subtle")
        subtitle.setWordWrap(True)
        lay.addWidget(subtitle)

        help_text = QPlainTextEdit()
        help_text.setReadOnly(True)
        help_text.setMaximumBlockCount(5000)
        help_text.setPlainText(self.database_help_text(language))
        lay.addWidget(help_text, 1)

        btn_row = QHBoxLayout()
        btn_copy = QPushButton("Copy Guide")
        btn_txt = QPushButton("Export TXT")
        btn_csv = QPushButton("Export CSV")
        btn_xlsx = QPushButton("Export XLSX")
        btn_pack = QPushButton("Export Pack")
        btn_close = QPushButton("Close")
        for b in (btn_copy, btn_txt, btn_csv, btn_xlsx, btn_pack, btn_close):
            b.setMinimumHeight(28)
            b.setMinimumWidth(88)
        btn_copy.clicked.connect(self.copy_quick_guide)
        btn_txt.clicked.connect(self.export_txt_template)
        btn_csv.clicked.connect(self.export_csv_template)
        btn_xlsx.clicked.connect(self.export_xlsx_template)
        btn_pack.clicked.connect(self.export_template_pack)
        btn_close.clicked.connect(dlg.accept)
        btn_row.addWidget(btn_copy)
        btn_row.addWidget(btn_txt)
        btn_row.addWidget(btn_csv)
        btn_row.addWidget(btn_xlsx)
        btn_row.addWidget(btn_pack)
        btn_row.addStretch(1)
        btn_row.addWidget(btn_close)
        lay.addLayout(btn_row)
        dlg.exec()

    def sample_csv_rows(self) -> list[list[str]]:
        """Header + example rows for CSV/XLSX database templates.

        Column names match the importer (build_raw_command_from_columns /
        _row_value) so an exported template round-trips cleanly on re-import.
        Six columns (A-F) align with the width map used in export_xlsx_template.
        All values are neutral placeholders.
        """
        return [
            ["URL", "Destination", "Tag", "Enabled", "Extra Args", "Command"],
            ["https://example.com/user/artist-one", "downloads/ArtistOne", "ArtistOne", "yes", "", ""],
            ["https://example.com/user/artist-two", "downloads/ArtistTwo", "ArtistTwo", "yes", "--no-check-certificate", ""],
            ["https://example.com/user/disabled-row", "", "ArtistThree", "no", "", ""],
            ["", "", "Full command example", "yes", "", 'gallery-dl -d "downloads/ArtistFour" https://example.com/user/artist-four'],
        ]

    def sample_txt_database(self) -> str:
        """Example .txt database showing the supported plain-text formats:
        '#tag' section headers, bare URLs, and full gallery-dl commands.
        All values are neutral placeholders.
        """
        return (
            "# Example gallery-dl database (plain text)\n"
            "# Lines starting with '#' set a tag for the rows that follow.\n"
            "# Each row may be a bare URL or a full gallery-dl command.\n"
            "# Blank lines are ignored.\n"
            "\n"
            "#ArtistOne\n"
            "https://example.com/user/artist-one\n"
            'gallery-dl -d "downloads/ArtistOne" https://example.com/gallery/artist-one\n'
            "\n"
            "#ArtistTwo\n"
            'gallery-dl -d "downloads/ArtistTwo" https://example.com/user/artist-two --no-check-certificate\n'
            'gallery-dl -d "downloads/ArtistTwo" https://example.com/gallery/artist-two\n'
            "\n"
            "#ArtistThree\n"
            "https://example.com/user/artist-three\n"
        )

    def export_txt_template(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export TXT example database", str(APP_DIR / "gallery_dl_database_example.txt"), "Text (*.txt)")
        if not path:
            return
        try:
            atomic_write_text(path, self.sample_txt_database(), encoding="utf-8")
            self.show_compact_message("TXT Example", f"TXT example exported:\n{path}", "info")
        except Exception as exc:
            self.show_compact_message("TXT export failed", str(exc), "error")

    def export_csv_template(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export CSV database template", str(APP_DIR / "gallery_dl_database_template.csv"), "CSV (*.csv)")
        if not path:
            return
        try:
            buffer = io.StringIO(newline="")
            writer = csv.writer(buffer)
            writer.writerows(self.sample_csv_rows())
            atomic_write_text(path, buffer.getvalue(), encoding="utf-8-sig", newline="")
            self.show_compact_message("CSV Template", f"CSV template exported:\n{path}", "info")
        except Exception as exc:
            self.show_compact_message("CSV export failed", str(exc), "error")

    @staticmethod
    def _xlsx_write_database_sheet(ws, rows) -> None:
        """Write the database rows so Excel never misreads them.

        Two problems are handled:
        1. A cell beginning with -, =, + or @ (e.g. "--no-check-certificate")
           is read by Excel as a formula and shows #NAME?. Forcing the data
           columns to Text number-format makes Excel treat them literally.
        2. The Text format is applied at COLUMN level (not by mixing it into the
           styled header cell), which keeps styles.xml simple and avoids the
           "we found a problem with some content" recovery prompt that some
           Excel builds raise for over-decorated cell styles.
        """
        from openpyxl.styles import Alignment, Font, PatternFill  # type: ignore

        for row in rows:
            ws.append(row)

        # Style only the header row (plain text, never formula-like).
        header_fill = PatternFill("solid", fgColor="1F4E78")
        header_font = Font(color="FFFFFF", bold=True)
        for cell in ws[1]:
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center")

        # Force every data cell (row 2+) to Text so leading dashes are safe.
        for row in ws.iter_rows(min_row=2):
            for cell in row:
                cell.number_format = "@"

        widths = {"A": 62, "B": 32, "C": 22, "D": 12, "E": 18, "F": 42}
        for col, width in widths.items():
            ws.column_dimensions[col].width = width
        ws.freeze_panes = "A2"

    @staticmethod
    def _xlsx_save_atomic(wb, path) -> None:
        """Save a workbook atomically.

        openpyxl writes incrementally
        if the process is interrupted mid-save
        (or the target is locked by Excel) the destination can be left as a
        truncated, corrupt file that Excel then offers to "recover". Writing to
        a temporary file in the same folder and replacing the target only after
        a successful save guarantees the destination is always a complete file.
        """
        target = Path(path)
        tmp = target.with_name(f"{target.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            wb.save(tmp)
            os.replace(tmp, target)
        except Exception:
            try:
                if tmp.exists():
                    tmp.unlink()
            except Exception:
                pass
            raise

    def export_xlsx_template(self) -> None:
        try:
            import openpyxl  # type: ignore
        except Exception:
            self.show_compact_message("XLSX Template", "openpyxl is required for XLSX export.\n\nInstall with:\npip install openpyxl", "warning")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export XLSX database template", str(APP_DIR / "gallery_dl_database_template.xlsx"), "Excel (*.xlsx)")
        if not path:
            return
        wb = None
        try:
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "database"
            self._xlsx_write_database_sheet(ws, self.sample_csv_rows())
            help_ws = wb.create_sheet("README_EN")
            for line in self.database_help_text("English").splitlines():
                help_ws.append([line])
            help_ws.column_dimensions["A"].width = 110
            help_id = wb.create_sheet("README_ID")
            for line in self.database_help_text("Indonesia").splitlines():
                help_id.append([line])
            help_id.column_dimensions["A"].width = 110
            self._xlsx_save_atomic(wb, path)
            self.show_compact_message("XLSX Template", f"XLSX template exported:\n{path}", "info")
        except Exception as exc:
            self.show_compact_message("XLSX export failed", str(exc), "error")
        finally:
            if wb is not None:
                try:
                    wb.close()
                except Exception:
                    pass

    def export_template_pack(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose folder for database template pack", str(APP_DIR))
        if not folder:
            return
        base = Path(folder)
        try:
            base.mkdir(parents=True, exist_ok=True)
            txt_path = base / "gallery_dl_database_example.txt"
            csv_path = base / "gallery_dl_database_template.csv"
            readme_en = base / "README_database_format_EN.md"
            readme_id = base / "README_database_format_ID.md"
            atomic_write_text(txt_path, self.sample_txt_database(), encoding="utf-8")
            csv_buffer = io.StringIO(newline="")
            writer = csv.writer(csv_buffer)
            writer.writerows(self.sample_csv_rows())
            atomic_write_text(csv_path, csv_buffer.getvalue(), encoding="utf-8-sig", newline="")
            atomic_write_text(readme_en, self.database_help_text("English"), encoding="utf-8")
            atomic_write_text(readme_id, self.database_help_text("Indonesia"), encoding="utf-8")

            xlsx_msg = ""
            try:
                import openpyxl  # type: ignore
            except Exception:
                openpyxl = None  # type: ignore
            if openpyxl is None:
                xlsx_msg = "\n- XLSX skipped because openpyxl is not installed"
            else:
                wb = None
                try:
                    wb = openpyxl.Workbook()
                    ws = wb.active
                    ws.title = "database"
                    self._xlsx_write_database_sheet(ws, self.sample_csv_rows())
                    help_ws = wb.create_sheet("README_EN")
                    for line in self.database_help_text("English").splitlines():
                        help_ws.append([line])
                    help_ws.column_dimensions["A"].width = 110
                    help_id = wb.create_sheet("README_ID")
                    for line in self.database_help_text("Indonesia").splitlines():
                        help_id.append([line])
                    help_id.column_dimensions["A"].width = 110
                    xlsx_path = base / "gallery_dl_database_template.xlsx"
                    self._xlsx_save_atomic(wb, xlsx_path)
                    xlsx_msg = f"\n- {xlsx_path.name}"
                except Exception as exc:
                    xlsx_msg = f"\n- XLSX skipped (export error: {exc})"
                finally:
                    if wb is not None:
                        try:
                            wb.close()
                        except Exception:
                            pass

            self.show_compact_message(
                "Template Pack",
                "Template pack exported:\n"
                f"- {txt_path.name}\n"
                f"- {csv_path.name}\n"
                f"- {readme_en.name}\n"
                f"- {readme_id.name}"
                f"{xlsx_msg}\n\nFolder:\n{base}",
                "info",
            )
        except Exception as exc:
            self.show_compact_message("Template pack export failed", str(exc), "error")

    def copy_quick_guide(self) -> None:
        QApplication.clipboard().setText(self.quick_guide_text())
        self.show_compact_message("Quick Guide", "Quick guide copied to clipboard.", "info")

    def _show_feature_page(self, index: int) -> None:
        if hasattr(self, "feature_stack"):
            self.feature_stack.setCurrentIndex(index)
        for i, btn in enumerate(getattr(self, "feature_nav_buttons", [])):
            btn.setChecked(i == index)

    @Slot()
    def open_feature_hub(self) -> None:
        self.feature_dialog.show()
        self.feature_dialog.raise_()
        self.feature_dialog.activateWindow()


__all__ = ['UiShellMixin']
