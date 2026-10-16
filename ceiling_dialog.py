"""Desktop countdown dialog; exit codes match the former zenity question."""
import math
import time


def run_dialog(seconds=30):
    # Use the system Python for the desktop's installed GTK bindings.
    import gi
    gi.require_version('Gtk', '3.0')
    from gi.repository import GLib, Gtk

    ready, _ = Gtk.init_check()
    if not ready:
        return 1

    dialog = Gtk.MessageDialog(
        title='천장등 자동 소등',
        message_type=Gtk.MessageType.QUESTION,
        buttons=Gtk.ButtonsType.NONE,
        text='PC 시작 동작이 완료되었습니다.\n켜져 있는 천장등을 끌까요?',
    )
    dialog.add_button('켜두기', Gtk.ResponseType.CANCEL)
    dialog.add_button('끄기', Gtk.ResponseType.OK)
    dialog.set_default_response(Gtk.ResponseType.OK)
    dialog.set_default_size(440, -1)
    dialog.format_secondary_text(f'자동 소등까지 {seconds}초 남았습니다.')
    dialog.show_all()
    deadline = time.monotonic() + seconds
    last_remaining = seconds

    def update():
        nonlocal last_remaining
        remaining = max(0, math.ceil(deadline - time.monotonic()))
        if remaining == 0:
            dialog.response(5)
        elif remaining != last_remaining:
            dialog.format_secondary_text(f'자동 소등까지 {remaining}초 남았습니다.')
            last_remaining = remaining
        return True

    timer = GLib.timeout_add(100, update)
    try:
        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            return 0
        return 5 if response == 5 else 1
    finally:
        GLib.source_remove(timer)
        dialog.destroy()


if __name__ == '__main__':
    raise SystemExit(run_dialog())
