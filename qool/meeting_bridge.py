"""جسر الميتينج: صوت الميتينج + المايك الحقيقي ← «CABLE Input».

ChatGPT (أو أي برنامج AI) يختار «CABLE Output» كمايك، فيسمع الناس في الميتينج ويسمعك إنت كمان،
وإنت سامع الميتينج عادي على الهيدفون.

إزاي بنلتقط صوت الميتينج: Teams بيستثني صوت المكالمة من process loopback، فبنلتقط كل اللي بيتشغّل على
جهاز الخرج اللي Teams شغال عليه (endpoint loopback)، والجهاز ده بيتعرف تلقائيًا من جلسات الصوت.
عشان ChatGPT ما يسمعش نفسه لو شغال على نفس الجهاز: وقت ما صوته طالع بنكتم صوت الميتينج عنه (ducking).
"""
from __future__ import annotations

import ctypes
import logging
import threading
import time
from collections import deque
from ctypes.wintypes import BOOL, DWORD, HANDLE, LONG, WCHAR

import comtypes
import numpy as np

from . import loopback

log = logging.getLogger(__name__)

RATE = loopback.RATE
BLOCK = RATE // 100             # 10ms
MAX_LAG = int(0.25 * RATE)      # لو التأخير زاد عن كده بنرمي القديم
KEEP_LAG = int(0.04 * RATE)
CABLE_WORD = "CABLE"
DEFAULT_APP = "ms-teams.exe"
DEFAULT_AI_APP = "ChatGPT.exe"
DEFAULT_OUTPUT = "CABLE Input"
SCAN_SECONDS = 1.0              # إعادة اكتشاف جهاز Teams وجلسات ChatGPT
METER_SECONDS = 0.02


class BridgeError(RuntimeError):
    pass


class Ring:
    """بافر بين خيوط الصوت؛ بيحافظ إن التأخير يفضل قليل مهما اختلفت ساعات الأجهزة."""

    def __init__(self):
        self._q: deque[np.ndarray] = deque()
        self._n = 0
        self._lock = threading.Lock()
        self.level = 0.0
        self.last_push = 0.0

    @property
    def buffered(self) -> int:
        return self._n

    @property
    def live_level(self) -> float:
        """المستوى، أو صفر لو مفيش صوت وصل من شوية (جهاز الخرج ساكت مش بيبعت packets)."""
        return self.level if time.monotonic() - self.last_push < 0.3 else 0.0

    def push(self, block: np.ndarray) -> None:
        if block.size:
            self.level = max(self.level * 0.9, float(np.sqrt(np.mean(block * block))))
        self.last_push = time.monotonic()
        with self._lock:
            self._q.append(block)
            self._n += block.size
            if self._n > MAX_LAG:
                self._drop(self._n - KEEP_LAG)

    def clear(self) -> None:
        with self._lock:
            self._q.clear()
            self._n = 0

    def _drop(self, count: int) -> None:
        while count > 0 and self._q:
            head = self._q[0]
            if head.size <= count:
                self._q.popleft()
                self._n -= head.size
                count -= head.size
            else:
                self._q[0] = head[count:]
                self._n -= count
                count = 0

    def pull(self, count: int) -> np.ndarray:
        out = np.zeros(count, dtype=np.float32)
        filled = 0
        with self._lock:
            while filled < count and self._q:
                head = self._q[0]
                take = min(head.size, count - filled)
                out[filled:filled + take] = head[:take]
                filled += take
                if take == head.size:
                    self._q.popleft()
                else:
                    self._q[0] = head[take:]
                self._n -= take
        return out


class Ducker:
    """بيقول «اكتم» وقت ما الـAI بيتكلم ولفترة قصيرة بعدها (ذيل الصوت + تأخير الالتقاط)."""

    def __init__(self, threshold: float = 0.01, hangover: float = 0.35):
        self.threshold, self.hangover = threshold, hangover
        self.active = False
        self._until = 0.0

    def update(self, peak: float, now: float) -> bool:
        if peak >= self.threshold:
            self._until = now + self.hangover
        self.active = now < self._until
        return self.active


def meter(level: float) -> float:
    """مستوى RMS ← 0..1 على مقياس dB (‎-60 → 0) عشان العداد يتحرك مع الكلام الهادي."""
    db = 20 * np.log10(max(level, 1e-5))
    return float(np.clip((db + 60) / 60, 0.0, 1.0))


# ---------------------------------------------------------------------------
# أجهزة الصوت
# ---------------------------------------------------------------------------

def _sd():
    import sounddevice as sd
    return sd


def _wasapi() -> dict:
    for api in _sd().query_hostapis():
        if api["name"] == "Windows WASAPI":
            return api
    raise BridgeError("واجهة الصوت WASAPI مش متاحة على الجهاز.")


def _wasapi_devices(kind: str) -> list[tuple[int, str]]:
    sd = _sd()
    key = "max_input_channels" if kind == "input" else "max_output_channels"
    out = []
    for idx in _wasapi()["devices"]:
        d = sd.query_devices(idx)
        if d[key] > 0:
            out.append((idx, d["name"]))
    return out


def _default_input() -> int:
    return _wasapi()["default_input_device"]


def is_cable(name: str) -> bool:
    return CABLE_WORD in name


def list_bridge_mics() -> list[str]:
    try:
        return [name for _, name in _wasapi_devices("input") if not is_cable(name)]
    except Exception:
        log.exception("تعذر قراءة مايكات الجسر")
        return []


def pick_mic(wanted: str = "") -> tuple[int, str]:
    """المايك الحقيقي؛ عمره ما يرجع الكابل نفسه (وإلا صوت الكابل يرجع لنفسه)."""
    if is_cable(wanted):
        raise BridgeError("مايك الجسر لازم يكون مايك حقيقي مش الكابل، وإلا هيحصل صدى.")
    devices = [(i, n) for i, n in _wasapi_devices("input") if not is_cable(n)]
    if wanted:
        for idx, name in devices:
            if wanted.lower() in name.lower() or name.lower() in wanted.lower():
                return idx, name
        raise BridgeError(f"المايك «{wanted}» مش متوصل. اختار مايك تاني من الإعدادات.")
    default = _default_input()
    for idx, name in devices:
        if idx == default:
            return idx, name
    if devices:
        return devices[0]
    raise BridgeError("مفيش مايك حقيقي متوصل.")


def pick_output(wanted: str = DEFAULT_OUTPUT) -> tuple[int, str]:
    for idx, name in _wasapi_devices("output"):
        if wanted.lower() in name.lower():
            return idx, name
    raise BridgeError(f"مش لاقي «{wanted}». اتأكد إن VB-Audio Virtual Cable متسطب.")


# ---------------------------------------------------------------------------
# العمليات
# ---------------------------------------------------------------------------

class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", DWORD), ("cntUsage", DWORD), ("th32ProcessID", DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", DWORD), ("cntThreads", DWORD), ("th32ParentProcessID", DWORD),
                ("pcPriClassBase", LONG), ("dwFlags", DWORD), ("szExeFile", WCHAR * 260)]


_TH32CS_SNAPPROCESS = 0x2
_INVALID_HANDLE = HANDLE(-1).value
_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.CreateToolhelp32Snapshot.restype = HANDLE
_k32.CreateToolhelp32Snapshot.argtypes = [DWORD, DWORD]
_k32.Process32FirstW.restype = BOOL
_k32.Process32FirstW.argtypes = [HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
_k32.Process32NextW.restype = BOOL
_k32.Process32NextW.argtypes = [HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
_k32.CloseHandle.argtypes = [HANDLE]


def _processes() -> list[tuple[int, int, str]]:
    """(pid, parent, exe) لكل العمليات. Toolhelp أسرع بكتير من psutil.process_iter (اللي خد ~8 ث على الجهاز ده)."""
    snap = _k32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if not snap or snap == _INVALID_HANDLE:
        return []
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        out = []
        ok = _k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            out.append((entry.th32ProcessID, entry.th32ParentProcessID, entry.szExeFile))
            ok = _k32.Process32NextW(snap, ctypes.byref(entry))
        return out
    finally:
        _k32.CloseHandle(snap)


def process_map() -> dict[int, tuple[int, str]]:
    return {pid: (parent, name) for pid, parent, name in _processes()}


def find_app_pid(exe: str = DEFAULT_APP) -> int | None:
    """أول عملية رئيسية للبرنامج (مش عملية تابعة لنفس البرنامج)."""
    procs = [(pid, parent) for pid, parent, name in _processes() if name.lower() == exe.lower()]
    pids = {pid for pid, _ in procs}
    roots = [pid for pid, parent in procs if parent not in pids]
    return roots[0] if roots else None


def owner_names(pid: int, procs: dict[int, tuple[int, str]]) -> set[str]:
    """اسم العملية وأسماء كل آبائها (جلسة صوت لعملية تابعة لـTeams تتحسب Teams)."""
    names: set[str] = set()
    seen: set[int] = set()
    while pid in procs and pid not in seen:
        seen.add(pid)
        parent, name = procs[pid]
        names.add(name.lower())
        pid = parent
    return names


def app_title(exe: str) -> str:
    return {"ms-teams.exe": "Teams", "chatgpt.exe": "ChatGPT"}.get(exe.lower(), exe)


def choose_meeting_device(sessions, procs, app_exe: str = DEFAULT_APP) -> tuple[str, str] | None:
    """الجهاز اللي البرنامج (Teams) بيشغّل عليه صوت: الجلسة الشغالة الأول، بعدين أي جلسة. الكابل مرفوض."""
    exe = app_exe.lower()
    mine = [s for s in sessions if exe in owner_names(s.pid, procs)]
    active = [s for s in mine if s.active]
    for s in active:
        if not is_cable(s.device_name):
            return s.device_id, s.device_name
    if active:
        title = app_title(app_exe)
        raise BridgeError(f"سمّاعة {title} متوجهة للكابل الوهمي. رجّعها للهيدفون من إعدادات {title} "
                          "(Settings ← Devices ← Speaker)، والجسر هو اللي هيوصّل الصوت لـChatGPT.")
    for s in mine:
        if not is_cable(s.device_name):
            return s.device_id, s.device_name
    return None


# ---------------------------------------------------------------------------
# الجسر
# ---------------------------------------------------------------------------

class MeetingBridge:
    def __init__(self, mic_name: str = "", app_exe: str = DEFAULT_APP, ai_exe: str = DEFAULT_AI_APP,
                 output_name: str = DEFAULT_OUTPUT, device_id: str | None = None,
                 app_gain: float = 1.0, mic_gain: float = 1.0):
        self.mic_name, self.app_exe, self.ai_exe, self.output_name = mic_name, app_exe, ai_exe, output_name
        self.device_id = device_id
        self._fixed_device = device_id is not None
        self.app_gain, self.mic_gain = app_gain, mic_gain
        self.app_ring, self.mic_ring = Ring(), Ring()
        self.ducker = Ducker()
        self.out_level = 0.0
        self.mic_label = self.output_label = self.device_label = ""
        self.error: str | None = None
        self._gain = app_gain
        self._loop: loopback.EndpointLoopback | None = None
        self._mic_stream = None
        self._out_stream = None
        self._monitor: threading.Thread | None = None
        self._stop = threading.Event()

    @property
    def running(self) -> bool:
        return self._out_stream is not None

    @property
    def title(self) -> str:
        return app_title(self.app_exe)

    def status_text(self) -> str:
        return f"بسمع {self.title} من «{self.device_label}» + مايكك «{self.mic_label}»"

    def app_running(self) -> bool:
        return find_app_pid(self.app_exe) is not None

    def _resolve_device(self) -> tuple[str, str]:
        found = choose_meeting_device(loopback.render_sessions(), process_map(), self.app_exe)
        if found:
            return found
        dev_id, name = loopback.default_render_device()
        if is_cable(name):
            raise BridgeError("جهاز الصوت الافتراضي في Windows هو الكابل الوهمي. خلّيه الهيدفون.")
        return dev_id, name

    def start(self) -> None:
        if self.running:
            return
        sd = _sd()
        mic = pick_mic(self.mic_name)
        out = pick_output(self.output_name)
        if not self._fixed_device:
            if not self.app_running():
                raise BridgeError(f"{self.title} مش مفتوح. افتح {self.title} الأول وبعدين شغّل الجسر.")
            self.device_id, self.device_label = loopback.run_in_mta(self._resolve_device)
        else:
            self.device_label = loopback.device_name(self.device_id)
        settings = sd.WasapiSettings(auto_convert=True)
        try:
            self._attach(self.device_id, self.device_label)
            self._mic_stream = sd.InputStream(device=mic[0], samplerate=RATE, channels=1, dtype="float32",
                                              blocksize=BLOCK, latency="low", extra_settings=settings,
                                              callback=self._on_mic)
            self._out_stream = sd.OutputStream(device=out[0], samplerate=RATE, channels=2, dtype="float32",
                                               blocksize=BLOCK, latency="low", extra_settings=settings,
                                               callback=self._on_out)
            self._mic_stream.start()
            self._out_stream.start()
        except loopback.LoopbackError as e:
            self.stop()
            raise BridgeError(f"تعذر التقاط صوت الميتينج: {e}") from e
        except Exception as e:
            self.stop()
            raise BridgeError(f"تعذر فتح أجهزة الصوت: {e}") from e
        self.mic_label, self.output_label = mic[1], out[1]
        self._stop.clear()
        self._monitor = threading.Thread(target=self._monitor_run, name="bridge-monitor", daemon=True)
        self._monitor.start()
        log.info("بدأ جسر الميتينج: device=%s mic=%s out=%s", self.device_label, mic[1], out[1])

    def _attach(self, device_id: str, label: str) -> None:
        new = loopback.EndpointLoopback(device_id, self.app_ring.push)
        new.start()
        old, self._loop = self._loop, new
        self.device_id, self.device_label = device_id, label
        if old:
            old.stop()

    def _monitor_run(self) -> None:
        """كل 20ms: هل ChatGPT بيتكلم على نفس الجهاز؟ كل ثانية: هل Teams غيّر جهاز الصوت؟"""
        comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        ai_sessions: list = []
        try:
            next_scan = 0.0
            ai = self.ai_exe.lower()
            while not self._stop.wait(METER_SECONDS):
                now = time.monotonic()
                if now >= next_scan:
                    next_scan = now + SCAN_SECONDS
                    ai_sessions = []
                    sessions = loopback.render_sessions(with_meters=True)
                    procs = process_map()
                    if not self._fixed_device:
                        try:
                            found = choose_meeting_device(sessions, procs, self.app_exe)
                        except BridgeError as e:
                            self.error = str(e)
                            return
                        if found and found[0] != self.device_id:
                            log.info("Teams غيّر جهاز الصوت إلى %s", found[1])
                            self._attach(*found)
                    ai_sessions = [s for s in sessions
                                   if s.device_id == self.device_id and ai in owner_names(s.pid, procs)]
                    sessions = None
                peak = max((s.peak() for s in ai_sessions), default=0.0)
                was = self.ducker.active
                if self.ducker.update(peak, now) and not was:
                    self.app_ring.clear()  # نرمي أي صوت للـAI اتلقط قبل ما نلاحظ
        except Exception as e:
            log.exception("خطأ في مراقبة جسر الميتينج")
            self.error = f"وقف اكتشاف أجهزة الصوت: {e}"
        finally:
            ai_sessions = []
            comtypes.CoUninitialize()

    def problem(self) -> str | None:
        """None = تمام، غير كده = رسالة خطأ توقف الجسر."""
        if not self.running:
            return None
        if self.error:
            return self.error
        if self._loop and self._loop.error:
            return f"وقف التقاط صوت الميتينج: {self._loop.error}"
        if self._mic_stream is not None and not self._mic_stream.active:
            return "المايك اتفصل، فالجسر وقف."
        if not self._out_stream.active:
            return "الكابل الوهمي اتفصل، فالجسر وقف."
        return None

    def _on_mic(self, indata, frames, time_info, status):
        self.mic_ring.push(indata[:, 0].copy())

    def _on_out(self, outdata, frames, time_info, status):
        target = 0.0 if self.ducker.active else self.app_gain
        app = self.app_ring.pull(frames)
        if target != self._gain:  # تدرّج 10ms بدل طقطقة
            app *= np.linspace(self._gain, target, frames, dtype=np.float32)
            self._gain = target
        elif target != 1.0:
            app *= target
        mix = app + self.mic_ring.pull(frames) * self.mic_gain
        np.tanh(mix, out=mix)  # soft clip لو الاتنين اتكلموا مع بعض
        self.out_level = max(self.out_level * 0.9, float(np.sqrt(np.mean(mix * mix))))
        outdata[:, 0] = mix
        outdata[:, 1] = mix

    def stop(self) -> None:
        self._stop.set()
        if self._monitor:
            self._monitor.join(2)
            self._monitor = None
        streams, self._out_stream, self._mic_stream = (self._out_stream, self._mic_stream), None, None
        for s in streams:
            if s is None:
                continue
            try:
                s.stop()
                s.close()
            except Exception:
                log.exception("خطأ عند إغلاق جهاز صوت الجسر")
        loop, self._loop = self._loop, None
        if loop:
            loop.stop()
        self.app_ring.level = self.mic_ring.level = self.out_level = 0.0
