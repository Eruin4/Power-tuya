import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import ceiling_dialog as app


class DialogTests(unittest.TestCase):
    def setUp(self):
        self.dialog = Mock()
        self.gtk = Mock()
        self.gtk.init_check.return_value = (True, [])
        self.gtk.MessageDialog.return_value = self.dialog
        self.gtk.ResponseType = SimpleNamespace(OK=-5, CANCEL=-6, DELETE_EVENT=-4)
        self.glib = Mock()
        self.glib.timeout_add.return_value = 42
        self.modules = patch.dict(sys.modules, {
            'gi': Mock(),
            'gi.repository': SimpleNamespace(Gtk=self.gtk, GLib=self.glib),
        })
        self.modules.start()
        self.addCleanup(self.modules.stop)

    def test_countdown_uses_elapsed_time_and_times_out_at_30_seconds(self):
        def run():
            update = self.glib.timeout_add.call_args.args[1]
            for _ in range(5):
                update()
            self.dialog.response.assert_called_once_with(5)
            return 5

        self.dialog.run.side_effect = run
        # Include a late tick: drift must not extend the 30-second deadline.
        with patch.object(app.time, 'monotonic', side_effect=[100, 100.9, 101, 102, 104.4, 130]):
            self.assertEqual(app.run_dialog(), 5)
        self.assertEqual(
            [call.args[0] for call in self.dialog.format_secondary_text.call_args_list],
            ['자동 소등까지 30초 남았습니다.', '자동 소등까지 29초 남았습니다.',
             '자동 소등까지 28초 남았습니다.', '자동 소등까지 26초 남았습니다.'],
        )
        self.glib.source_remove.assert_called_once_with(42)
        self.dialog.destroy.assert_called_once()

    def test_buttons_and_close_preserve_decisions(self):
        for response, expected in [(-5, 0), (-6, 1), (-4, 1)]:
            with self.subTest(response=response):
                self.dialog.run.return_value = response
                self.assertEqual(app.run_dialog(), expected)
        self.dialog.response.assert_not_called()

    def test_no_display_cannot_authorize_off(self):
        self.gtk.init_check.return_value = (False, [])
        self.assertEqual(app.run_dialog(), 1)
        self.gtk.MessageDialog.assert_not_called()


if __name__ == '__main__':
    unittest.main()
