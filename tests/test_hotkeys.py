"""hotkeys — 키 코드 변환 순수 함수 검증."""
from PySide6.QtCore import Qt

from utils.hotkeys import binding_to_label, qt_event_to_vkmods


def test_binding_to_label_modifiers() -> None:
    # mods: 0x0001=Alt, 0x0002=Ctrl, 0x0004=Shift, 0x0008=Win
    assert binding_to_label(0x0002, 0x70) == "Ctrl + F1"
    assert binding_to_label(0x0002 | 0x0004, 0x41) == "Ctrl + Shift + A"
    assert binding_to_label(0, 0x20) == "Space"


def test_binding_to_label_unknown_vk() -> None:
    assert binding_to_label(0, 0xFF) == "VKFF"


def test_qt_event_to_vkmods_function_keys() -> None:
    mods, vk = qt_event_to_vkmods(
        Qt.Key.Key_F1, Qt.KeyboardModifier.ControlModifier
    )
    assert (mods, vk) == (0x0002, 0x70)


def test_qt_event_to_vkmods_letter_with_two_mods() -> None:
    mods, vk = qt_event_to_vkmods(
        Qt.Key.Key_A,
        Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
    )
    assert vk == ord("A")
    assert mods == 0x0002 | 0x0004


def test_qt_event_to_vkmods_unmapped_key_returns_zero_vk() -> None:
    mods, vk = qt_event_to_vkmods(
        Qt.Key.Key_F35, Qt.KeyboardModifier.NoModifier
    )
    assert vk == 0


def test_roundtrip_qt_to_label() -> None:
    """Qt 입력 → win32 코드 → 사람이 읽는 라벨까지 한 바퀴."""
    mods, vk = qt_event_to_vkmods(
        Qt.Key.Key_F2, Qt.KeyboardModifier.ControlModifier
    )
    assert binding_to_label(mods, vk) == "Ctrl + F2"
