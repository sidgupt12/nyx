import unittest

from nyx.notifications import MacOSNotifier, _parse_alerter_action


class MacOSNotifierTests(unittest.TestCase):
    def test_non_macos_is_a_noop(self):
        calls = []
        notifier = MacOSNotifier(system_name="Linux", runner=lambda *args, **kwargs: calls.append(args))
        notifier.notify("Title", "Message")
        self.assertEqual(calls, [])

    def test_macos_uses_osascript_and_escapes_text(self):
        calls = []

        def runner(*args, **kwargs):
            calls.append((args, kwargs))

        notifier = MacOSNotifier(system_name="Darwin", runner=runner)
        notifier.notify('Agent "demo"', 'Run "quoted"\nnow')

        self.assertEqual(len(calls), 1)
        args, kwargs = calls[0]
        self.assertEqual(args[0][0:2], ["osascript", "-e"])
        self.assertIn('display notification', args[0][2])
        self.assertIn('Agent \\"demo\\"', args[0][2])
        self.assertEqual(kwargs["check"], False)

    def test_alerter_json_activation_value_maps_to_open(self):
        self.assertEqual(
            _parse_alerter_action('{"activationType":"actionClicked","activationValue":"Open Codex"}'),
            "open",
        )
