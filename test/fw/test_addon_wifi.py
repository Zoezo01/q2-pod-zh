"""patch/addon_wifi.c: card apps whose q2app says wifi=1 start with the Wi-Fi link and stop once it has been
gone ADDON_WIFI_DOWN_MS; a user's Stop holds until Wi-Fi is back after being gone
ADDON_WIFI_DOWN_MS or more."""
import ctypes as C

from test_addon import MOUNTED, App, ap, card, scan

DOWN_MS = 10000
RUN = (b'/bin/sh /mnt/mmc/.vpn/run',)


class Link:
    """The Wi-Fi link (stock's networkGetConnectStatus) and the follower's poll."""
    def __init__(self, fw, world, up=0):
        self.fw, self.world, self.up, self.signal = fw, world, up, 0
        fw.mock('networkGetConnectStatus', lambda *_: self.up)
        fw.mock('get_wifisignal', lambda *_: self.signal)
        self.poll_fn = fw.fn('addon_wifi_poll', None)

    def poll(self, up=None, ms=0):
        if up is not None:
            self.up = up
        self.world.ms += ms
        self.poll_fn()

    def starts(self):
        return self.world.of('system').count(RUN)

    def stops(self):
        return sum(1 for c in self.world.of('system') if c[0].startswith(b'kill $(cat /tmp/q2-vpn)'))


def vpn_app(fw, **more):
    world = card(fw, vpn=b'name=Tunnel\nwifi=1\n', **more)
    return world, Link(fw, world)


def vpn(fw):
    return [ap(fw).a[i] for i in range(ap(fw).n) if ap(fw).a[i].id == b'vpn'][0]


def running(world, link):
    """The follower's start took: the loop's pid file is there."""
    world.files[b'/tmp/q2-vpn'] = b'1'


def test_link_up_launches(fw):
    world, link = vpn_app(fw)
    link.poll(0)
    assert link.starts() == 0
    link.poll(1, 500)
    assert link.starts() == 1 and vpn(fw).launched == 1
    running(world, link)
    for _ in range(5):
        link.poll(1, 500)
    assert link.starts() == 1


def test_the_first_link_scans_the_card(fw):
    world, link = vpn_app(fw)  # no Streaming visit, no scan: as at boot
    assert ap(fw).n == 0
    link.poll(1)
    assert link.starts() == 1


def test_a_link_before_the_card_mounts_starts_the_app_once_it_does(fw):
    """At boot Wi-Fi can connect half a minute before the card mounts: the link's scan waits for the card."""
    world, link = vpn_app(fw)
    del world.files[b'/proc/mounts']
    link.poll(1)
    link.poll(1, 5000)
    assert link.starts() == 0
    world.files[b'/proc/mounts'] = MOUNTED
    link.poll(1, 1000)
    assert link.starts() == 1 and vpn(fw).launched == 1


def test_while_the_card_is_out_the_mounts_are_read_once_a_second_then_not_at_all(fw):
    world, link = vpn_app(fw)
    del world.files[b'/proc/mounts']
    opened, real = [], world.fopen
    fw.mock('fw_fopen', lambda path, mode, *_: opened.append(C.string_at(path)) or real(path, mode))
    link.poll(1)
    for _ in range(9):
        link.poll(1, 100)  # 0.9 s more: one read
    assert opened.count(b'/proc/mounts') == 1
    link.poll(1, 100)
    assert opened.count(b'/proc/mounts') == 2
    world.files[b'/proc/mounts'] = MOUNTED
    link.poll(1, 1000)
    for _ in range(20):
        link.poll(1, 1000)
    assert opened.count(b'/proc/mounts') == 3 and link.starts() == 1


def test_a_short_drop_keeps_the_app(fw):
    world, link = vpn_app(fw)
    link.poll(1)
    running(world, link)
    link.poll(0, 100)
    link.poll(0, DOWN_MS - 101)
    link.poll(1, 500)
    assert (link.starts(), link.stops()) == (1, 0)


def test_a_long_drop_stops_once(fw):
    world, link = vpn_app(fw)
    link.poll(1)
    running(world, link)
    link.poll(0, 100)
    link.poll(0, DOWN_MS - 1)
    assert link.stops() == 0
    link.poll(0, 1)
    assert link.stops() == 1 and vpn(fw).launched == 0 and vpn(fw).wifi_off == 0
    for _ in range(3):
        link.poll(0, 1000)
    assert link.stops() == 1
    del world.files[b'/tmp/q2-vpn']  # its loop ended on the TERM
    link.poll(1, 500)  # the link back: started again
    assert link.starts() == 2


def test_a_user_stop_holds_until_wifi_is_back_after_a_long_drop(fw):
    world, link = vpn_app(fw)
    link.poll(1)
    running(world, link)
    fw.fn('addon_stop', None)()  # Stop Streaming Services
    del world.files[b'/tmp/q2-vpn']
    for _ in range(4):
        link.poll(1, 500)
    assert link.starts() == 1
    link.poll(0, 500)
    link.poll(1, 500)  # a blip: the same link, the Stop kept
    assert link.starts() == 1
    link.poll(0, 500)
    link.poll(0, DOWN_MS)
    link.poll(1, 500)  # back after 10 s gone
    assert link.starts() == 2


def test_a_blip_doesnt_scan_the_card_again(fw):
    world, link = vpn_app(fw)
    link.poll(1)
    running(world, link)
    del world.files[b'/mnt/mmc/.vpn/q2-vpn']  # a scan now would find it gone
    link.poll(0, 100)
    link.poll(1, 500)
    assert vpn(fw).present == 1


def test_an_app_that_ended_itself_stays_off_until_wifi_is_back_after_a_long_drop(fw):
    world, link = vpn_app(fw)
    link.poll(1)
    world.ms += 20000  # its start window over, no pid file: it ended (exit 0, or a sign-out)
    fw.fn('addon__poll_one', None, C.POINTER(App))(C.pointer(vpn(fw)))
    assert vpn(fw).launched == 0 and vpn(fw).wifi_off == 1
    for _ in range(4):
        link.poll(1, 500)
    assert link.starts() == 1
    link.poll(0, 500)
    link.poll(0, DOWN_MS)
    link.poll(1, 500)
    assert link.starts() == 2


def test_an_app_gone_from_the_card_since_the_scan_isnt_started(fw):
    # an App Store remove with the link up scans nothing: the follower checks the binary itself
    world, link = vpn_app(fw)
    link.poll(1)
    running(world, link)
    link.poll(0, 100)
    link.poll(0, DOWN_MS)
    del world.files[b'/tmp/q2-vpn'], world.files[b'/mnt/mmc/.vpn/q2-vpn']
    world.files[b'/proc/mounts'] = b''  # the next link's scan can't run yet: present is the old scan's
    link.poll(1, 500)
    assert link.starts() == 1 and vpn(fw).present == 0


def test_a_long_drop_during_usb_storage_stops_the_app_waiting_to_start_again(fw):
    world, link = vpn_app(fw)
    link.poll(1)
    running(world, link)
    fw.mock('sleep_ms', lambda *_: world.files.pop(b'/tmp/q2-vpn', None))
    fw.fn('addon_card_away', None)()
    assert vpn(fw).relaunch == 1
    link.poll(0, 100)
    link.poll(0, DOWN_MS)  # gone 10 s with the card away: no relaunch once it is back
    assert vpn(fw).relaunch == 0


def test_card_away_and_back_launches_once(fw):
    world, link = vpn_app(fw)
    link.poll(1)
    running(world, link)
    fw.mock('sleep_ms', lambda *_: world.files.pop(b'/tmp/q2-vpn', None))
    fw.fn('addon_card_away', None)()
    assert vpn(fw).relaunch == 1
    link.poll(1, 500)  # the follower leaves it to the relaunch
    assert link.starts() == 1
    fw.mock('navigator_window_is_exist', lambda *_: 0)  # USB mode's page gone: the card is back
    world.ms += 3000
    fw.fn('addon__poll_one', None, C.POINTER(App))(C.pointer(vpn(fw)))
    link.poll(1, 500)
    assert link.starts() == 2 and vpn(fw).launched == 1


def test_no_wifi_apps_does_nothing(fw):
    world = card(fw, foo=b'name=Foo\n')
    link = Link(fw, world)
    link.poll(1)
    link.poll(0, 500)
    link.poll(0, DOWN_MS)
    assert world.of('system') == []


def test_a_plain_app_is_not_stopped_by_a_drop(fw):
    world, link = vpn_app(fw, foo=b'name=Foo\n')
    link.poll(1)
    running(world, link)
    foo = [ap(fw).a[i] for i in range(ap(fw).n) if ap(fw).a[i].id == b'foo'][0]
    foo.launched = 1
    link.poll(0, 100)
    link.poll(0, DOWN_MS)
    assert foo.launched == 1 and link.stops() == 1


def test_a_signal_dip_while_connected_is_no_drop(fw):
    world, link = vpn_app(fw)
    link.poll(1)
    running(world, link)
    link.signal = 0  # the bars at 0 for a poll, the connect status still up
    link.poll(1, DOWN_MS + 1)
    assert (link.starts(), link.stops()) == (1, 0)


def test_an_app_store_update_doesnt_hold_the_app_off(fw):
    # the update's worker kills the app (addon_quit) and the UI's poll sees it gone, as if it ended itself;
    # addon_forget, once the new files are in place, lets the follower start it again on the same link
    world, link = vpn_app(fw)
    link.poll(1)
    world.ms += 20000
    fw.fn('addon__poll_one', None, C.POINTER(App))(C.pointer(vpn(fw)))  # its pid file gone: the update stopped it
    assert vpn(fw).wifi_off == 1
    fw.fn('addon_forget', None, C.c_char_p)(b'.vpn')
    link.poll(1, 500)
    assert vpn(fw).wifi_off == 0 and link.starts() == 2


def test_a_link_back_while_the_app_is_still_ending_waits_for_it(fw):
    # stopped after a long drop, its loop still saving on its TERM (pid file there) when the link returns:
    # run would exit at once and the app end later as if by itself; the start waits for the loop to go
    world, link = vpn_app(fw)
    link.poll(1)
    running(world, link)
    link.poll(0, 100)
    link.poll(0, DOWN_MS)
    assert link.stops() == 1
    world.files[b'/proc/1'] = b''  # the loop still running
    link.poll(1, 500)  # back; the old loop not gone yet
    assert link.starts() == 1
    del world.files[b'/tmp/q2-vpn'], world.files[b'/proc/1']  # now it is
    link.poll(1, 500)
    assert link.starts() == 2 and vpn(fw).launched == 1


def test_a_stale_pid_file_from_before_a_restart_doesnt_hold_the_start(fw):
    # demo restarted (a crash, the emulator's reboot) with /tmp kept: the old loop is gone, its pid file not
    world, link = vpn_app(fw)
    world.files[b'/tmp/q2-vpn'] = b'4242\n'  # no /proc/4242
    link.poll(1)
    assert link.starts() == 1
