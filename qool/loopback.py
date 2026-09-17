"""التقاط الصوت الخارج من الجهاز (loopback) من غير درايفر ولا صلاحيات Administrator.

- EndpointLoopback: كل اللي بيتشغّل على جهاز خرج معيّن (زي الهيدفون).
- ProcessLoopback: صوت برنامج واحد بس. تحذير: Teams بيستثني صوت المكالمة منه (اتقاس: صمت تام وقت
  ميتينج شغال)، عشان كده جسر الميتينج بيستخدم EndpointLoopback.
- render_sessions: مين بيشغّل صوت على أنهي جهاز، ومقياس الجلسة (بيشتغل حتى مع Teams).

كل استدعاءات COM بتتعمل في خيوط MTA (خيوط الالتقاط أو run_in_mta)، مش في خيط الواجهة.
"""
from __future__ import annotations

import ctypes
import logging
import threading
import winreg
from ctypes import POINTER, Structure, byref, c_float, c_int, c_long, c_longlong, c_ubyte, c_uint, c_uint32, c_ulonglong, c_void_p, c_wchar_p
from ctypes.wintypes import DWORD, HANDLE, WORD
from dataclasses import dataclass

import comtypes
import numpy as np
from comtypes import CLSCTX_ALL, COMMETHOD, GUID, HRESULT, COMObject, IUnknown

log = logging.getLogger(__name__)

RATE = 48000
CHANNELS = 2

_VAD_PROCESS_LOOPBACK = "VAD\\Process_Loopback"
_ACTIVATION_TYPE_PROCESS_LOOPBACK = 1
_MODE_INCLUDE_TREE = 0
_MODE_EXCLUDE_TREE = 1
_VT_BLOB = 65

_AUDCLNT_SHAREMODE_SHARED = 0
_AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
_AUDCLNT_STREAMFLAGS_EVENTCALLBACK = 0x00040000
_AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM = 0x80000000
_AUDCLNT_BUFFERFLAGS_SILENT = 0x2
_WAVE_FORMAT_PCM = 1

_CLSID_MMDeviceEnumerator = GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
_E_RENDER = 0
_E_CONSOLE = 0
_DEVICE_STATE_ACTIVE = 1
SESSION_ACTIVE = 1
_PKEY_NAME = "{a45c254e-df1c-4efd-8020-67d146a850e0},2"
_PKEY_IFACE = "{b3f8fa53-0004-438e-9003-51a46e139bfc},6"


class WAVEFORMATEX(Structure):
    _pack_ = 1
    _fields_ = [("wFormatTag", WORD), ("nChannels", WORD), ("nSamplesPerSec", DWORD),
                ("nAvgBytesPerSec", DWORD), ("nBlockAlign", WORD), ("wBitsPerSample", WORD), ("cbSize", WORD)]


class _BLOB(Structure):
    _fields_ = [("cbSize", c_uint32), ("pBlobData", c_void_p)]


class PROPVARIANT(Structure):
    _fields_ = [("vt", WORD), ("r1", WORD), ("r2", WORD), ("r3", WORD), ("blob", _BLOB)]


class AUDIOCLIENT_ACTIVATION_PARAMS(Structure):
    _fields_ = [("ActivationType", c_int), ("TargetProcessId", DWORD), ("ProcessLoopbackMode", c_int)]


class IActivateAudioInterfaceAsyncOperation(IUnknown):
    _iid_ = GUID("{72A22D78-CDE4-431D-B8CC-843A71199B6D}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetActivateResult",
                  (["out"], POINTER(c_long), "activateResult"),
                  (["out"], POINTER(POINTER(IUnknown)), "activatedInterface")),
    ]


class IActivateAudioInterfaceCompletionHandler(IUnknown):
    _iid_ = GUID("{41D949AB-9862-444A-80F6-C261334DA5EB}")
    _methods_ = [
        COMMETHOD([], HRESULT, "ActivateCompleted",
                  (["in"], POINTER(IActivateAudioInterfaceAsyncOperation), "activateOperation")),
    ]


class IAgileObject(IUnknown):
    _iid_ = GUID("{94EA2B94-E9CC-49E0-C0FF-EE64CA8F5B90}")
    _methods_ = []


class IAudioClient(IUnknown):
    _iid_ = GUID("{1CB9AD4C-DBFA-4C32-B178-C2F568A703B2}")
    _methods_ = [
        COMMETHOD([], HRESULT, "Initialize",
                  (["in"], c_int, "ShareMode"), (["in"], DWORD, "StreamFlags"),
                  (["in"], c_longlong, "hnsBufferDuration"), (["in"], c_longlong, "hnsPeriodicity"),
                  (["in"], POINTER(WAVEFORMATEX), "pFormat"), (["in"], POINTER(GUID), "AudioSessionGuid")),
        COMMETHOD([], HRESULT, "GetBufferSize", (["out"], POINTER(c_uint32), "pNumBufferFrames")),
        COMMETHOD([], HRESULT, "GetStreamLatency", (["out"], POINTER(c_longlong), "phnsLatency")),
        COMMETHOD([], HRESULT, "GetCurrentPadding", (["out"], POINTER(c_uint32), "pNumPaddingFrames")),
        COMMETHOD([], HRESULT, "IsFormatSupported", (["in"], c_int, "ShareMode"),
                  (["in"], POINTER(WAVEFORMATEX), "pFormat"), (["out"], POINTER(POINTER(WAVEFORMATEX)), "ppClosestMatch")),
        COMMETHOD([], HRESULT, "GetMixFormat", (["out"], POINTER(POINTER(WAVEFORMATEX)), "ppDeviceFormat")),
        COMMETHOD([], HRESULT, "GetDevicePeriod", (["out"], POINTER(c_longlong), "phnsDefault"),
                  (["out"], POINTER(c_longlong), "phnsMinimum")),
        COMMETHOD([], HRESULT, "Start"),
        COMMETHOD([], HRESULT, "Stop"),
        COMMETHOD([], HRESULT, "Reset"),
        COMMETHOD([], HRESULT, "SetEventHandle", (["in"], HANDLE, "eventHandle")),
        COMMETHOD([], HRESULT, "GetService", (["in"], POINTER(GUID), "riid"),
                  (["out"], POINTER(POINTER(IUnknown)), "ppv")),
    ]


class IAudioCaptureClient(IUnknown):
    _iid_ = GUID("{C8ADBD64-E71E-48A0-A4DE-185C395CD317}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetBuffer",
                  (["out"], POINTER(POINTER(c_ubyte)), "ppData"), (["out"], POINTER(c_uint32), "pNumFramesToRead"),
                  (["out"], POINTER(DWORD), "pdwFlags"), (["out"], POINTER(c_ulonglong), "pu64DevicePosition"),
                  (["out"], POINTER(c_ulonglong), "pu64QPCPosition")),
        COMMETHOD([], HRESULT, "ReleaseBuffer", (["in"], c_uint32, "NumFramesRead")),
        COMMETHOD([], HRESULT, "GetNextPacketSize", (["out"], POINTER(c_uint32), "pNumFramesInNextPacket")),
    ]


class IMMDevice(IUnknown):
    _iid_ = GUID("{D666063F-1587-4E43-81F1-B948E807363F}")
    _methods_ = [
        COMMETHOD([], HRESULT, "Activate", (["in"], POINTER(GUID), "iid"), (["in"], DWORD, "dwClsCtx"),
                  (["in"], c_void_p, "pActivationParams"), (["out"], POINTER(POINTER(IUnknown)), "ppInterface")),
        COMMETHOD([], HRESULT, "OpenPropertyStore", (["in"], DWORD, "stgmAccess"), (["out"], POINTER(c_void_p), "ppProperties")),
        COMMETHOD([], HRESULT, "GetId", (["out"], POINTER(c_void_p), "ppstrId")),
        COMMETHOD([], HRESULT, "GetState", (["out"], POINTER(DWORD), "pdwState")),
    ]


class IMMDeviceCollection(IUnknown):
    _iid_ = GUID("{0BD7A1BE-7A1A-44DB-8397-CC5392387B5E}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetCount", (["out"], POINTER(c_uint), "pcDevices")),
        COMMETHOD([], HRESULT, "Item", (["in"], c_uint, "nDevice"), (["out"], POINTER(POINTER(IMMDevice)), "ppDevice")),
    ]


class IMMDeviceEnumerator(IUnknown):
    _iid_ = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
    _methods_ = [
        COMMETHOD([], HRESULT, "EnumAudioEndpoints", (["in"], c_int, "dataFlow"), (["in"], DWORD, "dwStateMask"),
                  (["out"], POINTER(POINTER(IMMDeviceCollection)), "ppDevices")),
        COMMETHOD([], HRESULT, "GetDefaultAudioEndpoint", (["in"], c_int, "dataFlow"), (["in"], c_int, "role"),
                  (["out"], POINTER(POINTER(IMMDevice)), "ppEndpoint")),
        COMMETHOD([], HRESULT, "GetDevice", (["in"], c_wchar_p, "pwstrId"), (["out"], POINTER(POINTER(IMMDevice)), "ppDevice")),
    ]


class IAudioSessionControl2(IUnknown):
    _iid_ = GUID("{BFB7FF88-7239-4FC9-8FA2-07C950BE9C6D}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetState", (["out"], POINTER(c_int), "pRetVal")),
        COMMETHOD([], HRESULT, "GetDisplayName", (["out"], POINTER(c_void_p), "pRetVal")),
        COMMETHOD([], HRESULT, "SetDisplayName", (["in"], c_wchar_p, "Value"), (["in"], c_void_p, "EventContext")),
        COMMETHOD([], HRESULT, "GetIconPath", (["out"], POINTER(c_void_p), "pRetVal")),
        COMMETHOD([], HRESULT, "SetIconPath", (["in"], c_wchar_p, "Value"), (["in"], c_void_p, "EventContext")),
        COMMETHOD([], HRESULT, "GetGroupingParam", (["out"], POINTER(GUID), "pRetVal")),
        COMMETHOD([], HRESULT, "SetGroupingParam", (["in"], POINTER(GUID), "Override"), (["in"], c_void_p, "EventContext")),
        COMMETHOD([], HRESULT, "RegisterAudioSessionNotification", (["in"], c_void_p, "NewNotifications")),
        COMMETHOD([], HRESULT, "UnregisterAudioSessionNotification", (["in"], c_void_p, "NewNotifications")),
        COMMETHOD([], HRESULT, "GetSessionIdentifier", (["out"], POINTER(c_void_p), "pRetVal")),
        COMMETHOD([], HRESULT, "GetSessionInstanceIdentifier", (["out"], POINTER(c_void_p), "pRetVal")),
        COMMETHOD([], HRESULT, "GetProcessId", (["out"], POINTER(DWORD), "pRetVal")),
    ]


class IAudioSessionEnumerator(IUnknown):
    _iid_ = GUID("{E2F5BB11-0570-40CA-ACDD-3AA01277DEE8}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetCount", (["out"], POINTER(c_int), "SessionCount")),
        COMMETHOD([], HRESULT, "GetSession", (["in"], c_int, "SessionCount"), (["out"], POINTER(POINTER(IUnknown)), "Session")),
    ]


class IAudioSessionManager2(IUnknown):
    _iid_ = GUID("{77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetAudioSessionControl", (["in"], c_void_p, "AudioSessionGuid"), (["in"], DWORD, "StreamFlags"),
                  (["out"], POINTER(c_void_p), "SessionControl")),
        COMMETHOD([], HRESULT, "GetSimpleAudioVolume", (["in"], c_void_p, "AudioSessionGuid"), (["in"], DWORD, "StreamFlags"),
                  (["out"], POINTER(c_void_p), "AudioVolume")),
        COMMETHOD([], HRESULT, "GetSessionEnumerator", (["out"], POINTER(POINTER(IAudioSessionEnumerator)), "SessionEnum")),
    ]


class IAudioMeterInformation(IUnknown):
    _iid_ = GUID("{C02216F6-8C67-4B5B-9D00-D008E73E0064}")
    _methods_ = [COMMETHOD([], HRESULT, "GetPeakValue", (["out"], POINTER(c_float), "pfPeak"))]


class _Completion(COMObject):
    _com_interfaces_ = [IActivateAudioInterfaceCompletionHandler, IAgileObject]

    def __init__(self):
        super().__init__()
        self.done = threading.Event()

    def ActivateCompleted(self, activateOperation):
        self.done.set()


_activate = ctypes.WinDLL("Mmdevapi.dll").ActivateAudioInterfaceAsync
_activate.restype = HRESULT
_activate.argtypes = [c_wchar_p, POINTER(GUID), POINTER(PROPVARIANT),
                      POINTER(IActivateAudioInterfaceCompletionHandler),
                      POINTER(POINTER(IActivateAudioInterfaceAsyncOperation))]

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreateEventW.restype = HANDLE
_kernel32.CreateEventW.argtypes = [c_void_p, c_int, c_int, c_wchar_p]
_kernel32.WaitForSingleObject.restype = DWORD
_kernel32.WaitForSingleObject.argtypes = [HANDLE, DWORD]
_kernel32.CloseHandle.argtypes = [HANDLE]
_ole32 = ctypes.WinDLL("ole32")
_ole32.CoTaskMemFree.argtypes = [c_void_p]


class LoopbackError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# أجهزة الخرج وجلسات الصوت
# ---------------------------------------------------------------------------

def _take_wstr(address) -> str:
    """نص راجع من COM بذاكرة CoTaskMem: نقرأه ونحرر الذاكرة."""
    if not address:
        return ""
    try:
        return ctypes.wstring_at(address)
    finally:
        _ole32.CoTaskMemFree(address)


def _enumerator():
    import comtypes.client

    return comtypes.client.CreateObject(_CLSID_MMDeviceEnumerator, interface=IMMDeviceEnumerator)


def device_name(device_id: str) -> str:
    guid = device_id.rsplit("}.", 1)[-1]
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            rf"SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render\{guid}\Properties") as k:
            return f"{winreg.QueryValueEx(k, _PKEY_NAME)[0]} ({winreg.QueryValueEx(k, _PKEY_IFACE)[0]})"
    except OSError:
        return device_id


@dataclass
class RenderSession:
    device_id: str
    device_name: str
    pid: int
    state: int
    meter: object = None  # POINTER(IAudioMeterInformation) — يُستخدم من نفس الخيط اللي جابه بس

    @property
    def active(self) -> bool:
        return self.state == SESSION_ACTIVE

    def peak(self) -> float:
        try:
            return float(self.meter.GetPeakValue()) if self.meter is not None else 0.0
        except Exception:
            return 0.0


def render_sessions(with_meters: bool = False) -> list[RenderSession]:
    """كل جلسات الصوت على كل أجهزة الخرج الشغالة. لازم من خيط MTA."""
    out: list[RenderSession] = []
    devices = _enumerator().EnumAudioEndpoints(_E_RENDER, _DEVICE_STATE_ACTIVE)
    for i in range(devices.GetCount()):
        dev = devices.Item(i)
        dev_id = _take_wstr(dev.GetId())
        name = device_name(dev_id)
        try:
            mgr = dev.Activate(byref(IAudioSessionManager2._iid_), CLSCTX_ALL, None).QueryInterface(IAudioSessionManager2)
            sessions = mgr.GetSessionEnumerator()
            for j in range(sessions.GetCount()):
                s = sessions.GetSession(j)
                ctl = s.QueryInterface(IAudioSessionControl2)
                pid = ctl.GetProcessId()
                if not pid:
                    continue
                meter = s.QueryInterface(IAudioMeterInformation) if with_meters else None
                out.append(RenderSession(dev_id, name, int(pid), int(ctl.GetState()), meter))
        except Exception:
            log.debug("تعذر قراءة جلسات الجهاز %s", name, exc_info=True)
    return out


def default_render_device() -> tuple[str, str]:
    dev_id = _take_wstr(_enumerator().GetDefaultAudioEndpoint(_E_RENDER, _E_CONSOLE).GetId())
    return dev_id, device_name(dev_id)


def run_in_mta(fn, timeout: float = 10.0):
    """ينفّذ fn في خيط MTA مؤقت ويرجع النتيجة. النتيجة لازم تكون بيانات عادية مش كائنات COM."""
    box: dict = {}

    def work():
        comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        try:
            box["value"] = fn()
        except BaseException as e:  # noqa: BLE001 — بترجع للمنادي
            box["error"] = e.with_traceback(None)  # نسيب مراجع COM قبل CoUninitialize
        finally:
            comtypes.CoUninitialize()

    t = threading.Thread(target=work, name="audio-com", daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise LoopbackError("Windows ما ردش على طلب قراءة أجهزة الصوت")
    if "error" in box:
        raise box["error"]
    return box.get("value")


# ---------------------------------------------------------------------------
# الالتقاط
# ---------------------------------------------------------------------------

class _Loopback:
    """خيط MTA بيفتح IAudioClient في وضع loopback ويبعت بلوكات mono float32 بمعدل 48kHz لـ on_audio."""

    use_event = False
    label = "loopback"

    def __init__(self, on_audio):
        self.on_audio = on_audio
        self.error: str | None = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, timeout: float = 5.0) -> None:
        self._thread = threading.Thread(target=self._run, name=self.label, daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout):
            raise LoopbackError("Windows ما ردش على طلب التقاط الصوت")
        if self.error:
            raise LoopbackError(self.error)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(2)

    def _activate(self):
        raise NotImplementedError

    def _open(self):
        client = self._activate()
        fmt = WAVEFORMATEX(_WAVE_FORMAT_PCM, CHANNELS, RATE, RATE * CHANNELS * 2, CHANNELS * 2, 16, 0)
        flags = _AUDCLNT_STREAMFLAGS_LOOPBACK | _AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM
        if self.use_event:
            flags |= _AUDCLNT_STREAMFLAGS_EVENTCALLBACK
        client.Initialize(_AUDCLNT_SHAREMODE_SHARED, flags, 2_000_000, 0, byref(fmt), None)
        event = None
        if self.use_event:
            event = _kernel32.CreateEventW(None, 0, 0, None)
            client.SetEventHandle(event)
        capture = client.GetService(byref(IAudioCaptureClient._iid_)).QueryInterface(IAudioCaptureClient)
        return client, capture, event

    def _run(self) -> None:
        comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        event = None
        client = capture = None
        try:
            try:
                client, capture, event = self._open()
                client.Start()
            except Exception as e:
                self.error = str(e)
                return
            finally:
                self._ready.set()

            block_align = CHANNELS * 2
            while not self._stop.is_set():
                if event:
                    _kernel32.WaitForSingleObject(event, 100)
                else:
                    self._stop.wait(0.01)
                frames_next = capture.GetNextPacketSize()
                while frames_next:
                    data, frames, flags, _, _ = capture.GetBuffer()
                    if frames and not (flags & _AUDCLNT_BUFFERFLAGS_SILENT) and data:
                        raw = ctypes.string_at(ctypes.cast(data, c_void_p), frames * block_align)
                        pcm = np.frombuffer(raw, dtype=np.int16).reshape(-1, CHANNELS)
                        block = pcm.mean(axis=1, dtype=np.float32) / 32768.0
                    else:
                        block = np.zeros(frames, dtype=np.float32)
                    capture.ReleaseBuffer(frames)
                    if frames:
                        self.on_audio(block)
                    frames_next = capture.GetNextPacketSize()
            client.Stop()
        except Exception as e:  # انقطاع أثناء التشغيل (جهاز اتفصل مثلًا)
            self.error = str(e)
        finally:
            client = capture = None
            if event:
                _kernel32.CloseHandle(event)
            comtypes.CoUninitialize()


class EndpointLoopback(_Loopback):
    """كل الصوت اللي بيتشغّل على جهاز خرج واحد."""

    def __init__(self, device_id: str, on_audio):
        super().__init__(on_audio)
        self.device_id = device_id
        self.label = "loopback-endpoint"

    def _activate(self):
        dev = _enumerator().GetDevice(self.device_id)
        return dev.Activate(byref(IAudioClient._iid_), CLSCTX_ALL, None).QueryInterface(IAudioClient)


class ProcessLoopback(_Loopback):
    """صوت عملية واحدة (ومعاها أولادها). مش بيلتقط صوت مكالمات Teams."""

    use_event = True

    def __init__(self, pid: int, on_audio, include_tree: bool = True):
        super().__init__(on_audio)
        self.pid = pid
        self.include_tree = include_tree
        self.label = f"loopback-{pid}"

    def _activate(self):
        params = AUDIOCLIENT_ACTIVATION_PARAMS(
            _ACTIVATION_TYPE_PROCESS_LOOPBACK, self.pid,
            _MODE_INCLUDE_TREE if self.include_tree else _MODE_EXCLUDE_TREE)
        pv = PROPVARIANT()
        pv.vt = _VT_BLOB
        pv.blob.cbSize = ctypes.sizeof(params)
        pv.blob.pBlobData = ctypes.addressof(params)

        handler = _Completion()
        op = POINTER(IActivateAudioInterfaceAsyncOperation)()
        _activate(_VAD_PROCESS_LOOPBACK, byref(IAudioClient._iid_), byref(pv),
                  handler.QueryInterface(IActivateAudioInterfaceCompletionHandler), byref(op))
        if not handler.done.wait(5):
            raise LoopbackError("انتهت مهلة تفعيل process loopback")
        hr, unk = op.GetActivateResult()
        if hr < 0:
            raise LoopbackError(f"فشل تفعيل process loopback (HRESULT 0x{hr & 0xFFFFFFFF:08X})")
        return unk.QueryInterface(IAudioClient)
