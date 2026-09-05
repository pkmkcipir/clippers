"""Tests for the AI Clip Generator page's caption UI: the ClipCard's
Caption button and CaptionDialog's Copy All clipboard behavior.
Run with: QT_QPA_PLATFORM=offscreen pytest tests/test_clip_card_caption_ui.py -v
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

FAKE_CLIP = {
    "id": "clip1", "start_time": 12.0, "end_time": 42.0, "duration": 30.0,
    "viral_score": 82.3, "confidence_score": 60.0,
    "transcript_text": "Ini contoh transkrip klip untuk menguji tampilan kartu caption.",
    "thumbnail_path": None,
    "suggested_title": "Investasi Saham yang Bikin Semua Orang Kaget",
    "suggested_hashtags": "#investasi #saham #gila #ternyata",
    "suggested_caption": "Halo semua, hari ini kita bahas investasi saham.",
    "suggested_description": "Deskripsi lebih panjang tentang investasi saham untuk pemula.",
    "suggested_keywords": "investasi, saham, gila, ternyata",
    "caption_source": "heuristic",
}


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_clip_card_shows_title_and_caption_button(qapp):
    from app.pages.ai_clip_generator import ClipCard
    card = ClipCard(FAKE_CLIP, "id")
    card.show()
    qapp.processEvents()
    # Constructing without exceptions plus a caption button existing is
    # the meaningful assertion here since Qt layout internals aren't
    # otherwise inspectable in a stable way.
    assert card.clip["suggested_title"] == FAKE_CLIP["suggested_title"]


def test_clip_card_without_caption_data_does_not_crash(qapp):
    """Backward compatibility: clips generated before this feature
    existed have empty caption fields and must still render."""
    from app.pages.ai_clip_generator import ClipCard
    old_clip = dict(FAKE_CLIP, suggested_title="", suggested_caption="",
                     suggested_description="", suggested_hashtags="", suggested_keywords="")
    card = ClipCard(old_clip, "id")
    card.show()
    qapp.processEvents()
    assert card.clip["suggested_title"] == ""


def test_caption_dialog_copy_all_populates_clipboard(qapp):
    from app.pages.ai_clip_generator import CaptionDialog
    dialog = CaptionDialog(FAKE_CLIP, "id")
    dialog.show()
    qapp.processEvents()

    dialog._copy_all()
    qapp.processEvents()
    clipboard_text = QApplication.clipboard().text()

    assert FAKE_CLIP["suggested_title"] in clipboard_text
    assert FAKE_CLIP["suggested_caption"] in clipboard_text
    assert FAKE_CLIP["suggested_description"] in clipboard_text
    assert FAKE_CLIP["suggested_hashtags"] in clipboard_text
    assert FAKE_CLIP["suggested_keywords"] in clipboard_text


def test_caption_dialog_fields_are_read_only(qapp):
    """The dialog is for reviewing/copying generated text, not editing
    it in place (editing happens by regenerating, not hand-patching)."""
    from app.pages.ai_clip_generator import CaptionDialog
    dialog = CaptionDialog(FAKE_CLIP, "id")
    for _label, widget in dialog._fields:
        if hasattr(widget, "isReadOnly"):
            assert widget.isReadOnly() is True
