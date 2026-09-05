"""Settings page: Dark/Light, Bahasa Indonesia/English, output & temp
folders, GPU detection, RAM/CPU usage, Whisper model size, default clip
duration -- matching the Settings section of the spec.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QLineEdit, QFileDialog, QFrame, QCheckBox,
)
from PySide6.QtCore import Signal, QTimer

from config.i18n import t
from config.settings import VALID_WHISPER_MODELS
from utils.system_info import detect_nvidia_gpu, detect_ffmpeg_hw_encoders, get_resource_usage


class SettingsRow(QFrame):
    def __init__(self, label: str, widget: QWidget, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 6)
        lbl = QLabel(label)
        lbl.setFixedWidth(190)
        layout.addWidget(lbl)
        layout.addWidget(widget, 1)


class SettingsPage(QWidget):
    theme_changed = Signal(str)
    language_changed = Signal(str)

    def __init__(self, language: str, settings, parent=None):
        super().__init__(parent)
        self.language = language
        self.settings = settings
        self._build_ui()
        self._refresh_system_info()

        self._resource_timer = QTimer(self)
        self._resource_timer.timeout.connect(self._refresh_resource_usage)
        self._resource_timer.start(3000)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(10)

        title = QLabel(t("settings.title", self.language))
        title.setProperty("role", "title")
        root.addWidget(title)

        # --- Appearance ---
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["dark", "light"])
        self.theme_combo.setCurrentText(self.settings.theme)
        self.theme_combo.currentTextChanged.connect(self._on_theme_changed)
        root.addWidget(SettingsRow(t("settings.theme", self.language), self.theme_combo))

        self.lang_combo = QComboBox()
        self.lang_combo.addItem("Bahasa Indonesia", "id")
        self.lang_combo.addItem("English", "en")
        self.lang_combo.setCurrentIndex(0 if self.settings.language == "id" else 1)
        self.lang_combo.currentIndexChanged.connect(self._on_language_changed)
        root.addWidget(SettingsRow(t("settings.language", self.language), self.lang_combo))

        # --- Folders ---
        self.output_edit = self._folder_row(root, t("settings.output_folder", self.language),
                                             self.settings.output_folder)
        self.temp_edit = self._folder_row(root, t("settings.temp_folder", self.language),
                                           self.settings.temp_folder)

        # --- AI / performance ---
        self.model_combo = QComboBox()
        self.model_combo.addItems(list(VALID_WHISPER_MODELS))
        self.model_combo.setCurrentText(self.settings.whisper_model_size)
        self.model_combo.currentTextChanged.connect(self._on_model_changed)
        root.addWidget(SettingsRow("Whisper Model", self.model_combo))

        self.duration_combo = QComboBox()
        self.duration_combo.addItems(["15", "30", "45", "60"])
        self.duration_combo.setCurrentText(str(self.settings.default_clip_duration))
        self.duration_combo.currentTextChanged.connect(self._on_duration_changed)
        root.addWidget(SettingsRow(t("clipgen.duration_bucket", self.language), self.duration_combo))

        self.autosave_check = QCheckBox("Auto Save")
        self.autosave_check.setChecked(self.settings.auto_save)
        self.autosave_check.toggled.connect(self._on_autosave_changed)
        root.addWidget(self.autosave_check)

        self.backup_check = QCheckBox("Backup Otomatis" if self.language == "id" else "Auto Backup")
        self.backup_check.setChecked(self.settings.auto_backup)
        self.backup_check.toggled.connect(self._on_backup_changed)
        root.addWidget(self.backup_check)

        # --- AI Caption Generator ---
        caption_title = QLabel("AI Caption Generator")
        caption_title.setStyleSheet("font-size: 15px; font-weight: 600; margin-top: 12px;")
        root.addWidget(caption_title)

        self.caption_backend_combo = QComboBox()
        self.caption_backend_combo.addItem(
            "Heuristik (offline, tanpa API key)" if self.language == "id" else "Heuristic (offline, no API key)",
            "heuristic")
        self.caption_backend_combo.addItem(
            "LLM (kualitas lebih baik, butuh API key sendiri)" if self.language == "id"
            else "LLM (higher quality, needs your own API key)", "llm")
        idx = self.caption_backend_combo.findData(self.settings.caption_backend)
        self.caption_backend_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.caption_backend_combo.currentIndexChanged.connect(self._on_caption_backend_changed)
        root.addWidget(SettingsRow("Backend", self.caption_backend_combo))

        self.caption_llm_box = QWidget()
        llm_layout = QVBoxLayout(self.caption_llm_box)
        llm_layout.setContentsMargins(0, 0, 0, 0)

        self.caption_provider_combo = QComboBox()
        self.caption_provider_combo.addItem("Anthropic (Claude)", "anthropic")
        self.caption_provider_combo.addItem("OpenAI", "openai")
        p_idx = self.caption_provider_combo.findData(self.settings.caption_llm_provider)
        self.caption_provider_combo.setCurrentIndex(p_idx if p_idx >= 0 else 0)
        self.caption_provider_combo.currentIndexChanged.connect(self._on_caption_provider_changed)
        llm_layout.addWidget(SettingsRow("Provider", self.caption_provider_combo))

        self.caption_api_key_edit = QLineEdit(self.settings.caption_llm_api_key)
        self.caption_api_key_edit.setEchoMode(QLineEdit.Password)
        self.caption_api_key_edit.setPlaceholderText("sk-ant-..." if self.language == "id" else "sk-ant-...")
        self.caption_api_key_edit.editingFinished.connect(self._on_caption_api_key_changed)
        llm_layout.addWidget(SettingsRow("API Key", self.caption_api_key_edit))

        self.caption_model_edit = QLineEdit(self.settings.caption_llm_model)
        self.caption_model_edit.setPlaceholderText("claude-haiku-4-5-20251001 (default)")
        self.caption_model_edit.editingFinished.connect(self._on_caption_model_changed)
        llm_layout.addWidget(SettingsRow("Model", self.caption_model_edit))

        key_warning = QLabel(
            "API key disimpan lokal di settings.json dalam bentuk teks biasa (tidak terenkripsi) dan "
            "hanya dikirim langsung ke provider yang kamu pilih -- tidak pernah ke server lain."
            if self.language == "id" else
            "The API key is stored locally in settings.json as plain text (not encrypted) and is sent "
            "only directly to the provider you choose -- never to any other server.")
        key_warning.setWordWrap(True)
        key_warning.setProperty("role", "muted")
        key_warning.setStyleSheet("font-size: 10px;")
        llm_layout.addWidget(key_warning)

        root.addWidget(self.caption_llm_box)
        self.caption_llm_box.setVisible(self.settings.caption_backend == "llm")

        # --- System info card ---
        sys_title = QLabel(t("settings.system", self.language))
        sys_title.setStyleSheet("font-size: 15px; font-weight: 600; margin-top: 12px;")
        root.addWidget(sys_title)

        self.system_card = QFrame()
        self.system_card.setProperty("class", "card")
        sys_layout = QVBoxLayout(self.system_card)
        self.gpu_label = QLabel("...")
        self.hw_label = QLabel("...")
        self.resource_label = QLabel("...")
        for lbl in (self.gpu_label, self.hw_label, self.resource_label):
            lbl.setProperty("role", "muted")
            sys_layout.addWidget(lbl)
        root.addWidget(self.system_card)

        root.addStretch(1)

    def _folder_row(self, root, label: str, initial: str) -> QLineEdit:
        row = QHBoxLayout()
        edit = QLineEdit(initial)
        browse_btn = QPushButton("...")
        browse_btn.setFixedWidth(36)
        browse_btn.setProperty("class", "secondary")

        def browse():
            folder = QFileDialog.getExistingDirectory(self, label, edit.text())
            if folder:
                edit.setText(folder)
                self._persist_folders()

        browse_btn.clicked.connect(browse)
        edit.editingFinished.connect(self._persist_folders)
        row.addWidget(edit, 1)
        row.addWidget(browse_btn)
        wrapper = QWidget()
        wrapper.setLayout(row)
        root.addWidget(SettingsRow(label, wrapper))
        return edit

    def _refresh_system_info(self):
        gpu = detect_nvidia_gpu()
        gpu_text = (f"GPU: {gpu.name}" if gpu.available else
                    ("GPU: Tidak terdeteksi (mode CPU)" if self.language == "id" else "GPU: Not detected (CPU mode)"))
        self.gpu_label.setText(gpu_text)

        encoders = detect_ffmpeg_hw_encoders()
        hw_text = ("Hardware Encoding: " + (", ".join(encoders) if encoders else
                    ("Tidak ada (pakai CPU)" if self.language == "id" else "None (using CPU)")))
        self.hw_label.setText(hw_text)

        self._refresh_resource_usage()

    def _refresh_resource_usage(self):
        usage = get_resource_usage()
        if usage["cpu_percent"] is None:
            self.resource_label.setText("RAM/CPU: psutil belum terpasang" if self.language == "id"
                                         else "RAM/CPU: psutil not installed")
        else:
            self.resource_label.setText(
                f"CPU: {usage['cpu_percent']:.0f}%   ·   RAM: {usage['ram_used_gb']}/{usage['ram_total_gb']} GB "
                f"({usage['ram_percent']:.0f}%)"
            )

    # ---- persistence ----------------------------------------------
    def _persist_folders(self):
        self.settings.output_folder = self.output_edit.text().strip() or self.settings.output_folder
        self.settings.temp_folder = self.temp_edit.text().strip() or self.settings.temp_folder
        self.settings.save()

    def _on_theme_changed(self, value: str):
        self.settings.theme = value
        self.settings.save()
        self.theme_changed.emit(value)

    def _on_language_changed(self, _index: int):
        value = self.lang_combo.currentData()
        self.settings.language = value
        self.settings.save()
        self.language_changed.emit(value)

    def _on_model_changed(self, value: str):
        self.settings.whisper_model_size = value
        self.settings.save()

    def _on_duration_changed(self, value: str):
        self.settings.default_clip_duration = int(value)
        self.settings.save()

    def _on_autosave_changed(self, checked: bool):
        self.settings.auto_save = checked
        self.settings.save()

    def _on_backup_changed(self, checked: bool):
        self.settings.auto_backup = checked
        self.settings.save()

    def _on_caption_backend_changed(self, _index: int):
        value = self.caption_backend_combo.currentData()
        self.settings.caption_backend = value
        self.settings.save()
        self.caption_llm_box.setVisible(value == "llm")

    def _on_caption_provider_changed(self, _index: int):
        self.settings.caption_llm_provider = self.caption_provider_combo.currentData()
        self.settings.save()

    def _on_caption_api_key_changed(self):
        self.settings.caption_llm_api_key = self.caption_api_key_edit.text().strip()
        self.settings.save()

    def _on_caption_model_changed(self):
        self.settings.caption_llm_model = self.caption_model_edit.text().strip()
        self.settings.save()
