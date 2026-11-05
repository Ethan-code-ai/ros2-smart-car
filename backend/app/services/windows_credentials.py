"""Current Windows user's non-roaming generic credential store."""
import ctypes
from ctypes import wintypes
import json
import os
import re


PROFILE_ENV = "INSIGHTROVER_CREDENTIAL_PROFILE"
NOT_FOUND = 1168


class CredentialError(RuntimeError):
    pass


class Credential(ctypes.Structure):
    _fields_ = [
        ("flags", wintypes.DWORD), ("type", wintypes.DWORD), ("target", wintypes.LPWSTR),
        ("comment", wintypes.LPWSTR), ("last_written", wintypes.FILETIME), ("size", wintypes.DWORD),
        ("blob", ctypes.POINTER(ctypes.c_ubyte)), ("persist", wintypes.DWORD),
        ("attribute_count", wintypes.DWORD), ("attributes", ctypes.c_void_p),
        ("alias", wintypes.LPWSTR), ("username", wintypes.LPWSTR),
    ]


def _target(profile):
    if not re.fullmatch(r"[0-9a-f]{64}", profile):
        raise CredentialError("凭据配置标识无效")
    return "InsightRover/v4/" + profile


def _library():
    if os.name != "nt":
        raise CredentialError("系统凭据存储仅在 Windows 桌面可用")
    lib = ctypes.WinDLL("advapi32", use_last_error=True)
    lib.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                             ctypes.POINTER(ctypes.POINTER(Credential))]
    lib.CredReadW.restype = wintypes.BOOL
    lib.CredWriteW.argtypes = [ctypes.POINTER(Credential), wintypes.DWORD]
    lib.CredWriteW.restype = wintypes.BOOL
    lib.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    lib.CredDeleteW.restype = wintypes.BOOL
    lib.CredFree.argtypes = [ctypes.c_void_p]
    lib.CredFree.restype = None
    return lib


def read_credentials(profile):
    target, lib = _target(profile), _library()
    pointer = ctypes.POINTER(Credential)()
    if not lib.CredReadW(target, 1, 0, ctypes.byref(pointer)):
        if ctypes.get_last_error() == NOT_FOUND:
            return {}
        raise CredentialError("无法读取 Windows 系统凭据")
    try:
        value = json.loads(ctypes.string_at(pointer.contents.blob, pointer.contents.size))
        if not isinstance(value, dict) or any(not isinstance(item, str) for item in value.values()):
            raise ValueError()
        return value
    except (ValueError, UnicodeError):
        raise CredentialError("Windows 系统凭据格式无效") from None
    finally:
        lib.CredFree(pointer)


def write_credentials(profile, value):
    target, lib = _target(profile), _library()
    encoded = json.dumps(value, ensure_ascii=False).encode("utf-8")
    if len(encoded) > 2560:
        raise CredentialError("凭据和模型连接配置过长")
    blob = (ctypes.c_ubyte * len(encoded)).from_buffer_copy(encoded)
    credential = Credential(type=1, target=target, size=len(encoded), blob=blob, persist=2,
                            username="InsightRover")
    try:
        if not lib.CredWriteW(ctypes.byref(credential), 0):
            raise CredentialError("无法保存到 Windows 系统凭据")
    finally:
        ctypes.memset(blob, 0, len(blob))


def delete_credentials(profile):
    target, lib = _target(profile), _library()
    if not lib.CredDeleteW(target, 1, 0) and ctypes.get_last_error() != NOT_FOUND:
        raise CredentialError("无法删除 Windows 系统凭据")


def machine_token():
    profile = os.getenv(PROFILE_ENV)
    if profile:
        try:
            return read_credentials(profile).get("robot_token", "")
        except CredentialError:
            return ""
    return os.getenv("ROBOT_SERVICE_TOKEN", "")
