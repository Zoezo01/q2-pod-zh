"""Scan Folders' ticks from folder to folder (patch/scan_folders.c), over a model of stock's page: its folders, the
batch record's ticks, and what a scan is given."""
import ctypes as C

TREE = {'/mnt/mmc': ['/mnt/mmc/A', '/mnt/mmc/B'], '/mnt/mmc/A': ['/mnt/mmc/A/1', '/mnt/mmc/A/2'],
        '/mnt/mmc/B': ['/mnt/mmc/B/1']}


class Page:
    def __init__(self, fw, tree=None):
        self.fw, self.tree, self.path, self.rows, self.ticks = fw, tree or TREE, [], [], []
        self.deques, self.scans, self.order = {}, [], []
        fw.mock('get_specfoldersize', lambda *_: len(self.rows))
        fw.mock('get_specfolderurl', lambda i, out, n, *_: C.memmove(out, self.rows[i].encode() + b'\0', len(self.rows[i]) + 1) and 0)
        fw.mock('batch_get_selectitem', lambda i, *_: self.ticks[i])
        fw.mock('batch_set_selectitem', self.set)
        fw.mock('stock_specfolder_enter_trampoline', self.stock_enter)
        fw.mock('stock_specfolder_back_trampoline', self.stock_back)
        fw.mock('stock_specfolder_scan_trampoline', lambda *_: self.order.append('stock scan'))
        fw.mock('stock_cancelchoice_trampoline', lambda *_: self.ticks.__setitem__(slice(None), [0] * len(self.ticks)))
        fw.mock('fw__create_deque', self.create)
        fw.mock('fw__deque_push_back', lambda q, s, *_: self.deques[q].append(C.string_at(s).decode()))
        fw.mock('fw_deque_destroy', lambda q, *_: self.deques.pop(q) and 0)
        fw.mock('navigator_to_with_context', self.dialog)
        for name in ('player_stop', 'navigator_back', 'clear_specfoldervariate'):
            fw.mock(name, lambda *_, name=name: self.order.append(name))

    def list(self):
        self.rows, self.fw.var('g_allselect_flag', C.c_ubyte).value = self.tree[self.path[-1]], 0
        self.ticks = [0] * len(self.rows)

    def set(self, i, *_):  # stock's: the row's tick flips; 1 with every row ticked
        self.ticks[i] ^= 1
        return int(all(self.ticks))

    def stock_enter(self, index, *_):  # -1 lists the card; a folder with none inside is refused, its ticks kept
        index = C.c_int(index).value
        folder = '/mnt/mmc' if index < 0 else self.rows[index]
        if folder not in self.tree:
            return -1
        self.path = [*self.path, folder] if index >= 0 else [folder]
        self.list()
        return 1

    def stock_back(self, *_):  # 0 from the card's own list: the page is left
        self.path.pop()
        if self.path:
            self.list()
        return int(bool(self.path))

    def create(self, kind, *_):
        assert C.string_at(kind) == b'char*'
        self.deques[len(self.deques) + 0x100] = []
        return len(self.deques) + 0xff

    def dialog(self, name, ctx, *_):
        deque, *rest = (C.c_int * 4).from_address(ctx)
        self.scans.append((C.string_at(name), sorted(self.deques[deque]), rest))
        self.order.append('dialog')

    def enter(self, index=-1):
        return self.fw.fn('ringnav_specfolder_enter', C.c_int, C.c_int, C.c_int)(index, 0)

    def back(self):
        return self.fw.fn('ringnav_specfolder_back', C.c_int, C.c_void_p)(None)

    def scan(self):
        """What the Scan button's click scanned, or 'stock' when it was left to stock's click."""
        self.order, self.scans = [], []
        assert self.fw.fn('ringnav_specfolder_scan', C.c_int, C.c_void_p, C.c_void_p)(None, None) == 0
        if self.order == ['stock scan']:
            return 'stock'
        assert self.order == ['player_stop', 'navigator_back', 'dialog', 'clear_specfoldervariate'] and not self.deques
        (name, folders, rest), = self.scans
        assert (name, rest) == (b'dialog/updatemusic_dialog', [0, 1, 0])
        return folders

    def all(self):
        return self.fw.var('g_allselect_flag', C.c_ubyte).value


def test_ticks_under_two_folders_reach_one_scan(fw):
    page = Page(fw)
    assert page.enter() == 1 and page.scan() == 'stock'  # nothing ticked: stock's "Folder not specified"
    assert page.enter(0) == 1
    page.ticks[0] = 1  # A/1
    assert page.back() == 1 and page.ticks == [0, 0] and page.enter(1) == 1
    page.ticks[0] = 1  # B/1
    assert page.back() == 1 and page.enter(0) == 1 and page.ticks == [1, 0] and not page.all()  # A shows its tick again
    assert page.back() == 1 and page.enter(1) == 1 and page.ticks == [1] and page.all() == 1  # B's is its every row
    assert page.back() == 1 and page.scan() == ['/mnt/mmc/A/1', '/mnt/mmc/B/1']
    assert page.enter() == 1 and page.scan() == 'stock'  # the scan forgot them


def test_a_tick_taken_off_leaves_and_a_refused_folder_keeps_the_rest(fw):
    page = Page(fw)
    page.enter(), page.enter(0)
    page.ticks[:] = [1, 1]
    page.back(), page.enter(0)
    page.ticks[0] = 0
    assert page.enter(0) == -1 and page.ticks == [0, 1]  # A/1 holds no folders: stock refuses it
    page.back()
    page.ticks[1] = 1
    assert page.scan() == ['/mnt/mmc/A/2', '/mnt/mmc/B']


def test_cancel_and_leaving_the_page_forget_every_tick(fw):
    page = Page(fw)
    page.enter(), page.enter(0)
    page.ticks[0] = 1
    page.back()
    page.ticks[1] = 1
    fw.fn('ringnav_cancelchoice', C.c_int, C.c_void_p, C.c_void_p)(None, None)
    assert page.ticks == [0, 0] and page.enter(0) == 1 and page.ticks == [0, 0] and page.scan() == 'stock'
    page.enter(), page.enter(0)
    page.ticks[0] = 1
    assert page.back() == 1 and page.back() == 0  # Return from the card's own list
    assert page.enter() == 1 and page.enter(0) == 1 and page.ticks == [0, 0]


def test_many_ticks_come_and_go(fw):
    """Forty folders ticked, some taken off, then all again: the list grows past its blocks of sixteen and every
    tick still reaches the scan (the sanitizer run watches the blocks)."""
    names = [f'/mnt/mmc/{i}' for i in range(40)]
    page = Page(fw, {'/mnt/mmc': names, names[0]: [names[0] + '/in']})
    page.enter()
    page.ticks[1:] = [1] * 39
    page.enter(0), page.back()
    assert page.ticks == [0] + [1] * 39
    page.ticks[5:30] = [0] * 25
    page.enter(0), page.back()
    assert page.ticks == [0] + [1] * 4 + [0] * 25 + [1] * 10
    page.ticks[:] = [1] * 40
    assert page.scan() == sorted(names)
