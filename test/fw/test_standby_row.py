"""system.c's two questions on stock's Standby page (docs/internals.md#standby-limit and #charge-limit): the page's
row click, stock's, comes to ringnav_standby_row first. The click that would turn Auto-standby or Auto-shutdown on
asks in a confirm before stock's handler has changed anything, and only the confirm's second cell lets that handler
run. Auto-shutdown's asks only while Charge Limit is on; and the confirm's second line (playlists.c confirm_over)."""
import ctypes as C

import pytest

from test_nav_painters import Ui

CONFIRM = b'dialog/confirminfo_dialog'
ASK = ((b'Standby may leave the Q2 unresponsive.', b'Use Auto-shutdown instead. Enable Standby?'),
       (b'Charge Limit is not enforced while the Q2 is off.', b'Enable Auto-shutdown?'))  # by row
ACCEPT = 10  # the confirm's second cell; Return, and a hold that cancels the prompt, quit it with 4
SAVES = ('write_int_config', 'stock_write_config_trampoline', 'toolsWriteConfig', 'reset_poweroptions_timer')
# A switch off, and the click on its row that would turn it on: (row, Auto-standby, Auto-shutdown's mode), then both
# switches once stock's handler has run. 0xff: a negative mode is off too, as stock's handler reads the byte.
TURNED_ON = [((0, 0, 0), (1, 0)), ((0, 0, 3), (1, 0)), ((1, 0, 0), (0, 1)), ((1, 1, 0), (0, 1)), ((1, 1, 0xff), (0, 1))]


class Standby:
    """Stock's Standby page as the hook meets it: its rows' buttons named by their index (0 Auto-standby, 1
    Auto-shutdown, then its times); stock's two switches, Auto-standby (g_standbyflag) and Auto-shutdown's mode
    (g_savepower_mode), together in state; stock's handler (clicks: what it was given), which flips row 0's or row 1's
    switch as on the Q2, turning the other off as one turns on; and the confirm (asked: what it showed), answering
    answer."""
    def __init__(self, fw, st, standby=0, mode=0, answer=ACCEPT, limit=1):
        self.fw, self.st, self.answer = fw, st, answer
        st('charge_limit', C.c_int).value = limit
        self.ui = Ui(fw)
        self.ui.window('home_page')
        self.page = self.ui.window('standby_page')
        view = self.ui.add('scroll_view', self.page, name='scroll_view_standby')
        self.rows = [self.ui.add('button', self.ui.add('list_item', view, name=str(k)), name=str(k)) for k in range(6)]
        self.flag, self.mode = fw.var('g_standbyflag', C.c_ubyte), fw.var('g_savepower_mode', C.c_ubyte)
        self.flag.value, self.mode.value = standby, mode
        self.asked: list = []
        self.clicks: list = []
        fw.mock('navigator_to_with_context', self.confirm)
        fw.mock('stock_standby_row_trampoline', self.stock)

    @property
    def state(self):
        return self.flag.value, self.mode.value

    def confirm(self, name, prompt, *_):
        kind = C.c_int.from_address(prompt).value
        lines = (C.string_at(prompt + 8), C.string_at(prompt + 0x208)) if kind == 2 else (C.string_at(prompt + 8),)
        # what stock's handler had been given, and both switches, when it asked: nothing yet, and as they were
        self.asked.append((C.string_at(name), kind, lines, self.st('confirm_verb', C.c_char_p).value, len(self.clicks), self.state))
        return self.answer(self) if callable(self.answer) else self.answer

    def stock(self, button, event, *_):
        self.clicks.append((button, event))
        if button == self.rows[0].addr:
            self.flag.value ^= 1
            self.mode.value = 0 if self.flag.value else self.mode.value
        elif button == self.rows[1].addr:
            self.mode.value = 0 if 0 < self.mode.value < 128 else 1
            self.flag.value = 0 if self.mode.value else self.flag.value
        return 9

    def click(self, row, event=0x77):
        return self.fw.fn('ringnav_standby_row', C.c_int, C.c_void_p, C.c_void_p)(self.rows[row].addr, event)


@pytest.mark.parametrize('was,now', TURNED_ON)
def test_turning_a_switch_on_asks_before_stock_has_changed_anything(fw, st, was, now):
    row, standby, mode = was
    page = Standby(fw, st, standby, mode)
    assert page.click(row) == 9
    assert page.asked == [(CONFIRM, 2, ASK[row], b'Accept', 0, (standby, mode))]  # stock's two-line prompt, its second cell Accept
    assert page.clicks == [(page.rows[row].addr, 0x77)]  # then stock's own turning on, once, with what the click gave
    assert page.state == now  # the switch on and the other one off: all the handler's doing
    assert st('confirm_verb', C.c_void_p).value is None  # the verb is the prompt's alone


def test_with_charge_limit_off_only_auto_shutdowns_click_asks_nothing(fw, st):
    page = Standby(fw, st, limit=0)
    assert page.click(1) == 9
    assert page.asked == [] and page.clicks == [(page.rows[1].addr, 0x77)]  # stock's handler, once, with the click's button
    assert page.state == (0, 1)  # on, as before
    page = Standby(fw, st, limit=0)
    assert page.click(0) == 9 and len(page.asked) == 1 and page.asked[0][2] == ASK[0]  # Auto-standby's asks all the same


@pytest.mark.parametrize('answer', [0, 4, 9])  # 4: Return, and Hold Return's cancel; any answer but the second cell's
@pytest.mark.parametrize('was', [was for was, _ in TURNED_ON])
def test_any_other_answer_leaves_both_switches_as_they_were(fw, st, answer, was):
    row, standby, mode = was
    page = Standby(fw, st, standby, mode, answer)
    assert page.click(row) == 0
    assert len(page.asked) == 1 and page.clicks == []  # asked, and stock's handler never ran
    assert page.state == (standby, mode)
    for name in SAVES:
        assert not fw.calls(name), name  # nothing saved, no idle counter reloaded


def test_cancel_keeps_the_other_switch_on(fw, st):
    page = Standby(fw, st, standby=1, answer=4)
    page.click(1)
    assert page.state == (1, 0) and page.clicks == []  # Auto-shutdown's question cancelled: Auto-standby still on
    page = Standby(fw, st, mode=3, answer=4)
    page.click(0)
    assert page.state == (0, 3) and page.clicks == []  # Auto-standby's cancelled: Auto-shutdown still on, at its time


@pytest.mark.parametrize('row,standby,mode', [(0, 1, 0), (1, 0, 1), (1, 0, 4), (2, 0, 1), (3, 0, 2), (4, 0, 3), (5, 0, 4)])
def test_no_other_click_asks(fw, st, row, standby, mode):
    """A switch turned off, and Auto-shutdown's times (rows 2 on, there while it is on): stock's at once."""
    page = Standby(fw, st, standby, mode)
    assert page.click(row) == 9
    assert page.asked == [] and page.clicks == [(page.rows[row].addr, 0x77)]


@pytest.mark.parametrize('row', [0, 1])
@pytest.mark.parametrize('name', ['', '00', '10', '0 ', '1 ', 'x'])
def test_a_button_that_is_not_a_switchs_row_is_stocks(fw, st, row, name):
    page = Standby(fw, st)
    page.rows[row].name = name
    assert page.click(row) == 9 and page.asked == [] and len(page.clicks) == 1


@pytest.mark.parametrize('row', [0, 1])
def test_each_click_asks_once_and_keeps_nothing(fw, st, row):
    page = Standby(fw, st, answer=0)
    page.click(row)
    page.click(row)
    assert len(page.asked) == 2 and page.clicks == [] and page.state == (0, 0)  # cancelled twice: asked twice, nothing kept
    page.answer = ACCEPT
    page.click(row)
    assert len(page.asked) == 3 and len(page.clicks) == 1 and page.state[row] == 1  # accepted: on, once
    page.click(row)
    assert len(page.asked) == 3 and len(page.clicks) == 2 and page.state == (0, 0)  # turning it off asks nothing


def test_each_switch_asks_its_own_question_and_turns_the_other_off(fw, st):
    page = Standby(fw, st, mode=3)
    page.click(0)
    assert page.state == (1, 0)  # Auto-standby on, Auto-shutdown off: stock's
    page.click(1)
    assert page.state == (0, 1)  # and the opposite
    assert [asked[2] for asked in page.asked] == [ASK[0], ASK[1]] and len(page.clicks) == 2


@pytest.mark.parametrize('row', [0, 1])
def test_a_click_that_got_under_the_prompt_cant_turn_it_on_twice(fw, st, row):
    """The prompt is modal, so on the Q2 a second press lands on it (on Cancel, in front). Were one to reach the row
    anyway and both prompts be accepted, stock's handler still runs once: the outer finds the switch on."""
    def again(page):
        if len(page.asked) == 1:
            page.click(row)
        return ACCEPT
    page = Standby(fw, st, answer=again)
    assert page.click(row) == 0
    assert len(page.asked) == 2 and len(page.clicks) == 1 and page.state[row] == 1  # not flipped back off


@pytest.mark.parametrize('row', [0, 1])
def test_accept_does_nothing_once_the_page_has_gone(fw, st, row):
    """The prompt's loop ran in between: stock's handler reads the button, so not after its page was destroyed."""
    def gone(page):
        page.ui.destroy(page.page.addr)
        return ACCEPT
    page = Standby(fw, st, answer=gone)
    assert page.click(row) == 0 and page.clicks == [] and page.state == (0, 0)


# confirm_over's second line
def confirm(fw, text, answer=7):
    seen: list = []
    fw.mock('navigator_to_with_context', lambda name, prompt, *_: seen.append((C.c_int.from_address(prompt).value, C.string_at(prompt + 8), C.string_at(prompt + 0x208))) or answer)
    assert fw.fn('confirm_over', C.c_int, C.c_char_p, C.c_char_p, C.c_int)(text, b'Verb', 0) == answer
    return seen[0]


def test_a_newline_makes_the_confirm_stocks_two_line_prompt(fw):
    assert confirm(fw, b'One line?\nAnd its second') == (2, b'One line?', b'And its second')
    assert confirm(fw, b'\nOnly a second') == (2, b'', b'Only a second')


def test_a_confirm_without_one_is_as_it_was(fw):
    assert confirm(fw, b'Restart to apply?') == (1, b'Restart to apply?', b'')
    long = b'x' * 0x3ff
    assert confirm(fw, long + b'yz')[:2] == (1, long)  # one line: the whole room, as before


def test_each_line_of_a_two_line_confirm_keeps_to_its_own_room(fw):
    first, second = b'a' * 0x1ff, b'b' * 0x300
    assert confirm(fw, first + b'\n' + second) == (2, first, second[:0x1ff])  # the second cut at its room, 0x1ff and a 0
    kind, text, _ = confirm(fw, first + b'a\n' + second)  # a first line over stock's room for it: one line, as before
    assert kind == 1 and text == (first + b'a\n' + second)[:0x3ff]
