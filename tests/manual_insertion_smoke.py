"""Run manually on an interactive Windows desktop: python tests/manual_insertion_smoke.py."""
import subprocess
import tempfile
import time
import tkinter as tk
from pathlib import Path

import win32con
import win32gui

from saystride.windows import _focus_target, _key, _send, field_context, foreground, paste, replace_live


def main():
    with tempfile.TemporaryDirectory(prefix="saystride-smoke-") as directory:
        path = Path(directory) / f"saystride-insertion-smoke-{Path(directory).name}.txt"
        path.write_text("", encoding="utf-8")
        process = subprocess.Popen(["notepad.exe", str(path)])
        target = 0
        root = None
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and not target:
                windows = []
                win32gui.EnumWindows(lambda hwnd, _: windows.append(hwnd), None)
                target = next((hwnd for hwnd in windows if win32gui.IsWindowVisible(hwnd)
                               and path.name in win32gui.GetWindowText(hwnd)), 0)
                time.sleep(.1)
            assert target, "Notepad window did not open"
            root = tk.Tk()
            root.withdraw()
            overlay = tk.Toplevel(root)
            overlay.withdraw()
            overlay.overrideredirect(True)
            overlay.attributes("-topmost", True)
            overlay.wm_geometry("250x50+100+100")
            overlay.update_idletasks()
            hwnd = win32gui.GetAncestor(overlay.winfo_id(), win32con.GA_ROOT)
            style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
            win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE,
                                   style | win32con.WS_EX_NOACTIVATE | win32con.WS_EX_TOOLWINDOW)
            assert _focus_target(target), "Could not focus Notepad"
            overlay.deiconify()
            root.update()
            assert foreground()[0] == target, "Overlay stole keyboard focus"
            print("target:", target, foreground())
            assert paste("SayStride insertion smoke test", target), "SendInput failed"
            time.sleep(.1)
            assert foreground()[0] == target, f"Paste changed focus: {foreground()}"
            before, _ = field_context()
            print("caret:", repr(before))
            assert "SayStride insertion smoke test" in before, "Text did not reach Notepad"
            assert replace_live(target, "SayStride insertion smoke test", "SayStride live revision"), "Live replacement failed"
            time.sleep(.1)
            assert foreground()[0] == target, f"Live replacement changed focus: {foreground()}"
            before, _ = field_context()
            print("revised:", repr(before))
            assert before.endswith("SayStride live revision"), "Live revision did not reach Notepad"
            assert replace_live(target, "SayStride live revision", "SayStride final result"), "Final replacement failed"
            time.sleep(.1)
            assert foreground()[0] == target, f"Final replacement changed focus: {foreground()}"
            before, _ = field_context()
            print("final:", repr(before))
            assert before.endswith("SayStride final result"), "Final result did not reach Notepad"
            long_text = "SayStride " * 25
            assert replace_live(target, "SayStride final result", long_text), "Long live text failed"
            assert replace_live(target, long_text, "Short final result"), "Long live text could not be revised"
            time.sleep(.1)
            before, _ = field_context()
            assert before == "Short final result", "Long live revision left text behind"
        finally:
            if root:
                root.destroy()
            if target:
                if path.name in win32gui.GetWindowText(target) and _focus_target(target):
                    _send([_key(win32con.VK_CONTROL), _key(ord("A")), _key(ord("A"), up=True), _key(win32con.VK_CONTROL, up=True)])
                    time.sleep(.1)
                    _send([_key(win32con.VK_BACK), _key(win32con.VK_BACK, up=True)])
                    time.sleep(.1)
                    _send([_key(win32con.VK_CONTROL), _key(ord("S")), _key(ord("S"), up=True), _key(win32con.VK_CONTROL, up=True)])
                    time.sleep(.3)
                    _send([_key(win32con.VK_CONTROL), _key(ord("W")), _key(ord("W"), up=True), _key(win32con.VK_CONTROL, up=True)])


if __name__ == "__main__":
    main()
