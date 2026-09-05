import socket
import unittest
from nyx.protocol import encode, receive


class ProtocolTests(unittest.TestCase):
    def test_coalesced_messages_are_not_lost(self):
        a, b = socket.socketpair()
        with a, b:
            a.sendall(encode({"wait": True}) + encode({"decision": "deny"}))
            self.assertEqual(receive(b), {"wait": True})
            self.assertEqual(receive(b), {"decision": "deny"})

    def test_bad_json_and_non_objects(self):
        for value in [b"nope\n", b"[]\n", b"null\n"]:
            a, b = socket.socketpair()
            with a, b:
                a.sendall(value)
                with self.assertRaises(ValueError):
                    receive(b)

    def test_oversized_input(self):
        a, b = socket.socketpair()
        with a, b:
            a.sendall(b"x" * 50 + b"\n")
            with self.assertRaises(ValueError):
                receive(b, 20)

    def test_eof_before_newline(self):
        a, b = socket.socketpair()
        with a, b:
            a.sendall(b"{}")
            a.shutdown(socket.SHUT_WR)
            with self.assertRaises(ValueError):
                receive(b)
