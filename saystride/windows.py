"""Windows hotkey, focus context, audio capture and safe text insertion."""
from __future__ import annotations

import ctypes
import logging
import os
import threading
import time
from ctypes import wintypes

import numpy as np
import psutil
import sounddevice as sd
import win32clipboard
import win32con
import win32gui
import win32process

LOG = logging.getLogger(__name__)
USER = ctypes.windll.user32
KERNEL = ctypes.windll.kernel32
WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x100, 0x101, 0x104, 0x105
VK_CONTROL, VK_SHIFT, VK_ESCAPE = 0x11, 0x10, 0x1B
VK_LCONTROL, VK_RCONTROL, VK_LSHIFT, VK_RSHIFT = 0xA2, 0xA3, 0xA0, 0xA1
VK_HOLD, VK_TOGGLE = 0x77, 0x78  # F8 hold-to-talk, F9 toggle-to-talk
KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x0002, 0x0004


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", INPUTUNION)]


CALLBACK = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
USER.CallNextHookEx.restype = ctypes.c_ssize_t
USER.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
USER.SetWindowsHookExW.restype = wintypes.HHOOK
USER.SetWindowsHookExW.argtypes = [ctypes.c_int, CALLBACK, wintypes.HINSTANCE, wintypes.DWORD]
KERNEL.GetModuleHandleW.restype = wintypes.HMODULE
KERNEL.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
USER.SendInput.restype = wintypes.UINT
USER.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]


def _pressed(key: int) -> bool:
    return bool(USER.GetAsyncKeyState(key) & 0x8000)


def modifiers_down() -> bool:
    return any(_pressed(key) for key in (VK_CONTROL, VK_LCONTROL, VK_RCONTROL, VK_SHIFT, VK_LSHIFT, VK_RSHIFT))


def wait_for_modifiers(timeout: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while modifiers_down() and time.monotonic() < deadline:
        time.sleep(.01)


class Hotkey:
    """F8 hold-to-talk, F9 toggle-to-talk, Esc cancel."""
    def __init__(self, on_down, on_up, on_cancel, on_error=None):
        self.on_down, self.on_up, self.on_cancel = on_down, on_up, on_cancel
        self.on_error = on_error or (lambda message: None)
        self._hook = None
        self._callback = None
        self._thread_id = 0
        self._holding = None
        self._toggle_active = False

    def start(self):
        def run():
            self._thread_id = KERNEL.GetCurrentThreadId()
            @CALLBACK
            def hook(n, wparam, lparam):
                if n < 0:
                    return USER.CallNextHookEx(self._hook, n, wparam, lparam)
                event = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if event.flags & 0x10:  # our own SendInput event
                    return USER.CallNextHookEx(self._hook, n, wparam, lparam)
                if event.vkCode == VK_ESCAPE and wparam in (WM_KEYDOWN, WM_SYSKEYDOWN) and (self._holding or self._toggle_active):
                    self.on_cancel()
                    self._holding = None
                    self._toggle_active = False
                    return 1
                if event.vkCode in (VK_HOLD, VK_TOGGLE):
                    mode = "hold" if event.vkCode == VK_HOLD else "toggle"
                    if wparam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                        if not self._holding:
                            self._holding = mode
                            self._toggle_active = mode == "toggle"
                            self.on_down(mode)
                        return 1
                    if wparam in (WM_KEYUP, WM_SYSKEYUP) and self._holding == "hold":
                        self._holding = None
                        self.on_up()
                        return 1
                    if wparam in (WM_KEYUP, WM_SYSKEYUP) and self._holding == "toggle":
                        self._holding = None
                        return 1
                    if self._holding == "toggle":
                        return 1
                return USER.CallNextHookEx(self._hook, n, wparam, lparam)
            self._callback = hook
            self._hook = USER.SetWindowsHookExW(WH_KEYBOARD_LL, hook, KERNEL.GetModuleHandleW(None), 0)
            if not self._hook:
                error = f"Could not install dictation hotkey: Windows error {KERNEL.GetLastError()}"
                LOG.error(error)
                self.on_error(error)
                return
            msg = wintypes.MSG()
            while USER.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                USER.TranslateMessage(ctypes.byref(msg))
                USER.DispatchMessageW(ctypes.byref(msg))
            USER.UnhookWindowsHookEx(self._hook)
        threading.Thread(target=run, name="saystride-hotkey", daemon=True).start()

    def stop(self):
        if self._thread_id:
            USER.PostThreadMessageW(self._thread_id, 0x12, 0, 0)  # WM_QUIT


class Recorder:
    """16 kHz mono mic. Keep the stream warm for 20 seconds after each dictation."""
    def __init__(self, device=None, on_level=None, on_audio=None):
        self.device = device
        self.on_level = on_level or (lambda level: None)
        self.on_audio = on_audio or (lambda samples: None)
        self.stream = None
        self.chunks = []
        self.lock = threading.Lock()
        self.recording = False
        self.last_stop = 0.0
        self._timer = None

    def _callback(self, data, frames, timing, status):
        if status:
            LOG.warning("Microphone: %s", status)
        samples = data[:, 0].copy()
        with self.lock:
            if self.recording:
                self.chunks.append(samples)
        if self.recording:
            self.on_level(float(np.max(np.abs(samples))) if len(samples) else 0)
            self.on_audio(samples)

    def start(self):
        if self._timer:
            self._timer.cancel()
        if self.stream is None:
            self.stream = sd.InputStream(samplerate=16000, channels=1, dtype="float32", device=self.device,
                                         blocksize=1600, callback=self._callback)
            self.stream.start()
        with self.lock:
            self.chunks = []
            self.recording = True

    def snapshot(self) -> np.ndarray:
        with self.lock:
            return np.concatenate(self.chunks) if self.chunks else np.empty(0, dtype=np.float32)

    def stop(self) -> np.ndarray:
        with self.lock:
            self.recording = False
            samples = np.concatenate(self.chunks) if self.chunks else np.empty(0, dtype=np.float32)
        self.last_stop = time.monotonic()
        self._timer = threading.Timer(20, self.close_if_idle)
        self._timer.daemon = True
        self._timer.start()
        return samples

    def close_if_idle(self):
        if not self.recording and time.monotonic() - self.last_stop >= 20:
            self.close()

    def close(self):
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None


def foreground() -> tuple[int, str, str]:
    hwnd = win32gui.GetForegroundWindow()
    title = win32gui.GetWindowText(hwnd) if hwnd else ""
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        name = psutil.Process(pid).name()
    except (psutil.Error, OSError):
        name = ""
    return hwnd, title, name


def field_context() -> tuple[str, str]:
    """Read at most 160 chars before and 100 after the insertion point."""
    try:
        import comtypes
        import comtypes.client
        comtypes.CoInitialize()
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient as UIA
        ui = comtypes.client.CreateObject(UIA.CUIAutomation, interface=UIA.IUIAutomation)
        element = ui.GetFocusedElement()
        if element is None or element.CurrentIsPassword:
            return "", ""
        pattern = element.GetCurrentPattern(UIA.UIA_TextPatternId).QueryInterface(UIA.IUIAutomationTextPattern)
        selection = pattern.GetSelection()
        if not selection or selection.Length == 0:
            return "", ""
        caret = selection.GetElement(0)
        before = caret.Clone()
        before.MoveEndpointByUnit(UIA.TextPatternRangeEndpoint_Start, UIA.TextUnit_Character, -160)
        after = caret.Clone()
        after.MoveEndpointByUnit(UIA.TextPatternRangeEndpoint_End, UIA.TextUnit_Character, 100)
        return before.GetText(-1)[-160:], after.GetText(-1)[:100]
    except Exception as exc:
        LOG.debug("Field context unavailable: %s", exc)
        return "", ""


def _key(vk: int, up: bool = False, unicode: bool = False) -> INPUT:
    flags = (KEYEVENTF_UNICODE if unicode else 0) | (KEYEVENTF_KEYUP if up else 0)
    return INPUT(1, INPUTUNION(KEYBDINPUT(0 if unicode else vk, vk if unicode else 0, flags, 0, 0)))


def _send(events: list[INPUT]) -> bool:
    array = (INPUT * len(events))(*events)
    return USER.SendInput(len(events), array, ctypes.sizeof(INPUT)) == len(events)


def _unicode_type(text: str) -> bool:
    events = []
    encoded = text.encode("utf-16-le", errors="surrogatepass")
    for i in range(0, len(encoded), 2):
        unit = encoded[i] | encoded[i + 1] << 8
        events.extend((_key(unit, unicode=True), _key(unit, up=True, unicode=True)))
    return all(_send(events[i:i + 100]) for i in range(0, len(events), 100))


def _focus_target(target: int) -> bool:
    if not target or not win32gui.IsWindow(target):
        return False
    if win32gui.GetForegroundWindow() == target:
        return True
    try:
        current = win32gui.GetForegroundWindow()
        current_thread = win32process.GetWindowThreadProcessId(current)[0] if current else 0
        target_thread = win32process.GetWindowThreadProcessId(target)[0]
        if current_thread and target_thread and current_thread != target_thread:
            USER.AttachThreadInput(current_thread, target_thread, True)
        USER.AllowSetForegroundWindow(-1)
        win32gui.ShowWindow(target, win32con.SW_SHOWNOACTIVATE)
        win32gui.BringWindowToTop(target)
        win32gui.SetForegroundWindow(target)
        time.sleep(.08)
        if current_thread and target_thread and current_thread != target_thread:
            USER.AttachThreadInput(current_thread, target_thread, False)
    except Exception:
        return False
    return win32gui.GetForegroundWindow() == target


def replace_live(target: int, previous: str, updated: str) -> bool:
    """Replace only text that still exactly precedes the caret in a readable control."""
    if not _focus_target(target):
        return False
    if previous:
        try:
            import comtypes
            import comtypes.client
            comtypes.CoInitialize()
            comtypes.client.GetModule("UIAutomationCore.dll")
            from comtypes.gen import UIAutomationClient as UIA
            ui = comtypes.client.CreateObject(UIA.CUIAutomation, interface=UIA.IUIAutomation)
            element = ui.GetFocusedElement()
            if element is None or element.CurrentIsPassword:
                return False
            pattern = element.GetCurrentPattern(UIA.UIA_TextPatternId).QueryInterface(UIA.IUIAutomationTextPattern)
            selection = pattern.GetSelection()
            if not selection or selection.Length == 0:
                return False
            span = selection.GetElement(0).Clone()
            span.MoveEndpointByUnit(UIA.TextPatternRangeEndpoint_Start, UIA.TextUnit_Character, -len(previous))
            if span.GetText(-1).replace("\r\n", "\n").replace("\r", "\n") != previous:
                return False
            span.Select()
        except Exception as exc:
            LOG.debug("Live text selection unavailable: %s", exc)
            return False
    if not updated:
        return _send([_key(win32con.VK_BACK), _key(win32con.VK_BACK, up=True)])
    return paste(updated, target)


def replace_append_live(target: int, previous: str, updated: str) -> bool:
    """Replace text SayStride typed into a console, without touching other input."""
    if not _focus_target(target):
        return False
    if previous:
        events = []
        for _ in previous:
            events.extend((_key(win32con.VK_BACK), _key(win32con.VK_BACK, up=True)))
        if not _send(events):
            return False
    return _unicode_type(updated) if updated else True


def revise_append_live(target: int, previous: str, updated: str) -> bool:
    """Rewrite only the changed complete-word suffix of console live text."""
    common = os.path.commonprefix((previous, updated))
    if common and not common.endswith(" "):
        common = common[:common.rfind(" ") + 1]
    return replace_append_live(target, previous[len(common):], updated[len(common):])


def paste(text: str, target: int) -> bool:
    """Paste Unicode text into the original window."""
    if not text or not _focus_target(target):
        return False
    use_clipboard = False
    try:
        win32clipboard.OpenClipboard()
        # Word and modern editors reliably accept Unicode clipboard paste; Unicode
        # SendInput is frequently ignored.
        use_clipboard = True
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(text, win32con.CF_UNICODETEXT)
    except Exception:
        use_clipboard = False
    finally:
        try:
            win32clipboard.CloseClipboard()
        except Exception:
            pass
    if not use_clipboard:
        return _unicode_type(text)
    sent = _send([_key(VK_CONTROL), _key(ord("V")), _key(ord("V"), up=True), _key(VK_CONTROL, up=True)])
    # Keep the final text available for Ctrl+V recovery if the target editor rejects it.
    time.sleep(.25)
    return sent
