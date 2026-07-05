"""wgc_capture 의 GUID 처리 검증.

vtable 호출 자체는 실제 Windows 환경이 필요해 수동 스모크로 검증하지만,
GUID 파싱과 handler PIID 계산은 순수 로직이라 여기서 고정한다.
PIID 가 틀리면 WGC 콜백 등록이 조용히 실패하므로 회귀 방지 가치가 크다.
"""
import uuid

from capture.wgc_capture import (
    GUID,
    IID_FrameArrivedHandler,
    _guid,
    _guid_equal,
)


def test_guid_layout_matches_bytes_le() -> None:
    """ctypes GUID 구조체의 메모리 레이아웃 = Windows GUID (bytes_le)."""
    s = "F810DB63-93F2-5FE6-A429-4BF67D98E90C"
    g = _guid("{" + s + "}")
    assert bytes(g) == uuid.UUID(s).bytes_le


def test_guid_equal() -> None:
    a = _guid("{00000000-0000-0000-C000-000000000046}")
    b = _guid("{00000000-0000-0000-C000-000000000046}")
    c = _guid("{94EA2B94-E9CC-49E0-C0FF-EE64CA8F5B90}")
    assert _guid_equal(a, b)
    assert not _guid_equal(a, c)


def test_frame_arrived_handler_piid() -> None:
    """하드코딩된 handler PIID 가 WinRT 표준 알고리즘(UUID v5)과 일치하는지.

    signature 형식: pinterface({TypedEventHandler PIID 베이스};
    rc(런타임클래스 풀네임;{기본 인터페이스 IID});cinterface(IInspectable))
    """
    ns = uuid.UUID("11f47ad5-7b73-42c0-abae-878b1e16adee")
    sig = (
        "pinterface({9de1c534-6ae1-11e0-84e1-18a905bcc53f};"
        "rc(Windows.Graphics.Capture.Direct3D11CaptureFramePool;"
        "{24b5d8fd-f76a-4eba-9b88-32a3a35b3eaf});cinterface(IInspectable))"
    )
    expected = uuid.uuid5(ns, sig)
    assert bytes(IID_FrameArrivedHandler) == expected.bytes_le


def test_guid_struct_size() -> None:
    import ctypes
    assert ctypes.sizeof(GUID) == 16
