import json
import queue
import unittest
from unittest.mock import patch

import numpy as np
import websocket

from saystride.deepgram import DeepgramStream, _params


class FakeSocket:
    def __init__(self):
        self.messages = queue.Queue()
        self.binary = []

    def settimeout(self, _):
        pass

    def send(self, data, opcode=None):
        if opcode == websocket.ABNF.OPCODE_BINARY:
            self.binary.append(data)
        elif json.loads(data)["type"] == "Finalize":
            self.messages.put(json.dumps({"type": "Results", "is_final": True, "from_finalize": True,
                                          "channel": {"alternatives": [{"transcript": "spoken words"}]}}))

    def recv(self):
        try:
            return self.messages.get(timeout=.1)
        except queue.Empty:
            raise websocket.WebSocketTimeoutException()

    def close(self):
        pass


class DeepgramTest(unittest.TestCase):
    def test_dictionary_is_passed_as_separate_keyterms(self):
        params = _params({"deepgram_model": "nova-3", "deepgram_language": "en",
                          "deepgram_keyterms": True, "dictionary": ["SayStride: say stride", "AIOS: ay os"]}, live=True)
        self.assertEqual(params["keyterm"], ["SayStride", "AIOS"])

    def test_stream_sends_audio_and_collects_final_result(self):
        socket = FakeSocket()
        updates = []
        with patch("saystride.deepgram.websocket.create_connection", return_value=socket):
            stream = DeepgramStream("test-key", {"deepgram_model": "nova-3", "deepgram_language": "en"}, updates.append)
            stream.feed(np.ones(1600, dtype=np.float32) * .1)
            self.assertEqual(stream.finish(), "spoken words")
        self.assertEqual(len(socket.binary), 1)
        self.assertEqual(updates[-1], "spoken words")


if __name__ == "__main__":
    unittest.main()
