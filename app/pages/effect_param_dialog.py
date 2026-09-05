"""Dynamically builds a parameter-editing dialog for one effect, driven
entirely by editor.effects.EFFECT_REGISTRY's param specs -- so adding a
new effect kind to the registry doesn't require writing a new dialog.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QPushButton,
    QDoubleSpinBox, QComboBox,
)

from editor.effects import EFFECT_REGISTRY


class EffectParamDialog(QDialog):
    def __init__(self, kind: str, initial_params: dict | None, language: str = "id", parent=None):
        super().__init__(parent)
        self.kind = kind
        self.language = language
        spec = EFFECT_REGISTRY.get(kind, {"params": {}})
        initial_params = initial_params or {}

        label_key = "label_id" if language == "id" else "label_en"
        self.setWindowTitle(spec.get(label_key, kind))
        self.setMinimumWidth(340)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self._controls: dict[str, QDoubleSpinBox | QComboBox] = {}

        for param_name, param_spec in spec.get("params", {}).items():
            default = initial_params.get(param_name, param_spec.get("default"))
            if "options" in param_spec:
                combo = QComboBox()
                options = param_spec["options"]
                for opt in options:
                    if isinstance(opt, tuple):
                        combo.addItem(opt[0], opt[1])
                    else:
                        combo.addItem(str(opt), opt)
                idx = combo.findData(default)
                combo.setCurrentIndex(idx if idx >= 0 else 0)
                form.addRow(param_name.replace("_", " ").title(), combo)
                self._controls[param_name] = combo
            else:
                spin = QDoubleSpinBox()
                spin.setMinimum(float(param_spec.get("min", 0.0)))
                spin.setMaximum(float(param_spec.get("max", 100.0)))
                spin.setSingleStep(0.05 if spin.maximum() <= 3 else 1.0)
                spin.setDecimals(2)
                spin.setValue(float(default if default is not None else param_spec.get("min", 0.0)))
                form.addRow(param_name.replace("_", " ").title(), spin)
                self._controls[param_name] = spin

        layout.addLayout(form)

        buttons = QHBoxLayout()
        cancel_btn = QPushButton("Batal" if language == "id" else "Cancel")
        cancel_btn.setProperty("class", "secondary")
        cancel_btn.clicked.connect(self.reject)
        ok_btn = QPushButton("Terapkan" if language == "id" else "Apply")
        ok_btn.setProperty("class", "primary")
        ok_btn.clicked.connect(self.accept)
        buttons.addStretch(1)
        buttons.addWidget(cancel_btn)
        buttons.addWidget(ok_btn)
        layout.addLayout(buttons)

    def get_params(self) -> dict:
        result = {}
        for name, control in self._controls.items():
            if isinstance(control, QComboBox):
                result[name] = control.currentData()
            else:
                result[name] = control.value()
        return result
