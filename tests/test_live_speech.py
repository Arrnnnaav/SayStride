import unittest
import threading
import queue
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from saystride.models import Speech
from saystride.app import App


class LiveSpeechTest(unittest.TestCase):
    def test_final_recognizes_entire_recording_after_live_chunks(self):
        audio = np.ones(12 * 16000, dtype=np.float32) * .1
        seen = []
        def decode(samples):
            seen.append(len(samples))
            return "complete transcript"
        app = SimpleNamespace(state="recording", toggled=True, live_stop=threading.Event(),
                              stopped_at=0.0, _pill=Mock(), recorder=SimpleNamespace(stop=lambda: audio),
                              live_thread=None, deepgram=None, cloud_lock=threading.Lock(), cloud_buffer=None,
                              speech=SimpleNamespace(transcribe=decode), polisher=SimpleNamespace(polish=lambda raw, *a, **k: raw),
                              config={"tones": {"verbatim": ""}, "keep_clips": False}, store=SimpleNamespace(examples=lambda tone: []),
                              tone="verbatim", target_title="", before="", after="", started_at=0.0,
                              first_text_at=None, first_written_at=None, live_updates=0, live_rewrites=0,
                              events=queue.Queue(), frozen_at=8 * 16000, frozen_text="wrong earlier chunk")
        App._finish(app)
        kind, result = app.events.get(timeout=5)
        self.assertEqual(kind, "result")
        self.assertEqual(result[0], "complete transcript")
        self.assertEqual(seen, [len(audio)])

    def test_empty_live_parakeet_does_not_load_whisper(self):
        stream = SimpleNamespace(result=SimpleNamespace(text=""), accept_waveform=lambda *_: None)
        recognizer = SimpleNamespace(create_stream=lambda: stream, decode_stream=lambda *_: None)
        speech = Speech({"asr_backend": "parakeet"})
        with patch.object(speech, "_load_parakeet", return_value=recognizer), \
             patch.object(speech, "_load_whisper", side_effect=AssertionError("slow fallback")):
            self.assertEqual(speech.transcribe(np.ones(16000, dtype=np.float32), live=True), "")

    def test_failed_live_replacement_does_not_append_duplicate(self):
        app = SimpleNamespace(state="processing", pill=Mock(), live_text="rough words", target=1,
                              root=Mock(), status=Mock(), store=Mock(), tone="casual", target_app="Notepad.exe",
                              config={"dictation_provider": "local"}, _refresh_history=Mock())
        app._copy_recovery_text = lambda text: (app.root.clipboard_append(text), True)[1]
        with patch("saystride.app.wait_for_modifiers"), \
             patch("saystride.app.replace_live", return_value=False), \
             patch("saystride.app.paste") as paste:
            App._result(app, "rough words", "clean words", (2.0, None))
        paste.assert_not_called()
        app.root.clipboard_append.assert_called_once_with("clean words")
        app.store.record_diagnostic.assert_called_once()
        diagnostic = app.store.record_diagnostic.call_args.args[0]
        self.assertEqual(diagnostic["insertion"], "clipboard_fallback")
        self.assertEqual(diagnostic["error"], "insertion_failed")
        self.assertNotIn("Pasted", app.status.set.call_args.args[0])

    def test_stop_during_decode_does_not_write_live_text(self):
        stop = threading.Event()
        def decode(*_, **__):
            stop.set()
            return "late partial"
        app = SimpleNamespace(live_stop=stop, state="recording", recorder=SimpleNamespace(
                              snapshot=lambda: np.ones(16000, dtype=np.float32)),
                              frozen_at=0, frozen_text="", speech=SimpleNamespace(transcribe=decode),
                              live_enabled=True, target=1, live_text="", events=Mock())
        with patch("saystride.app.replace_live") as replace:
            App._live_loop(app)
        replace.assert_not_called()

    def test_terminal_live_appends_completed_words(self):
        app = SimpleNamespace(first_text_at=None, first_written_at=None, live_text="", live_enabled=True,
                              live_append_mode=True, target=1, live_updates=0,
                              live_rewrites=0, _pill=Mock())
        with patch("saystride.app.modifiers_down", return_value=False), \
             patch("saystride.app.revise_append_live", return_value=True) as revise:
            App._show_live(app, "hello")
            App._show_live(app, "hello world")
            App._show_live(app, "hello world again")
        self.assertEqual(app.live_text, "hello world ")
        self.assertEqual([call.args[1:] for call in revise.call_args_list],
                         [("", "hello "), ("hello ", "hello world ")])

    def test_terminal_live_continues_after_interim_revision(self):
        app = SimpleNamespace(first_text_at=None, first_written_at=None, live_text="hello world ",
                              live_enabled=True, live_append_mode=True, target=1, live_updates=1,
                              live_rewrites=0, _pill=Mock())
        with patch("saystride.app.modifiers_down", return_value=False), \
             patch("saystride.app.revise_append_live", return_value=True) as revise:
            App._show_live(app, "hello there again")
        revise.assert_called_once_with(1, "hello world ", "hello there ")
        self.assertEqual(app.live_text, "hello there ")

    def test_terminal_final_replaces_typed_text_automatically(self):
        app = SimpleNamespace(state="processing", pill=Mock(), live_text="typed words ",
                              live_append_mode=True, target=1, root=Mock(), status=Mock(),
                              store=Mock(), tone="casual", target_app="WindowsTerminal.exe",
                              config={"dictation_provider": "local"},
                              _refresh_history=Mock())
        with patch("saystride.app.wait_for_modifiers"), \
             patch("saystride.app.replace_append_live", return_value=True) as replace:
            App._result(app, "typed words", "clean words", (2.0, None))
        replace.assert_called_once_with(1, "typed words ", "clean words")
        app.root.clipboard_append.assert_not_called()


if __name__ == "__main__":
    unittest.main()
