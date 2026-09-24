"""Cyber-dashboard main surface inspired by the supplied visual reference."""

from __future__ import annotations

import time

from PySide6.QtCore import QPointF, QSize, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPalette, QPen, QPolygonF
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
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
    QStyle,
    QTableWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)


from .core import APP_VERSION
from .themes import action_icon, application_icon


class ActivityGraph(QWidget):
    """Small neon activity trace; intentionally data-light and inexpensive."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(46)
        self.setMaximumHeight(58)
        self._samples = [0.08] * 48

    def push(self, value: float) -> None:
        value = max(0.04, min(1.0, float(value)))
        if abs(self._samples[-1] - value) < 0.005:
            return
        self._samples = [*self._samples[1:], value]
        self.update()

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(1, 5, -1, -5)

        light = self.palette().color(QPalette.Window).lightness() >= 160
        trace = QColor("#087f7b" if light else "#32e6d1")
        glow = QColor(trace)
        glow.setAlpha(54 if light else 72)
        fade = QColor(trace)
        fade.setAlpha(0)
        endpoint = QColor("#176e70" if light else "#c8fff7")

        gradient = QLinearGradient(0, 0, 0, rect.height())
        gradient.setColorAt(0.0, glow)
        gradient.setColorAt(1.0, fade)

        width = max(1, rect.width())
        height = max(1, rect.height())
        points = QPolygonF()
        for index, sample in enumerate(self._samples):
            x = rect.left() + (index / max(1, len(self._samples) - 1)) * width
            y = rect.bottom() - sample * height
            points.append(QPointF(x, y))

        fill = QPolygonF([QPointF(rect.left(), rect.bottom()), *points, QPointF(rect.right(), rect.bottom())])
        painter.setPen(Qt.NoPen)
        painter.setBrush(gradient)
        painter.drawPolygon(fill)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(trace, 2.0))
        painter.drawPolyline(points)
        painter.setPen(QPen(endpoint, 1.0))
        painter.setBrush(trace)
        painter.drawEllipse(points[-1], 3.2, 3.2)


class DashboardUiMixin:
    """Override the legacy main layout while reusing its feature methods."""

    @staticmethod
    def _describe(widget: QWidget, text: str) -> None:
        """Expose one concise explanation through hover, status, and a11y."""
        widget.setToolTip(text)
        widget.setStatusTip(text)
        widget.setAccessibleDescription(text)

    def _section_label(self, text: str) -> QLabel:
        label = QLabel(text.upper())
        label.setObjectName("eyebrow")
        return label

    def _set_standard_icon(self, button: QPushButton, icon: QStyle.StandardPixmap) -> None:
        """Use bundled vector drawings instead of platform dependent native icons."""
        names = {
            QStyle.SP_DialogOpenButton: "open",
            QStyle.SP_DialogApplyButton: "paste",
            QStyle.SP_TrashIcon: "trash",
            QStyle.SP_DirOpenIcon: "folder",
            QStyle.SP_ArrowDown: "download",
            QStyle.SP_MediaPause: "pause",
            QStyle.SP_DialogCancelButton: "cancel",
            QStyle.SP_BrowserReload: "retry",
            QStyle.SP_MediaStop: "stop",
        }
        button.setProperty("actionIcon", names[icon])
        button.setIcon(action_icon(names[icon], dark=self.current_theme == "dark"))
        button.setIconSize(QSize(16, 16))

    def _refresh_action_icons(self) -> None:
        for button in self.findChildren(QPushButton):
            name = button.property("actionIcon")
            if name:
                button.setIcon(action_icon(str(name), dark=self.current_theme == "dark"))

    def _metric(self, title: str, value: str, accent: str = "cyan") -> tuple[QFrame, QLabel]:
        frame = QFrame()
        frame.setObjectName("metricBlock")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(1)
        caption = QLabel(title.upper())
        caption.setObjectName("metricCaption")
        number = QLabel(value)
        number.setObjectName("metricValuePurple" if accent == "purple" else "metricValue")
        layout.addWidget(caption)
        layout.addWidget(number)
        return frame, number

    def _build_ui(self) -> None:
        central = QWidget()
        central.setObjectName("appRoot")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Header ---------------------------------------------------------
        header = QFrame()
        header.setObjectName("topBar")
        header.setFixedHeight(78)
        header_l = QHBoxLayout(header)
        header_l.setContentsMargins(24, 12, 26, 10)
        header_l.setSpacing(12)

        self.lbl_brand_mark = QLabel()
        self.lbl_brand_mark.setObjectName("brandMark")
        self.lbl_brand_mark.setFixedWidth(38)
        self.lbl_brand_mark.setAlignment(Qt.AlignCenter)
        self.lbl_brand_mark.setPixmap(
            application_icon(dark=self.current_theme == "dark").pixmap(30, 30)
        )
        self.lbl_brand_mark.setAccessibleName("GdlScrape logo")
        brand = QVBoxLayout()
        brand.setSpacing(0)
        brand_row = QHBoxLayout()
        brand_row.setSpacing(7)
        self.lbl_title = QLabel("GDL")
        self.lbl_title.setObjectName("brandSoft")
        self.lbl_subtitle = QLabel("SCRAPE")
        self.lbl_subtitle.setObjectName("brandStrong")
        brand_row.addWidget(self.lbl_title)
        brand_row.addWidget(self.lbl_subtitle)
        brand_row.addStretch(1)
        self.lbl_brand_tagline = QLabel("GALLERY-DL  •  BATCH PIPELINE")
        self.lbl_brand_tagline.setObjectName("brandTagline")
        brand.addLayout(brand_row)
        brand.addWidget(self.lbl_brand_tagline)

        self.lbl_language = QLabel("LANG")
        self.lbl_language.setObjectName("eyebrow")
        self.combo_help_language = QComboBox()
        self.combo_help_language.setObjectName("headerCombo")
        self.combo_help_language.addItems(["English", "Indonesia"])
        self.combo_help_language.setCurrentText(getattr(self, "help_language", "English"))
        self.combo_help_language.setFixedWidth(108)
        self.combo_help_language.currentTextChanged.connect(self.set_help_language)
        self.lbl_theme = QLabel("THEME")
        self.lbl_theme.setObjectName("eyebrow")
        self.combo_theme = QComboBox()
        self.combo_theme.setObjectName("headerCombo")
        self.combo_theme.addItem("Dark", "dark")
        self.combo_theme.addItem("Light", "light")
        theme_index = self.combo_theme.findData(getattr(self, "current_theme", "dark"))
        self.combo_theme.setCurrentIndex(max(0, theme_index))
        self.combo_theme.setFixedWidth(84)
        self.combo_theme.currentIndexChanged.connect(
            lambda index: self.set_theme(str(self.combo_theme.itemData(index) or "dark"))
        )
        self.lbl_ready = QLabel("READY")
        self.lbl_ready.setObjectName("statusPill")
        self.lbl_app_version = QLabel(f"v{APP_VERSION}  •  DESKTOP")
        self.lbl_app_version.setObjectName("versionLabel")

        header_l.addWidget(self.lbl_brand_mark)
        header_l.addLayout(brand)
        header_l.addStretch(1)
        header_l.addWidget(self.lbl_theme)
        header_l.addWidget(self.combo_theme)
        header_l.addWidget(self.lbl_language)
        header_l.addWidget(self.combo_help_language)
        header_l.addWidget(self.lbl_ready)
        header_l.addWidget(self.lbl_app_version)
        root.addWidget(header)

        accent_line = QFrame()
        accent_line.setObjectName("accentLine")
        accent_line.setFixedHeight(2)
        root.addWidget(accent_line)

        body = QSplitter(Qt.Horizontal)
        body.setObjectName("dashboardSplitter")
        body.setChildrenCollapsible(False)
        root.addWidget(body, 1)

        # Left command rail ---------------------------------------------
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.NoFrame)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        left_scroll.setMinimumWidth(310)
        left_scroll.setMaximumWidth(390)
        self.side_panel = QFrame()
        self.side_panel.setObjectName("leftRail")
        self.side_panel.setMinimumWidth(310)
        self.side_panel.setMaximumWidth(390)
        left_scroll.setWidget(self.side_panel)
        rail = QVBoxLayout(self.side_panel)
        rail.setContentsMargins(18, 18, 18, 18)
        rail.setSpacing(12)

        links_card = QFrame()
        links_card.setObjectName("panel")
        links_l = QVBoxLayout(links_card)
        links_l.setContentsMargins(14, 12, 14, 14)
        links_l.setSpacing(8)
        links_head = QHBoxLayout()
        links_head.addWidget(self._section_label("Links / Commands"))
        links_head.addStretch(1)
        self.lbl_entry_count = QLabel("0 LINKS")
        self.lbl_entry_count.setObjectName("countBadge")
        links_head.addWidget(self.lbl_entry_count)
        links_l.addLayout(links_head)
        self.txt_commands = QPlainTextEdit()
        self.txt_commands.setObjectName("linkEditor")
        self.txt_commands.setPlaceholderText("Paste URL or full gallery-dl command\nOne entry per line…")
        self.txt_commands.setMinimumHeight(190)
        self.txt_commands.setMaximumBlockCount(20000)
        self.txt_commands.textChanged.connect(self.on_text_changed)
        links_l.addWidget(self.txt_commands)
        input_buttons = QGridLayout()
        input_buttons.setHorizontalSpacing(6)
        input_buttons.setVerticalSpacing(6)
        self.btn_import = self._btn("Import", "Load .txt/.csv/.xlsx database")
        self.btn_paste = self._btn("Paste", "Paste commands from clipboard")
        self.btn_clear_input = self._btn("Clear", "Clear command input")
        self._set_standard_icon(self.btn_import, QStyle.SP_DialogOpenButton)
        self._set_standard_icon(self.btn_paste, QStyle.SP_DialogApplyButton)
        self._set_standard_icon(self.btn_clear_input, QStyle.SP_TrashIcon)
        self.btn_import.clicked.connect(self.import_file)
        self.btn_paste.clicked.connect(self.paste_clipboard)
        self.btn_clear_input.clicked.connect(self.clear_input)
        input_buttons.addWidget(self.btn_import, 0, 0)
        input_buttons.addWidget(self.btn_paste, 0, 1)
        input_buttons.addWidget(self.btn_clear_input, 1, 0, 1, 2)
        links_l.addLayout(input_buttons)
        self.lbl_loaded_file = QLabel("No file loaded")
        self.lbl_loaded_file.setObjectName("subtle")
        self.lbl_loaded_file.setWordWrap(True)
        links_l.addWidget(self.lbl_loaded_file)
        rail.addWidget(links_card)

        destination = QFrame()
        destination.setObjectName("panel")
        dest_l = QVBoxLayout(destination)
        dest_l.setContentsMargins(14, 12, 14, 14)
        dest_l.setSpacing(8)
        dest_l.addWidget(self._section_label("Destination"))
        out_row = QHBoxLayout()
        self.edit_output = QLineEdit("./downloads")
        self.edit_output.setToolTip("Fallback output when a command has no destination.")
        self.btn_output = self._btn("", "Choose output directory", "iconButton")
        self.btn_output.setAccessibleName("Choose output directory")
        self.btn_output.setMinimumWidth(0)
        self.btn_output.setFixedWidth(38)
        self._set_standard_icon(self.btn_output, QStyle.SP_DirOpenIcon)
        self.btn_output.clicked.connect(self.choose_output_dir)
        out_row.addWidget(self.edit_output, 1)
        out_row.addWidget(self.btn_output)
        dest_l.addLayout(out_row)
        self.lbl_destination_hint = QLabel("A command's custom destination overrides this default folder.")
        self.lbl_destination_hint.setObjectName("subtle")
        self.lbl_destination_hint.setWordWrap(True)
        dest_l.addWidget(self.lbl_destination_hint)
        self.btn_open_output = QPushButton("Open output folder")
        self.btn_open_output.setObjectName("ghost")
        self.btn_open_output.clicked.connect(self.open_output_folder)
        dest_l.addWidget(self.btn_open_output)
        rail.addWidget(destination)

        options = QFrame()
        options.setObjectName("panel")
        options_l = QGridLayout(options)
        options_l.setContentsMargins(14, 12, 14, 14)
        options_l.setSpacing(7)
        options_l.addWidget(self._section_label("Common settings"), 0, 0, 1, 2)
        options_l.addWidget(QLabel("Workers"), 1, 0)
        options_l.addWidget(QLabel("Cookies"), 1, 1)
        self.spin_workers = QSpinBox()
        self.spin_workers.setRange(1, 32)
        self.spin_workers.setValue(3)
        self.spin_workers.valueChanged.connect(self._sync_worker_log_filters)
        self.spin_workers.setButtonSymbols(QAbstractSpinBox.UpDownArrows)
        self.combo_cookies = QComboBox()
        self.combo_cookies.addItems(["none", "chrome", "firefox", "edge", "brave", "chromium", "opera"])
        options_l.addWidget(self.spin_workers, 2, 0)
        options_l.addWidget(self.combo_cookies, 2, 1)
        options_l.addWidget(QLabel("Retries"), 3, 0)
        options_l.addWidget(QLabel("Delay"), 3, 1)
        self.spin_retries = QSpinBox()
        self.spin_retries.setRange(0, 20)
        self.spin_delay = QSpinBox()
        self.spin_delay.setRange(0, 1440)
        self.spin_delay.setSuffix(" min")
        options_l.addWidget(self.spin_retries, 4, 0)
        options_l.addWidget(self.spin_delay, 4, 1)
        self.chk_compress = QCheckBox("Compress")
        self.chk_compress.setToolTip("Create an archive after a successful download with an explicit destination.")
        self.combo_archive = QComboBox()
        self.combo_archive.addItems(["zip", "7z", "tar"])
        self.combo_archive.setToolTip("ZIP is built in. 7z and tar require 7-Zip in PATH.")
        self.chk_convert_webp = QCheckBox("PNG → WebP")
        self.chk_convert_webp.setToolTip("Convert PNG files after download. Requires Pillow.")
        options_l.addWidget(self.chk_compress, 5, 0)
        options_l.addWidget(self.combo_archive, 5, 1)
        options_l.addWidget(self.chk_convert_webp, 6, 0, 1, 2)
        rail.addWidget(options)

        controls = QFrame()
        controls.setObjectName("panel")
        control_l = QGridLayout(controls)
        control_l.setContentsMargins(14, 12, 14, 14)
        control_l.setSpacing(7)
        self.btn_start = QPushButton("DOWNLOAD")
        self.btn_start.setObjectName("primary")
        self.btn_pause = QPushButton("Pause")
        self.btn_cancel = QPushButton("Cancel Row")
        self.btn_retry = QPushButton("Retry")
        self.btn_stop = QPushButton("STOP")
        self.btn_stop.setObjectName("danger")
        self._set_standard_icon(self.btn_start, QStyle.SP_ArrowDown)
        self._set_standard_icon(self.btn_pause, QStyle.SP_MediaPause)
        self._set_standard_icon(self.btn_cancel, QStyle.SP_DialogCancelButton)
        self._set_standard_icon(self.btn_retry, QStyle.SP_BrowserReload)
        self._set_standard_icon(self.btn_stop, QStyle.SP_MediaStop)
        self.btn_start.clicked.connect(self.start_download)
        self.btn_pause.clicked.connect(self.toggle_pause)
        self.btn_cancel.clicked.connect(self.cancel_selected)
        self.btn_retry.clicked.connect(self.retry_failed)
        self.btn_stop.clicked.connect(self.stop_download)
        self.btn_pause.setEnabled(False)
        self.btn_cancel.setEnabled(False)
        self.btn_retry.setEnabled(False)
        self.btn_stop.setEnabled(False)
        control_l.addWidget(self.btn_start, 0, 0, 1, 2)
        control_l.addWidget(self.btn_pause, 1, 0)
        control_l.addWidget(self.btn_retry, 1, 1)
        control_l.addWidget(self.btn_cancel, 2, 0, 1, 2)
        control_l.addWidget(self.btn_stop, 3, 0, 1, 2)
        rail.addWidget(controls)
        rail.addStretch(1)
        self.lbl_feature_hint = QLabel("")
        self.lbl_feature_hint.hide()
        rail.addWidget(self.lbl_feature_hint)
        body.addWidget(left_scroll)

        # Right dashboard -----------------------------------------------
        dashboard = QWidget()
        dashboard.setObjectName("dashboard")
        dash = QVBoxLayout(dashboard)
        dash.setContentsMargins(14, 14, 18, 16)
        dash.setSpacing(11)

        metrics = QFrame()
        metrics.setObjectName("panel")
        metrics_l = QVBoxLayout(metrics)
        metrics_l.setContentsMargins(14, 10, 14, 8)
        metrics_l.setSpacing(4)
        metrics_row = QHBoxLayout()
        rate_frame, self.lbl_metric_rate = self._metric("Throughput", "0.0 jobs/min")
        done_frame, self.lbl_metric_completed = self._metric("Completed", "0 / 0")
        files_frame, self.lbl_metric_files = self._metric("Files", "0", "purple")
        eta_frame, self.lbl_metric_eta = self._metric("ETA", "—")
        metrics_row.addWidget(rate_frame, 2)
        metrics_row.addStretch(1)
        metrics_row.addWidget(done_frame)
        metrics_row.addWidget(files_frame)
        metrics_row.addWidget(eta_frame)
        metrics_l.addLayout(metrics_row)
        self.activity_graph = ActivityGraph()
        metrics_l.addWidget(self.activity_graph)
        dash.addWidget(metrics)

        pipeline = QFrame()
        pipeline.setObjectName("panel")
        pipeline_l = QGridLayout(pipeline)
        pipeline_l.setContentsMargins(14, 10, 14, 12)
        pipeline_l.setHorizontalSpacing(10)
        pipeline_l.setVerticalSpacing(6)
        pipeline_l.addWidget(self._section_label("Pipeline"), 0, 0)
        self.lbl_summary = QLabel("0/0 · Done 0 · Failed 0 · Stopped 0")
        self.lbl_summary.setObjectName("pipelineSummary")
        pipeline_l.addWidget(self.lbl_summary, 0, 1, 1, 2)
        pipeline_l.addWidget(QLabel("COMPLETION"), 1, 0)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        pipeline_l.addWidget(self.progress, 1, 1)
        self.lbl_eta = QLabel("ETA: —")
        self.lbl_eta.setObjectName("metric")
        pipeline_l.addWidget(self.lbl_eta, 1, 2)
        pipeline_l.addWidget(QLabel("ACTIVE WORKERS"), 2, 0)
        self.progress_active = QProgressBar()
        self.progress_active.setTextVisible(False)
        self.progress_active.setMaximum(1)
        pipeline_l.addWidget(self.progress_active, 2, 1)
        self.lbl_active_workers = QLabel("0 / 0")
        self.lbl_active_workers.setObjectName("subtle")
        pipeline_l.addWidget(self.lbl_active_workers, 2, 2)
        pipeline_l.setColumnStretch(1, 1)
        dash.addWidget(pipeline)

        transfer_panel = QFrame()
        transfer_panel.setObjectName("panel")
        transfer_l = QVBoxLayout(transfer_panel)
        transfer_l.setContentsMargins(10, 6, 10, 10)
        transfer_l.setSpacing(4)

        # Keep the queue as the visual focus. Only actions that make sense
        # while preparing a download stay in the main workspace.
        action_bar = QHBoxLayout()
        action_bar.setSpacing(6)
        self.btn_action_builder = self._btn("Composer", "Create downloads and reusable gallery-dl defaults in one place")
        self.btn_action_composer = self.btn_action_builder
        self.btn_action_config = self._btn("Config", "Build and save gallery-dl configuration")
        self.btn_action_preview = self._btn("Preview", "Preview final commands")
        self.btn_action_dedupe = self._btn("Dedupe", "Remove exact duplicate jobs")
        self.btn_action_manage = self._btn("Manage", "Open library, scheduler, accounts, options, and runtime tools")
        self.btn_action_help = self._btn("Help", "Open the help center")
        self.btn_action_builder.clicked.connect(self.open_download_composer)
        self.btn_action_config.clicked.connect(self.open_config_builder)
        self.btn_action_preview.clicked.connect(self.command_preview)
        self.btn_action_dedupe.clicked.connect(self.remove_exact_duplicates)
        self.btn_action_manage.clicked.connect(self.open_management_center)
        self.btn_action_help.clicked.connect(self.open_help_center)
        for button in (
            self.btn_action_builder,
            self.btn_action_config,
            self.btn_action_preview,
            self.btn_action_dedupe,
            self.btn_action_manage,
            self.btn_action_help,
        ):
            button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            action_bar.addWidget(button)
        transfer_l.addLayout(action_bar)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("transferTabs")
        transfer_l.addWidget(self.tabs, 1)

        queue_page = QWidget()
        queue_l = QVBoxLayout(queue_page)
        queue_l.setContentsMargins(4, 8, 4, 4)
        queue_top = QHBoxLayout()
        queue_top.addWidget(self._section_label("Transfers"))
        self.search_queue = QLineEdit()
        self.search_queue.setPlaceholderText("Filter transfers")
        self.search_queue.setMaximumWidth(260)
        self.search_queue.textChanged.connect(self.apply_queue_filter)
        self.combo_filter = QComboBox()
        self.combo_filter.addItems(["All", "Queued", "Running", "Done", "Failed", "Stopped", "Cancelled"])
        self.combo_filter.currentTextChanged.connect(self.apply_queue_filter)
        queue_top.addStretch(1)
        queue_top.addWidget(self.search_queue)
        queue_top.addWidget(self.combo_filter)
        queue_l.addLayout(queue_top)
        self.tbl_queue = QTableWidget(0, 7)
        self.tbl_queue.setHorizontalHeaderLabels(["#", "Service", "ID", "Destination", "Tag", "Status", "Files"])
        self.tbl_queue.verticalHeader().setVisible(False)
        self.tbl_queue.verticalHeader().setDefaultSectionSize(44)
        self.tbl_queue.setShowGrid(False)
        self.tbl_queue.setAlternatingRowColors(True)
        self.tbl_queue.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl_queue.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_queue.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tbl_queue.customContextMenuRequested.connect(self.show_queue_context_menu)
        # Keep long destinations readable at the minimum window width. A
        # horizontal scrollbar is clearer than squeezing this column to a few
        # pixels when the queue panel gets narrow.
        self.tbl_queue.horizontalHeader().setSectionResizeMode(self.COL_DEST, QHeaderView.Interactive)
        self.tbl_queue.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.tbl_queue.setColumnWidth(self.COL_NUM, 38)
        self.tbl_queue.setColumnWidth(self.COL_SERVICE, 100)
        self.tbl_queue.setColumnWidth(self.COL_ID, 125)
        self.tbl_queue.setColumnWidth(self.COL_DEST, 250)
        self.tbl_queue.setColumnWidth(self.COL_TAG, 95)
        self.tbl_queue.setColumnWidth(self.COL_STATUS, 92)
        self.tbl_queue.setColumnWidth(self.COL_STATS, 105)
        queue_l.addWidget(self.tbl_queue, 1)
        self.tab_queue = self.tabs.addTab(queue_page, "TRANSFERS")

        log_page = QWidget()
        log_l = QVBoxLayout(log_page)
        log_l.setContentsMargins(4, 8, 4, 4)
        log_top = QHBoxLayout()
        self.combo_log_filter = QComboBox()
        self.combo_log_filter.addItems(["All", "Error", "Warning", "Done", "Current worker lines"])
        self._sync_worker_log_filters()
        self.combo_log_filter.currentTextChanged.connect(self.refresh_log_view)
        self.lbl_log_counts = QLabel("Done 0   Failed 0")
        self.lbl_log_counts.setObjectName("metric")
        log_top.addWidget(self._section_label("Live log"))
        log_top.addStretch(1)
        log_top.addWidget(self.combo_log_filter)
        log_top.addWidget(self.lbl_log_counts)
        log_l.addLayout(log_top)
        self.log_all = QPlainTextEdit()
        self.log_all.setObjectName("liveLog")
        self.log_all.setReadOnly(True)
        self.log_all.setMaximumBlockCount(7000)
        log_l.addWidget(self.log_all, 1)
        self.tab_log = self.tabs.addTab(log_page, "LOG")

        worker_page = QWidget()
        worker_l = QVBoxLayout(worker_page)
        worker_l.setContentsMargins(4, 8, 4, 4)
        self.tbl_workers = QTableWidget(0, 3)
        self.tbl_workers.setHorizontalHeaderLabels(["Worker", "Status", "Current Link"])
        self.tbl_workers.verticalHeader().setVisible(False)
        self.tbl_workers.setShowGrid(False)
        self.tbl_workers.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_workers.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        worker_l.addWidget(self.tbl_workers)
        self.tab_workers = self.tabs.addTab(worker_page, "WORKERS")

        history_page = QWidget()
        history_l = QVBoxLayout(history_page)
        history_l.setContentsMargins(4, 8, 4, 4)
        hist_top = QHBoxLayout()
        self.btn_load_history = self._btn("Refresh", "Refresh job history")
        self.btn_clear_history = self._btn("Clear", "Clear saved job history")
        self.btn_load_history.clicked.connect(self.load_history_table)
        self.btn_clear_history.clicked.connect(self.clear_history)
        hist_top.addWidget(self._section_label("History"))
        hist_top.addStretch(1)
        hist_top.addWidget(self.btn_load_history)
        hist_top.addWidget(self.btn_clear_history)
        history_l.addLayout(hist_top)
        self.tbl_history = QTableWidget(0, 5)
        self.tbl_history.setHorizontalHeaderLabels(["Time", "Status", "Service", "ID", "URL"])
        self.tbl_history.verticalHeader().setVisible(False)
        self.tbl_history.setShowGrid(False)
        self.tbl_history.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_history.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        history_l.addWidget(self.tbl_history)
        self.tab_history = self.tabs.addTab(history_page, "HISTORY")
        dash.addWidget(transfer_panel, 1)
        body.addWidget(dashboard)
        body.setSizes([330, 910])
        body.setStretchFactor(0, 0)
        body.setStretchFactor(1, 1)

        self.apply_language_to_ui()
        self._update_header_compact()

    def _update_header_compact(self) -> None:
        compact = self.width() < 1100
        self.lbl_brand_tagline.setVisible(not compact)
        self.lbl_app_version.setVisible(not compact)

    def _apply_main_descriptions(self, ind: bool) -> None:
        descriptions = {
            self.combo_theme: "Pilih tampilan gelap atau terang." if ind else "Choose the dark or light appearance.",
            self.combo_help_language: "Pilih bahasa antarmuka dan bantuan." if ind else "Choose the interface and help language.",
            self.txt_commands: "Masukkan satu URL atau perintah gallery-dl per baris." if ind else "Enter one URL or gallery-dl command per line.",
            self.btn_import: "Impor daftar dari TXT, CSV, atau XLSX." if ind else "Import a list from TXT, CSV, or XLSX.",
            self.btn_paste: "Tempel URL atau perintah dari clipboard." if ind else "Paste URLs or commands from the clipboard.",
            self.btn_clear_input: "Kosongkan seluruh daftar input." if ind else "Clear the entire input list.",
            self.edit_output: "Folder tujuan cadangan jika perintah tidak menentukan tujuan." if ind else "Fallback folder when a command has no destination.",
            self.btn_output: "Pilih folder hasil unduhan." if ind else "Choose the download output folder.",
            self.btn_open_output: "Buka folder hasil di File Explorer." if ind else "Open the output folder in File Explorer.",
            self.spin_workers: "Jumlah unduhan yang boleh berjalan bersamaan." if ind else "Number of downloads allowed to run at once.",
            self.combo_cookies: "Gunakan cookie login dari browser pilihan." if ind else "Use login cookies from the selected browser.",
            self.spin_retries: "Jumlah percobaan ulang ketika unduhan gagal." if ind else "Number of retries when a download fails.",
            self.spin_delay: "Tunda dimulainya antrean dalam hitungan menit." if ind else "Delay the queue start by this many minutes.",
            self.chk_compress: "Buat arsip setelah unduhan berhasil." if ind else "Create an archive after a successful download.",
            self.combo_archive: "Pilih format arsip hasil kompresi." if ind else "Choose the archive format used for compression.",
            self.chk_convert_webp: "Konversi gambar PNG menjadi WebP setelah selesai." if ind else "Convert PNG images to WebP after completion.",
            self.btn_start: "Mulai memproses seluruh antrean." if ind else "Start processing the complete queue.",
            self.btn_pause: "Jeda antrean setelah pekerjaan aktif selesai." if ind else "Pause the queue after active jobs finish.",
            self.btn_retry: "Jalankan kembali semua pekerjaan yang gagal." if ind else "Run all failed jobs again.",
            self.btn_cancel: "Batalkan baris antrean yang sedang dipilih." if ind else "Cancel the currently selected queue row.",
            self.btn_stop: "Hentikan seluruh proses unduhan." if ind else "Stop every running download.",
            self.btn_action_builder: "Susun job, default config, dan preset situs di satu tempat." if ind else "Compose jobs, config defaults, and site presets in one place.",
            self.btn_action_config: "Buat dan simpan config gallery-dl." if ind else "Build and save gallery-dl configuration.",
            self.btn_action_preview: "Periksa perintah final sebelum dijalankan." if ind else "Inspect final commands before running them.",
            self.btn_action_dedupe: "Hapus entri yang benar-benar sama dari antrean." if ind else "Remove exact duplicate entries from the queue.",
            self.btn_action_manage: "Kelola library, jadwal, akun, opsi, dan runtime." if ind else "Manage the library, schedules, accounts, options, and runtime.",
            self.btn_action_help: "Buka petunjuk penggunaan aplikasi." if ind else "Open the application usage guide.",
            self.search_queue: "Cari transfer berdasarkan isi tabel." if ind else "Search transfers using table contents.",
            self.combo_filter: "Tampilkan transfer dengan status tertentu." if ind else "Show transfers with a specific status.",
            self.combo_log_filter: "Saring pesan log berdasarkan jenisnya." if ind else "Filter log messages by type.",
            self.btn_load_history: "Muat ulang riwayat pekerjaan tersimpan." if ind else "Reload the saved job history.",
            self.btn_clear_history: "Hapus seluruh riwayat pekerjaan tersimpan." if ind else "Clear all saved job history.",
        }
        for widget, description in descriptions.items():
            self._describe(widget, description)

        tab_descriptions = [
            "Pantau seluruh antrean unduhan." if ind else "Monitor the complete download queue.",
            "Lihat keluaran proses secara langsung." if ind else "View live process output.",
            "Pantau pekerjaan setiap worker." if ind else "Monitor each worker's current job.",
            "Lihat hasil pekerjaan sebelumnya." if ind else "Review previously completed jobs.",
        ]
        for tab_index, description in enumerate(tab_descriptions):
            self.tabs.setTabToolTip(tab_index, description)

    def apply_language_to_ui(self) -> None:
        super().apply_language_to_ui()
        ind = self._ui_is_indonesian()
        self.btn_start.setText("UNDUH" if ind else "DOWNLOAD")
        self.btn_stop.setText("HENTIKAN" if ind else "STOP")
        labels = [
            (self.tab_queue, "TRANSFER"),
            (self.tab_log, "LOG"),
            (self.tab_workers, "WORKERS" if not ind else "WORKER"),
            (self.tab_history, "HISTORY" if not ind else "RIWAYAT"),
        ]
        for index, text in labels:
            self.tabs.setTabText(index, text)
        if hasattr(self, "btn_action_builder"):
            self.btn_action_builder.setText("RANCANG" if ind else "COMPOSER")
            self.btn_action_config.setText("CONFIG")
            self.btn_action_preview.setText("PRATINJAU" if ind else "PREVIEW")
            self.btn_action_dedupe.setText("DUPLIKAT" if ind else "DEDUPE")
            self.btn_action_manage.setText("KELOLA" if ind else "MANAGE")
            self.btn_action_help.setText("BANTUAN" if ind else "HELP")
            self.chk_compress.setText("Kompres" if ind else "Compress")
            self.chk_convert_webp.setText("PNG → WebP")
            self.lbl_theme.setText("TEMA" if ind else "THEME")
            self.lbl_destination_hint.setText(
                "Tujuan khusus pada command/database menggantikan folder default ini."
                if ind else "A command or database row's custom destination overrides this default folder."
            )
            self.combo_theme.setItemText(0, "Gelap" if ind else "Dark")
            self.combo_theme.setItemText(1, "Terang" if ind else "Light")
            self._apply_main_descriptions(ind)
            self._sync_worker_log_filters()

    def _update_counts(self) -> None:
        super()._update_counts()
        total = len(self.jobs)
        done = len(self.done_indices)
        files = sum(result.downloaded for result in self.results.values())
        elapsed = max(0.0, time.time() - self.started_at) if self.started_at else 0.0
        rate = (self.processed_run / elapsed * 60.0) if elapsed > 0 else 0.0
        self.lbl_metric_rate.setText(f"{rate:.1f} jobs/min")
        self.lbl_metric_completed.setText(f"{done} / {total}")
        self.lbl_metric_files.setText(str(files))
        self.lbl_entry_count.setText(f"{total} {'LINK' if total == 1 else 'LINKS'}")
        worker_capacity = max(1, self.spin_workers.value())
        active = len(self.active_job_indices)
        self.progress_active.setMaximum(worker_capacity)
        self.progress_active.setValue(active)
        self.lbl_active_workers.setText(f"{active} / {worker_capacity}")
        level = self.processed_run / max(1, self.total_run)
        if self.active_workers > 0:
            level = max(level, active / worker_capacity * 0.65)
        self.activity_graph.push(level)

    def _sync_worker_log_filters(self, *_args) -> None:
        if not hasattr(self, "combo_log_filter"):
            return
        combo = self.combo_log_filter
        selected = combo.currentText()
        base_count = 5
        combo.blockSignals(True)
        while combo.count() > base_count:
            combo.removeItem(combo.count() - 1)
        for worker_number in range(1, self.spin_workers.value() + 1):
            combo.addItem(f"Worker {worker_number}")
        index = combo.findText(selected)
        combo.setCurrentIndex(index if index >= 0 else 0)
        combo.blockSignals(False)
        self.refresh_log_view()

    def _refresh_eta(self) -> None:
        super()._refresh_eta()
        text = self.lbl_eta.text().replace("ETA:", "").replace("Sisa waktu:", "").strip()
        self.lbl_metric_eta.setText(text or "—")


__all__ = ["ActivityGraph", "DashboardUiMixin"]
