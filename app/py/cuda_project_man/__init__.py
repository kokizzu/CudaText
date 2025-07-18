import os
import sys
import re
import collections
import json
import stat
import copy
import string
import time
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath
from .projman_glob import *

from cudatext import *
from cudatext_keys import *
import cudatext_cmd

from cudax_lib import get_translation
_   = get_translation(__file__)  # i18n

IS_WIN = os.name == 'nt'
IS_MAC = sys.platform == 'darwin'

S_CTRL_API = 'm' if IS_MAC else 'c'
S_CTRL_NAME = 'Meta' if IS_MAC else 'Ctrl'

ctypes = None
if IS_WIN:
    try:
        import ctypes
    except (ImportError, ModuleNotFoundError):
        pass

PROJECT_EXTENSION = ".cuda-proj"
PROJECT_DIALOG_FILTER = _("CudaText projects") + "|*" + PROJECT_EXTENSION
PROJECT_UNSAVED_NAME = _("(Unsaved project)")
PROJECT_TEMP_FILENAME = os.path.join(app_path(APP_DIR_SETTINGS), 'temporary'+PROJECT_EXTENSION)
NODE_PROJECT, NODE_DIR, NODE_FILE, NODE_BAD = range(4)
OS_SUFFIX = app_proc(PROC_GET_OS_SUFFIX, '')
DEF_SES = 'default'+OS_SUFFIX+'.cuda-session'
global_project_info = {}
sort_order = 'ext'

def is_session_name(s):
    allowed = string.ascii_letters+string.digits+'., ()-+_$%='
    for ch in s:
        if not ch in allowed:
            return False
    return True

def _file_open(fn, options=''):
    gr = ed.get_prop(PROP_INDEX_GROUP)
    #print('Opening file in group %d'%gr)
    file_open(fn, group=gr, options=options)

# don't allow spaces and punctuation in file extension
EXT_CH = string.ascii_letters + string.digits + '.-_'

def _file_ext(fn):
    _, s = os.path.splitext(fn)
    for ch in s:
        if not ch in EXT_CH:
            return ''
    return s

def _file_editor(fn):
    for h in ed_handles():
        e = Editor(h)
        if e.get_filename()==fn:
            return e
    return None

# https://stackoverflow.com/questions/377017/test-if-executable-exists-in-python
def which(program):
    def is_exe(fpath):
        return os.path.isfile(fpath) and os.access(fpath, os.X_OK)

    fpath, fname = os.path.split(program)
    if fpath:
        if is_exe(program):
            return program
    else:
        for path in os.environ["PATH"].split(os.pathsep):
            exe_file = os.path.join(path, program)
            if is_exe(exe_file):
                return exe_file

    return None

def project_variables():
    """
    gives dict with "project variables", which is ok for using from other plugins,
    e.g. ExtTools.
    add to names {} or $() if you want.
    1) predefined var ProjMainFile (defined by right-click menu in ProjMan)
    2) predefined var ProjDir (dir of .cuda-proj file)
    3) other vars are defined by user in Proj Properties dialog.
    """
    res = collections.OrderedDict()
    data = global_project_info
    res['ProjDir'] = os.path.dirname(data.get('filename', ''))

    fn = data.get('mainfile', '')
    res['ProjMainFile'] = fn
    res['ProjMainFileNameOnly'] = os.path.basename(fn)
    res['ProjMainFileNameNoExt'] = '.'.join(os.path.basename(fn).split('.')[0:-1])

    data = global_project_info.get('vars', [])
    for item in data:
        s1, s2 = item.split('=', maxsplit=1)
        res[s1] = s2
    return res

NodeInfo = collections.namedtuple("NodeInfo", "caption image")

_homedir = os.path.expanduser('~')
_appdir = os.path.dirname(app_path(APP_DIR_SETTINGS))

def collapse_macros(proj_dir, fn):
    if proj_dir and (fn+os.sep).startswith(proj_dir+os.sep):
        fn = fn.replace(proj_dir, '{ProjDir}', 1)
    if (fn+os.sep).startswith(_appdir+os.sep):
        fn = fn.replace(_appdir, '{AppDir}', 1)
    fn = fn.replace(os.sep, '/') if IS_WIN else fn
    return fn

def expand_macros(proj_dir, s):
    if proj_dir:
        s = s.replace('{ProjDir}', proj_dir, 1)
    s = s.replace('{AppDir}', _appdir, 1)
    return os.path.normpath(s)

def collapse_filename(fn):
    if (fn+'/').startswith(_homedir+'/'):
        fn = fn.replace(_homedir, '~', 1)
    return fn

def nice_filename(path):
    return os.path.basename(path) + ' ('+ collapse_filename(os.path.dirname(path)) + ')'


def is_simple_listed(name, masks):
    s = name.lower()
    for mask in masks.split(' '):
        if s.endswith(mask):
            return True
    return False

def is_mask_listed(s, masks):
    for mask in masks.split(';'):
        r = fnmatch(s, mask)
        #print("fnmatch('%s', '%s') = %d"%(s, mask, int(r)))
        if r:
            return True
    return False

# only Py 3.5 supports os.stat(s).st_file_attributes
# so this is to support Py 3.4
def is_hidden_win32(s):
    if not ctypes:
        return False
    try:
        attrs = ctypes.windll.kernel32.GetFileAttributesW(s)
        assert attrs != -1
        res = bool(attrs & 2)
    except (AttributeError, AssertionError):
        res = False
    return res

def is_hidden(s):
    if IS_WIN:
        if s=='':
            return False
        if s.endswith(':\\'):
            return False

        return is_hidden_win32(s)
        #try:
        #    return bool(os.stat(s).st_file_attributes & stat.FILE_ATTRIBUTE_HIDDEN)
        #except:
        #    return True

    else:
        return os.path.basename(s).startswith('.')

def is_win_root(s):
    return IS_WIN and s.endswith(':\\')

def is_locked(s):
    if IS_WIN:
        if s.endswith(':\\'):
            return False
        return is_hidden_win32(s)
    else:
        return not os.access(s, os.R_OK)


def _toolbar_add_btn(h_bar, hint, icon=-1, command=''):
    toolbar_proc(h_bar, TOOLBAR_ADD_ITEM)
    cnt = toolbar_proc(h_bar, TOOLBAR_GET_COUNT)
    h_btn = toolbar_proc(h_bar, TOOLBAR_GET_BUTTON_HANDLE, index=cnt-1)
    if hint=='-':
        button_proc(h_btn, BTN_SET_KIND, BTNKIND_SEP_HORZ)
    else:
        button_proc(h_btn, BTN_SET_KIND, BTNKIND_ICON_ONLY)
        button_proc(h_btn, BTN_SET_HINT, hint)
        button_proc(h_btn, BTN_SET_IMAGEINDEX, icon)
        button_proc(h_btn, BTN_SET_DATA1, command)


class Command:
    goto_history = []
    cur_dir = ''
    project_file_path = None

    title ="Project" # No _() here, the translation is offered in "translation template.ini".
    menuitems = (
        # item_caption, item_parent, item_types, item_action, item_hotkey
        (_("New project"), "proj", [None, NODE_PROJECT, NODE_DIR, NODE_FILE, NODE_BAD], "cuda_project_man.action_new_project", ""),
        (_("Open project..."), "proj", [None, NODE_PROJECT, NODE_DIR, NODE_FILE, NODE_BAD], "cuda_project_man.action_open_project", ""),
        (_("Recent projects"), "proj", [None, NODE_PROJECT, NODE_DIR, NODE_FILE, NODE_BAD], "collect_recent_projects", ""),
        (_("Save project as..."), "proj", [None, NODE_PROJECT, NODE_DIR, NODE_FILE, NODE_BAD], "cuda_project_man.action_save_project_as", ""),

        (_("Add folder..."), "nodes", [None, NODE_PROJECT, NODE_DIR, NODE_FILE, NODE_BAD], "cuda_project_man.action_add_folder", ""),
        (_("Add file..."), "nodes", [None, NODE_PROJECT, NODE_DIR, NODE_FILE, NODE_BAD], "cuda_project_man.action_add_file", ""),
        (_("Clear project"), "nodes", [None, NODE_PROJECT, NODE_DIR, NODE_FILE, NODE_BAD], "cuda_project_man.action_clear_project", ""),
        (_("Remove node"), "nodes", [None, NODE_PROJECT, NODE_DIR, NODE_FILE, NODE_BAD], "cuda_project_man.action_remove_node", ""),

        (_("New file..."), "dir", [NODE_DIR], "cuda_project_man.action_new_file", S_CTRL_NAME + "+N"),
        (_("New folder..."), "dir", [NODE_DIR], "cuda_project_man.action_new_directory", "F7"),
        (_("-"), "dir", [NODE_DIR], "", ""),
        (_("Cut"), "dir", [NODE_DIR], "cuda_project_man.action_cut", S_CTRL_NAME + "+X"),
        (_("Copy"), "dir", [NODE_DIR], "cuda_project_man.action_copy", S_CTRL_NAME + "+C"),
        (_("Paste"), "dir", [NODE_DIR], "cuda_project_man.action_paste", S_CTRL_NAME + "+V"),
        (_("-"), "dir", [NODE_DIR], "", ""),
        (_("Rename..."), "dir", [NODE_DIR], "cuda_project_man.action_rename", "F2"),
        (_("Delete"), "dir", [NODE_DIR], "cuda_project_man.action_delete_directory", "Del"),
        (_("-"), "dir", [NODE_DIR], "", ""),
        (_("Find in folder..."), "dir", [NODE_DIR], "cuda_project_man.action_find_in_directory", ""),
        (_("-"), "dir", [NODE_DIR], "", ""),
        (_("Copy path"), "dir", [NODE_DIR], "cuda_project_man.action_copy_path", ""),
        (_("Copy relative path") , "dir", [NODE_DIR], "cuda_project_man.action_copy_relative_path", ""),
        (_("-"), "dir", [NODE_DIR], "", ""),
        (_("Duplicate..."), "dir", [NODE_DIR], "cuda_project_man.action_duplicate", ""),
        (_("-"), "dir", [NODE_DIR], "", ""),
        (_("Focus in file manager"), "dir", [NODE_DIR], "cuda_project_man.action_focus_in_fileman", ""),
        (_("-"), "dir", [NODE_DIR], "", ""),
        (_("Properties..."), "dir", [NODE_DIR], "cuda_project_man.action_get_properties", ""),

        (_("Open in default application"), "file", [NODE_FILE], "cuda_project_man.action_open_def", ""),
        (_("-"), "file", [NODE_FILE], "", ""),
        (_("Cut"), "file", [NODE_FILE], "cuda_project_man.action_cut", S_CTRL_NAME + "+X"),
        (_("Copy"), "file", [NODE_FILE], "cuda_project_man.action_copy", S_CTRL_NAME + "+C"),
        (_("Paste"), "file", [NODE_FILE], "cuda_project_man.action_paste", S_CTRL_NAME + "+V"),
        (_("-"), "file", [NODE_FILE], "", ""),
        (_("Copy path"), "file", [NODE_FILE], "cuda_project_man.action_copy_path", ""),
        (_("Copy relative path"), "file", [NODE_FILE], "cuda_project_man.action_copy_relative_path", ""),
        (_("-"), "file", [NODE_FILE], "", ""),
        (_("Rename..."), "file", [NODE_FILE], "cuda_project_man.action_rename", "F2"),
        (_("Delete"), "file", [NODE_FILE], "cuda_project_man.action_delete_file", "Del"),
        (_("-"), "file", [NODE_FILE], "", ""),
        (_("Duplicate..."), "file", [NODE_FILE], "cuda_project_man.action_duplicate", ""),
        (_("-"), "file", [NODE_FILE], "", ""),
        (_("Set as main file"), "file", [NODE_FILE], "cuda_project_man.action_set_as_main_file", ""),
        (_("-"), "file", [NODE_FILE], "", ""),
        (_("Focus in file manager"), "file", [NODE_FILE], "cuda_project_man.action_focus_in_fileman", ""),
        (_("-"), "file", [NODE_FILE], "", ""),
        (_("Properties..."), "file", [NODE_FILE], "cuda_project_man.action_get_properties", ""),

        ("-"   , "", [None, NODE_PROJECT, NODE_DIR, NODE_FILE, NODE_BAD], "", ""),
        (_("Refresh"), "", [None, NODE_PROJECT, NODE_DIR, NODE_FILE, NODE_BAD], "cuda_project_man.action_refresh", "F5"),
        ("-"   , "", [None, NODE_PROJECT, NODE_DIR, NODE_FILE, NODE_BAD], "", ""),
        (_("Go to file...")  , "", [None, NODE_PROJECT, NODE_DIR, NODE_FILE, NODE_BAD], "cuda_project_man.action_go_to_file", ""),
    )

    options = {
        "recent_projects": [],
        "no_files": "",
        "no_dirs": ".git;.svn",
        "no_hidden": True,
        "toolbar": True,
        "preview": True,
        "d_click": False,
        "goto_open": False,
        "sort_order": "ext",
        "always_sync": False,
    }

    tree = None
    h_dlg = None
    h_menu = None
    h_menu_cfg = None

    action_cut_activate = False

    def __init__(self):
        settings_dir = Path(app_path(APP_DIR_SETTINGS))
        self.options_filename = settings_dir / "cuda_project_man.json"
        if self.options_filename.exists():
            with self.options_filename.open(encoding='utf8') as fin:
                self.options = json.load(fin)
                if "recent_projects" in self.options:
                    self.options["recent_projects"] = [expand_macros('', fn) for fn in self.options["recent_projects"]]

        self.new_project(False, False) # don't forget session in on_start


    def init_form_main(self):

        show_toolbar = self.options.get("toolbar", True)
        toolbar_theme = self.options.get("toolbar_theme", "default_16x16")

        self.h_dlg = dlg_proc(0, DLG_CREATE)

        dlg_proc(self.h_dlg, DLG_PROP_SET, {
            'keypreview': True,
            'on_key_down': self.form_key_down,
            } )

        n = dlg_proc(self.h_dlg, DLG_CTL_ADD, prop='toolbar')
        dlg_proc(self.h_dlg, DLG_CTL_PROP_SET, index=n, prop={
            'name':'bar',
            'a_r':('',']'), #anchor to top: l,r,t
            'vis': show_toolbar,
            'h': 24,
            'autosize': True,
            } )

        self.h_bar = dlg_proc(self.h_dlg, DLG_CTL_HANDLE, index=n)
        self.toolbar_imglist = toolbar_proc(self.h_bar, TOOLBAR_GET_IMAGELIST)
        self.set_imagelist_size(toolbar_theme, self.toolbar_imglist)

        dirname = os.path.join(app_path(APP_DIR_DATA), 'projtoolbaricons', toolbar_theme)
        icon_recents = imagelist_proc(self.toolbar_imglist, IMAGELIST_ADD, value = os.path.join(dirname, 'recents.png'))
        icon_open = imagelist_proc(self.toolbar_imglist, IMAGELIST_ADD, value = os.path.join(dirname, 'open.png'))
        icon_save = imagelist_proc(self.toolbar_imglist, IMAGELIST_ADD, value = os.path.join(dirname, 'save.png'))
        icon_add_file = imagelist_proc(self.toolbar_imglist, IMAGELIST_ADD, value = os.path.join(dirname, 'add-file.png'))
        icon_add_dir = imagelist_proc(self.toolbar_imglist, IMAGELIST_ADD, value = os.path.join(dirname, 'add-dir.png'))
        # icon_del = imagelist_proc(self.toolbar_imglist, IMAGELIST_ADD, value = os.path.join(dirname, 'del.png'))
        icon_cfg = imagelist_proc(self.toolbar_imglist, IMAGELIST_ADD, value = os.path.join(dirname, 'cfg.png'))
        icon_sync = imagelist_proc(self.toolbar_imglist, IMAGELIST_ADD, value = os.path.join(dirname, 'sync.png'))

        toolbar_proc(self.h_bar, TOOLBAR_THEME)
        _toolbar_add_btn(self.h_bar, hint=_('Recent projects'), icon=icon_recents, command='cuda_project_man.menu_recents' )
        _toolbar_add_btn(self.h_bar, hint=_('Open project'), icon=icon_open, command='cuda_project_man.action_open_project' )
        _toolbar_add_btn(self.h_bar, hint=_('Save project as'), icon=icon_save, command='cuda_project_man.action_save_project_as' )
        _toolbar_add_btn(self.h_bar, hint='-' )
        _toolbar_add_btn(self.h_bar, hint=_('Add folder'), icon=icon_add_dir, command='cuda_project_man.action_add_folder' )
        _toolbar_add_btn(self.h_bar, hint=_('Add file'), icon=icon_add_file, command='cuda_project_man.action_add_file' )
        # _toolbar_add_btn(self.h_bar, hint=_('Remove node'), icon=icon_del, command='cuda_project_man.action_remove_node' )
        _toolbar_add_btn(self.h_bar, hint='-' )
        _toolbar_add_btn(self.h_bar, hint=_('Project sessions'), icon=icon_cfg, command='cuda_project_man.menu_cfg')
        _toolbar_add_btn(self.h_bar, hint=_('Sync project-tree with current editor file'), icon=icon_sync, command='cuda_project_man.sync_to_ed' )

        btn_count = toolbar_proc(self.h_bar, TOOLBAR_GET_COUNT)
        self.h_btn_sync = toolbar_proc(self.h_bar, TOOLBAR_GET_BUTTON_HANDLE, index=btn_count-1)
        button_proc(self.h_btn_sync, BTN_SET_VISIBLE, not self.options.get('always_sync', False))

        toolbar_proc(self.h_bar, TOOLBAR_SET_WRAP, index=True)
        toolbar_proc(self.h_bar, TOOLBAR_UPDATE)

        n = dlg_proc(self.h_dlg, DLG_CTL_ADD, prop='treeview')
        dlg_proc(self.h_dlg, DLG_CTL_PROP_SET, index=n, prop={
            'name':'tree',
            'a_t':('bar', ']'),
            'a_r':('',']'), #anchor to entire form
            'a_b':('',']'),
            'on_menu': 'cuda_project_man.tree_on_menu',
            'on_unfold': 'cuda_project_man.tree_on_unfold',
            'on_click': 'cuda_project_man.tree_on_click',
            'on_click_dbl': 'cuda_project_man.tree_on_click_dbl',
            } )

        self.tree = dlg_proc(self.h_dlg, DLG_CTL_HANDLE, index=n)
        self.tree_imglist = tree_proc(self.tree, TREE_GET_IMAGELIST)
        tree_proc(self.tree, TREE_PROP_SHOW_ROOT, text='0')
        tree_proc(self.tree, TREE_ITEM_DELETE, 0)

        self.icon_init()
        self.ICON_ALL = self.icon_get('_')
        self.ICON_DIR = self.icon_get('_dir')
        self.ICON_PROJ = self.icon_get('_proj')
        self.ICON_BAD = self.icon_get('_bad')
        self.ICON_ZIP = self.icon_get('_zip')
        self.ICON_BIN = self.icon_get('_bin')
        self.ICON_IMG = self.icon_get('_img')


    def init_panel(self, and_activate=True):
        # already inited?
        if self.tree:
            return

        self.init_form_main()

        #dlg_proc(self.h_dlg, DLG_SCALE)
        tree_proc(self.tree, TREE_THEME) #TREE_THEME only after DLG_SCALE

        app_proc(PROC_SIDEPANEL_ADD_DIALOG, (self.title, self.h_dlg, 'project.png'))

        if and_activate:
            self.do_show()

        self.action_refresh()
        self.generate_context_menu()


    def show_panel(self):
        self.do_show()

    def focus_panel(self):
        self.do_show()
        app_proc(PROC_SIDEPANEL_ACTIVATE, (self.title, True))

    def do_show(self):
        if not self.tree:
            self.init_panel(True)
        else:
            ed.cmd(cudatext_cmd.cmd_ShowSidePanelAsIs)
            app_proc(PROC_SIDEPANEL_ACTIVATE, self.title)

    @property
    def selected(self):
        return tree_proc(self.tree, TREE_ITEM_GET_SELECTED)

    def add_context_menu_node(self, parent, action, name):
        return menu_proc(parent, MENU_ADD, command=action, caption=name)


    def generate_context_menu(self):
        node_type = None
        if self.selected is not None:
            n = self.get_info(self.selected).image
            if n == self.ICON_PROJ: node_type = NODE_PROJECT
            elif n == self.ICON_DIR: node_type = NODE_DIR
            elif n == self.ICON_BAD: node_type = NODE_BAD
            else: node_type = NODE_FILE

        if not self.h_menu:
            self.h_menu = menu_proc(0, MENU_CREATE)

        menu_all = self.h_menu
        menu_proc(menu_all, MENU_CLEAR)
        menu_proj = self.add_context_menu_node(menu_all, "0", _("Project file"))
        menu_nodes = self.add_context_menu_node(menu_all, "0", _("Root nodes"))
        if node_type == NODE_FILE:
            menu_file = self.add_context_menu_node(menu_all, "0", _("Selected file"))
        if node_type == NODE_DIR:
            menu_dir = self.add_context_menu_node(menu_all, "0", _("Selected folder"))

        for item in self.menuitems:
            item_caption = item[0]
            item_parent = item[1]
            item_types = item[2]
            item_action = item[3]
            item_hotkey = item[4]
            if node_type not in item_types:
                continue

            if item_parent == "proj":
                menu_use = menu_proj
            elif item_parent == "nodes":
                menu_use = menu_nodes
            elif item_parent == "file":
                menu_use = menu_file
            elif item_parent == "dir":
                menu_use = menu_dir
            else:
                menu_use = menu_all

            if item_action == "collect_recent_projects":
                action = ""
            else:
                action = item_action

            menu_added = self.add_context_menu_node(menu_use, action, item_caption)

            if item_hotkey:
                menu_proc(menu_added, MENU_SET_HOTKEY, item_hotkey)

            if item_action == "collect_recent_projects":
                for path in self.options["recent_projects"]:
                    if os.sep in path:
                        action = str.format("module=cuda_project_man;cmd=action_open_project;info={};", path)
                        self.add_context_menu_node(menu_added, action, nice_filename(path))

    @staticmethod
    def node_ordering(node):
        # sort folders first, then by extension
        path = Path(node)
        return path.is_file(), path.suffix.upper(), path.name.upper()

    @staticmethod
    def node_ordering_direntry(path):
        ''' node_ordering() for os.DirEntry and pathlib.Path '''
        global sort_order
        isfile = path.is_file()
        if sort_order=='ext':
            key = _file_ext(path.name).upper() if isfile else ''
        elif sort_order=='name':
            key = path.name.upper()
        elif sort_order=='size':
            key = -path.stat().st_size if isfile else ''
        elif sort_order=='size-':
            key = path.stat().st_size if isfile else ''
        elif sort_order=='mtime':
            key = -path.stat().st_mtime if isfile else ''
        elif sort_order=='mtime-':
            key = path.stat().st_mtime if isfile else ''
        elif sort_order=='ctime':
            key = -path.stat().st_ctime if isfile else ''
        elif sort_order=='ctime-':
            key = path.stat().st_ctime if isfile else ''
        elif sort_order=='':
            key = ''
        else:
            raise ValueError('Unknown sort_order: '+sort_order)

        return isfile, key, path.name.upper()

    def add_node(self, path):
        if path:
            # on adding _first_ node to _untitled_ proj, name the proj as 'temporary' and save it
            if not self.project["nodes"] and not self.project_file_path:
                self.action_save_project_as(PROJECT_TEMP_FILENAME)
                self.add_recent(PROJECT_TEMP_FILENAME)
                self.save_options()

            if path in self.project["nodes"]:
                return

            msg_status(_("Adding to project: ") + collapse_filename(path), True)
            self.project["nodes"].append(path)
            self.project["nodes"].sort(key=Command.node_ordering)
            self.action_refresh()
            if self.project_file_path:
                self.action_save_project_as(self.project_file_path)

    def new_project(self, forget_session=True, apply_on_start=True):
        if self.project_file_path:
            self.action_save_project_as(self.project_file_path)

        if forget_session:
            self.session_forget()

        self.project = dict(nodes=[])
        self.project_file_path = None
        self.update_global_data()
        self.goto_history = []
        self.cur_dir = ''

        app_proc(PROC_SET_FOLDER, '')
        app_proc(PROC_SET_PROJECT, '')

        self.close_foreign_tabs(True)

        if apply_on_start:
            self.options['on_start'] = False
            self.save_events()

    def add_recent(self, path):
        recent = self.options["recent_projects"]
        if path in recent:
            recent.pop(recent.index(path))

        self.options["recent_projects"] = ([path] + recent)[:10]
        self.generate_context_menu()

    def action_new_file(self):
        location = Path(self.get_location_by_index(self.selected))
        if location.is_file():
            location = location.parent

        result = dlg_input(_("New file:"), "")
        if not result:
            return

        if os.sep in result:
            msg_status(_("Incorrect file name"))
            return

        path = location / result
        path.touch()
        self.action_refresh()

        #open new file
        self.jump_to_filename(str(path))
        if os.path.isfile(str(path)):
            _file_open(str(path))

    def action_open_def(self):
        fn = self.get_location_by_index(self.selected)
        sfn = str(fn)
        if not os.path.isfile(sfn):
            return
        suffix = OS_SUFFIX
        if suffix=='':
            #Windows
            #os.startfile(sfn) crashes with LSP plugin
            import subprocess
            subprocess.Popen('start "" "{}"'.format(sfn), shell=True, cwd=os.path.dirname(sfn))
        elif suffix=='__mac':
            #macOS
            os.system('open "%s"'%sfn)
        elif suffix=='__haiku':
            #Haiku
            msg_status('TODO: implement "Open in default app" for Haiku')
        else:
            #other Unixes
            os.spawnvp(os.P_WAIT, 'xdg-open', ('xdg-open', fn.as_uri()))

    def action_focus_in_fileman(self):
        fn = self.get_location_by_index(self.selected)
        sfn = str(fn)
        #if not os.path.isfile(sfn):
            #return
        suffix = OS_SUFFIX

        if suffix=='':
            #Windows
            #os.system('explorer.exe /select,'+sfn)
            import subprocess
            subprocess.Popen(('explorer.exe', '/select,', sfn), shell=True) # works better
        elif suffix=='__mac':
            #macOS
            #macOS cannot handle quoted filename
            os.system('open --new --reveal '+sfn.replace(' ', '\\ '))
        elif suffix=='__haiku':
            #Haiku
            msg_status('"Focus in file manager" not implemented for this OS')
        else:
            #Linux and others
            if os.spawnvp(os.P_WAIT, 'dbus-send', ('dbus-send', '--session', '--dest=org.freedesktop.FileManager1', '--type=method_call', '--print-reply', '/org/freedesktop/FileManager1', 'org.freedesktop.FileManager1.ShowItems', 'array:string:'+fn.as_uri(), 'string:')):
                os.spawnvp(os.P_WAIT, 'xdg-open', ('xdg-open', fn.parent.as_uri()))

    def action_rename(self):
        location = Path(self.get_location_by_index(self.selected))
        e = _file_editor(str(location))

        # TODO: Support unsaved changes when API available
        if e is not None \
        and e.get_prop(PROP_MODIFIED, '') == True \
        and msg_box(_('The file you are renaming has unsaved changes. Would you like to save the changes first?'), MB_OKCANCEL+MB_ICONWARNING) != ID_OK:
            return

        result = dlg_input(_("Rename to:"), str(location.name))
        if not result:
            return

        new_location = location.parent / result
        if location == new_location:
            return

        if e is not None:
            e.save(str(new_location))
            location.unlink(True)
        else:
            location.replace(new_location)

        if self.is_path_in_root(location):
            self.action_remove_node()
            self.add_node(str(new_location))

        # fix filename of existing ui-tab
        for h in ed_handles():
            e = Editor(h)
            if e.get_filename()==str(location):
                e.save(str(new_location))

        self.action_refresh()
        self.jump_to_filename(str(new_location))
        msg_status(_("Renamed to: ") + str(collapse_filename(str(new_location.name))))

    def action_duplicate(self):
        location = Path(self.get_location_by_index(self.selected))
        e = _file_editor(str(location))

        fn_parts = location.name.split('.')
        from datetime import datetime
        fn_1 = location.name if not fn_parts[0] else fn_parts[0]
        fn_2 = '' if not fn_parts[0] else ('.' + '.'.join(fn_parts[i+1] for i in range(len(fn_parts) - 1)))
        backup_name = fn_1 + '_' + datetime.now().strftime("%y%m%d_%H%M%S") + fn_2 if not os.path.isdir(str(location)) else location.name+' copy'
        result = dlg_input(_("Duplicate to:"), str(backup_name))
        if not result:
            return

        new_location = location.parent / result
        if location == new_location:
            return

        if e is not None:
            e.save(str(new_location), True)
        else:
            import shutil
            if os.path.isdir(location):
                shutil.copytree(location, new_location, symlinks=False)
            else:
                shutil.copy2(location, new_location)

        if self.is_path_in_root(location):
            self.add_node(str(new_location))

        self.action_refresh()
        msg_status(_("Copied to: ") + str(collapse_filename(str(new_location.name))))

    def action_delete_file(self):
        location = Path(self.get_location_by_index(self.selected))
        if msg_box(_("Delete file from disk:\n") + str(location), MB_OKCANCEL + MB_ICONWARNING) != ID_OK:
            return

        location.unlink()
        if self.is_path_in_root(location):
            self.action_remove_node()
        else:
            h_parent = tree_proc(self.tree, TREE_ITEM_GET_PROPS, self.selected)['parent']
            tree_proc(self.tree, TREE_ITEM_SELECT, h_parent)
            self.action_refresh(h_parent)
        msg_status(_("Deleted file: ") + str(location.name))

    def action_copy_relative_path(self):
        file_path = str(self.get_location_by_index(self.selected))
        project_node = ''
        for node in self.project["nodes"]:
            if (file_path+os.sep).startswith(node+os.sep):
                project_node = node
                break
        if project_node:
            file_path = file_path[len(os.path.dirname(project_node))+1:]
        app_proc(PROC_SET_CLIP, file_path)

    def action_copy_path(self):
        file_path = str(self.get_location_by_index(self.selected))
        proj_path = os.path.dirname(self.project_file_path or '')
        if not proj_path:
            return
        if file_path.startswith(proj_path+os.sep):
            file_path = file_path[len(proj_path)+1:]
        app_proc(PROC_SET_CLIP, file_path)

    def action_cut(self):
        selected = self.get_location_by_index(self.selected)
        if (selected.is_file() or selected.is_dir()):
            app_proc(PROC_SET_CLIP, str(selected))
        self.action_cut_activate = True

    def action_copy(self):
        selected = self.get_location_by_index(self.selected)
        if (selected.is_file() or selected.is_dir()):
            app_proc(PROC_SET_CLIP, str(selected))

    def action_paste(self):
        location = app_proc(PROC_GET_CLIP, '')
        new_location = Path(self.get_location_by_index(self.selected))
        if (Path(location).is_file() or Path(location).is_dir()):
            if new_location.is_file():
                new_location = new_location.parent
            if Path(location).is_file():
                new_location_path = str(new_location) + os.sep + str(Path(location).name)
                if location == new_location_path:
                    fn_parts = Path(location).name.split('.')
                    from datetime import datetime
                    fn_1 = Path(location).name if not fn_parts[0] else fn_parts[0]
                    fn_2 = '' if not fn_parts[0] else ('.' + '.'.join(fn_parts[i+1] for i in range(len(fn_parts) - 1)))
                    new_name = fn_1 + '_' + datetime.now().strftime("%y%m%d_%H%M%S") + fn_2
                    new_location = str(new_location) + os.sep + new_name
                import shutil
                if self.action_cut_activate:
                    shutil.move(location, new_location)
                else:
                    shutil.copy2(location, new_location)
            else:
                new_location = str(new_location) + os.sep + Path(location).name
                import shutil
                if self.action_cut_activate:
                    shutil.move(str(location), new_location)
                else:
                    from distutils.dir_util import copy_tree
                    copy_tree(str(location), new_location)
                    shutil.copystat(str(location), new_location)
            self.action_refresh()

    def get_w_h(self):
        w_ = 600
        h_ = 600
        r = app_proc(PROC_COORD_MONITOR, 0)
        if r:
            w_ = (r[2]-r[0]) // 3
            h_ = (r[3]-r[1]) // 4

        return w_, h_

    def callback_button_ok(self, id_dlg, id_ctl, data='', info=''):
        dlg_proc(id_dlg, DLG_HIDE)

    def show_memo(self, text, caption):
        w_, h_ = self.get_w_h()
        h = dlg_proc(0, DLG_CREATE)
        dlg_proc(h, DLG_PROP_SET, prop={
            'w': w_,
            'h': h_,
            'cap': caption,
            'border': DBORDER_DIALOG,
        })

        n = dlg_proc(h, DLG_CTL_ADD, prop='button')
        dlg_proc(h, DLG_CTL_PROP_SET, index=n, prop={
            'name': 'btn_ok',
            'x': w_-100,
            'y': h_-6-25,
            'w': 100-6,
            'cap': _('&OK'),
            'on_change': 'module=cuda_project_man;cmd=callback_button_ok;',
            'ex0': True,
        })

        n = dlg_proc(h, DLG_CTL_ADD, prop='memo')
        dlg_proc(h, DLG_CTL_PROP_SET, index=n, prop={
            'name': 'memo_log',
            'val': text,
            'x': 6,
            'y': 6,
            'w': w_-6*2,
            'h': h_-6*3-25,
            'ex0': True,
            'ex1': True,
        })

        dlg_proc(h, DLG_CTL_FOCUS, name='btn_ok')
        dlg_proc(h, DLG_SCALE)
        dlg_proc(h, DLG_SHOW_MODAL)
        dlg_proc(h, DLG_FREE)

    def convert_size(self, size_bytes):
        size_bytes = int(size_bytes)
        if size_bytes == 0:
            return '0 b'
        size_name = ('b', 'kB', 'mB', 'gB')
        import math
        i = int(math.floor(math.log(size_bytes, 1024)))
        p = math.pow(1024, i)
        s = round(size_bytes / p, 2)
        return str("%s %s" % (s, size_name[i]))

    def action_get_properties(self):
        import os
        import datetime
        selected = self.get_location_by_index(self.selected)
        if os.path.exists(selected):
            if os.path.isfile(selected):
                text = _('File: ') + str(selected) + "\n"
                text += _('Size: ') + self.convert_size(os.path.getsize(selected)) + "\n"
                text += _('Date of creation: ') + str(datetime.datetime.fromtimestamp(int(os.path.getctime(selected)))) + "\n"
                text += _('Date of opening: ') + str(datetime.datetime.fromtimestamp(int(os.path.getatime(selected)))) + "\n"
                text += _('Date of modification: ') + str(datetime.datetime.fromtimestamp(int(os.path.getmtime(selected)))) + "\n"
            elif os.path.isdir(selected):
                text = _('Folder: ') + str(selected) + "\n"
                def folder_size(path):
                    total = 0
                    for entry in os.scandir(path):
                        if entry.is_file():
                            total += entry.stat().st_size
                        elif entry.is_dir():
                            total += folder_size(entry.path)
                    return total
                text += _('Size: ') + str(self.convert_size(folder_size(selected))) + "\n"
        else:
            text = _('Object not found')

        if text:
            self.show_memo(text, _('Properties'))

    def do_delete_dir(self, location):
        for path in location.glob("*"):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                self.do_delete_dir(path)
        location.rmdir()

    def action_delete_directory(self):
        location = Path(self.get_location_by_index(self.selected))
        if msg_box(_("Delete folder from disk:\n") + str(location), MB_OKCANCEL + MB_ICONWARNING) != ID_OK:
            return

        self.do_delete_dir(location)
        if self.is_path_in_root(location):
            self.action_remove_node()
        else:
            h_parent = tree_proc(self.tree, TREE_ITEM_GET_PROPS, self.selected)['parent']
            tree_proc(self.tree, TREE_ITEM_SELECT, h_parent)
            self.action_refresh(h_parent)

            path = self.get_location_by_index(self.selected)
            app_proc(PROC_SET_FOLDER, path)
            self.cur_dir = path

        msg_status(_("Deleted dir: ") + str(location.name))

    def action_new_directory(self):
        location = Path(self.get_location_by_index(self.selected))
        if location.is_file():
            location = location.parent
        result = dlg_input(_("New folder:"), "")
        if not result:
            return

        location = location / result
        location.mkdir()
        self.action_refresh()
        self.jump_to_filename(str(location))

    def action_find_in_directory(self):
        location = str(self.get_location_by_index(self.selected))
        try:
            import cuda_find_in_files as fif
            msg_status(_('Called "Find in Files" for "%s"') % location)
            fif.show_dlg(what="", opts={"fold": location})
        except ImportError:
            try:
                from cuda_find_in_files4 import show_fif4
                msg_status(_('Called "Find in Files 4" for "%s"') % location)
                show_fif4({'with': {
                    'in_what': '',
                    'wk_fold': location,
                    'wk_incl': '*'
                    }})
            except ImportError:
                msg_box(_('Plugin "Find in Files" or "Find in Files 4" is not installed, install it first'), MB_OK + MB_ICONERROR)


    def action_refresh(self, parent=None):

        sel_fn = ''
        id = self.selected
        if id:
            prop = tree_proc(self.tree, TREE_ITEM_GET_PROPS, id)
            if prop:
                sel_fn = prop.get('data', '')

        # it was hard to add TREE_LOCK/UNLOCK directly into action_refresh_int
        tree_proc(self.tree, TREE_LOCK)
        try:
            unfolds = []
            self.enum_all_getfolds(unfolds)
            #msg_box('unfolds:\n'+', '.join(unfolds), MB_OK)

            self.action_refresh_int(parent)

            self.enum_all_setfolds(unfolds)
        finally:
            tree_proc(self.tree, TREE_UNLOCK)

        if sel_fn:
            #print('sel to:', sel_fn)
            self.enum_all_sel(sel_fn)


    def get_project_name(self):

        if self.project_file_path is None:
            return PROJECT_UNSAVED_NAME
        else:
            return self.project_file_path.stem

    def action_refresh_int(self, parent=None):

        global sort_order
        sort_order = self.options.get('sort_order', 'ext')

        unfold = parent is None
        if parent is None:
            # clear tree
            tree_proc(self.tree, TREE_ITEM_DELETE, 0)
            project_name = self.get_project_name()

            parent = tree_proc(
                self.tree,
                TREE_ITEM_ADD,
                0,
                -1,
                project_name,
                self.ICON_PROJ,
            )

            #select 1st node
            items_root = tree_proc(self.tree, TREE_ITEM_ENUM, 0)
            tree_proc(self.tree, TREE_ITEM_SELECT, items_root[0][0])

            nodes = map(Path, self.project["nodes"])
            if sort_order != '':
                nodes = sorted(nodes, key=Command.node_ordering_direntry)
        else:
            fn = str(self.get_location_by_index(parent)) # str() is required for old Python 3.5 for os.scandir()
            if not fn: return
            #print('Reading dir:', fn)
            try:
                if hasattr(os, "scandir") and callable(os.scandir):
                    if sort_order == '':
                        scandir_ = list(os.scandir(fn))
                        dirs_ = [i for i in scandir_ if i.is_dir()]
                        files_ = [i for i in scandir_ if not i.is_dir()]
                        nodes = dirs_ + files_
                    else:
                        nodes = sorted(os.scandir(fn), key=Command.node_ordering_direntry)
                else:
                    # support for old Python 3.4
                    if sort_order == '':
                        nodes = Path(fn).iterdir()
                    else:
                        nodes = sorted(Path(fn).iterdir(), key=Command.node_ordering)
            except:
                tree_proc(self.tree, TREE_ITEM_SET_ICON, parent, image_index=self.ICON_BAD)
                raise # good to see the error
                return

        for path in nodes:
            # DirEntry or Path?
            if isinstance(path, Path):
                spath = str(path)
            else:
                spath = path.path
            is_dir = path.is_dir()
            sname = path.name
            if is_win_root(spath):
                sname = spath
            elif self.options.get("no_hidden", True) and is_hidden(spath):
                continue
            elif self.is_filename_ignored(spath, is_dir):
                continue

            if is_locked(spath):
                imageindex = self.ICON_BAD
            elif is_dir:
                imageindex = self.ICON_DIR
            elif is_simple_listed(path.name, MASKS_IMAGES):
                imageindex = self.ICON_IMG
            elif is_simple_listed(path.name, MASKS_ZIP):
                imageindex = self.ICON_ZIP
            elif is_simple_listed(path.name, MASKS_BINARY):
                imageindex = self.ICON_BIN
            else:
                lexname = lexer_proc(LEXER_DETECT, path.name)
                if lexname:
                    imageindex = self.icon_get(lexname)
                else:
                    imageindex = self.ICON_ALL

            index = tree_proc(
                self.tree,
                TREE_ITEM_ADD,
                parent,
                -1,
                sname,
                imageindex,
                data=spath
                )

            # dummy nested node for folders
            if imageindex == self.ICON_DIR:
                tree_proc(
                    self.tree,
                    TREE_ITEM_ADD,
                    index,
                    -1,
                    'dummy',
                    -1
                    )

        if unfold:
            tree_proc(self.tree, TREE_ITEM_UNFOLD, parent)

    def action_new_project(self):
        self.session_save(True)
        self.new_project()
        self.action_refresh()

    def action_open_project(self, info=None):
        if self.project_file_path:
            self.action_save_project_as(self.project_file_path)

        path = info
        if path is None:
            path = dlg_file(True, "", "", PROJECT_DIALOG_FILTER)
        if path:
            self.session_save(True)

            proj_dir = os.path.dirname(path)

            if Path(path).exists():
                print(_('Loading project: ') + collapse_filename(path))
                with open(path, encoding='utf8') as fin:
                    self.project = json.load(fin)

                    if 'nodes' in self.project:
                        for i in range(len(self.project['nodes'])):
                            node = self.project['nodes'][i]
                            # normalize os separators
                            node = os.path.normpath(node)
                            self.project['nodes'][i] = expand_macros(proj_dir, node)

                        # delete orphan items
                        bads = [fn for fn in self.project["nodes"] if not os.path.exists(fn)]
                        '''
                        for fn in bads:
                            self.project["nodes"].remove(fn)
                        '''

                    #print('Loaded project:', self.project)
                    self.project_file_path = Path(path)
                    self.add_recent(path)
                    self.action_refresh()
                    self.options['on_start'] = True
                    self.save_events()
                    self.save_options()

                self.update_global_data()
                self.goto_history = []

                for fn in self.project["nodes"]:
                    if os.path.isdir(fn):
                        app_proc(PROC_SET_FOLDER, fn)
                        self.cur_dir = fn
                        break

                app_proc(PROC_SET_PROJECT, path)

                s = _("Opened project: ") + os.path.basename(path)
                if bads:
                    s += ', ' + _('%d deleted item(s)')%len(bads)
                msg_status(s)

                sess = self.project.get('def_session', '')
                if sess not in ('', '-'):
                    self.session_load(sess, False)
                if 'unfold' in self.project:
                    unfolds = self.project['unfold']
                    for i in range(len(unfolds)):
                        unfolds[i] = expand_macros(proj_dir, unfolds[i])
                    self.enum_all_setfolds(unfolds)
            else:
                msg_status(_("Project file not found: ") + collapse_filename(str(path)))

    def action_add_folder(self):
        fn = dlg_dir("")
        self.add_node(fn)

    def action_add_file(self):
        fn = dlg_file(True, "", "", "")
        self.add_node(fn)

    def action_remove_node(self):
        index = self.selected
        while True:
            prop = tree_proc(self.tree, TREE_ITEM_GET_PROPS, index)
            if prop["level"] == 0:
                return
            if prop["level"] == 1:
                path = prop["data"]
                break
            index = prop["parent"]

        tree_proc(self.tree, TREE_ITEM_DELETE, index)
        if str(path) in self.project["nodes"]:
            self.project["nodes"].remove(str(path))
            if (self.cur_dir+os.sep).startswith(str(path)+os.sep):
                app_proc(PROC_SET_FOLDER, '')
                self.cur_dir = ''

        if self.project_file_path:
            self.action_save_project_as(self.project_file_path)

    def action_clear_project(self):

        self.session_forget()
        self.session_delete_all()
        self.project["nodes"].clear()
        if self.project_file_path:
            self.action_save_project_as(self.project_file_path)
        self.action_refresh()

    def action_set_as_main_file(self):

        path = self.get_location_by_index(self.selected)
        self.project["mainfile"] = str(path)
        self.update_global_data()

        if self.project_file_path:
            self.action_save_project_as(self.project_file_path)

    def action_save_project_as(self, path=None):

        need_refresh = path is None
        if path is None:
            if self.project_file_path:
                project_path = str(self.project_file_path.parent)
            else:
                project_path = ""
            path = dlg_file(False, "", project_path, PROJECT_DIALOG_FILTER)

        if path:
            proj_dir = os.path.dirname(str(path))

            path = Path(path)
            if path.suffix != PROJECT_EXTENSION:
                path = path.parent / (path.name + PROJECT_EXTENSION)

            # pre-processing of dict before saving
            d = copy.deepcopy(self.project)
            if 'nodes' in d:
                for i in range(len(d['nodes'])):
                    d['nodes'][i] = collapse_macros(proj_dir, d['nodes'][i])

            unfolds = []
            self.enum_all_getfolds(unfolds)
            unfolds = list(map(lambda x: collapse_macros(proj_dir, x), unfolds))
            d['unfold'] = unfolds

            self.project_file_path = path
            with path.open("w", encoding='utf8') as fout:
                json.dump(d, fout, indent=2)

            # any saving of project file makes on_start On
            self.options['on_start'] = True
            self.save_events()

            self.update_global_data()
            print(_('Saving project: ') + collapse_filename(str(path)))
            msg_status(_("Project saved"))

            if need_refresh:
                self.add_recent(str(path))
                self.action_refresh()
                self.save_options()

    def action_go_to_file(self):
        self.menu_goto()

    def action_project_properties(self):
        self.config_proj()

    def action_config(self):
        self.config()

    def menu_cfg(self):
        if self.h_menu_cfg is None:
            self.h_menu_cfg = menu_proc(0, MENU_CREATE)
        menu_proc(self.h_menu_cfg, MENU_CLEAR)

        cur_name = self.session_cur_name()

        names = self.session_get_names()
        if names:
            for (index, name) in enumerate(names):
                id = menu_proc(self.h_menu_cfg, MENU_ADD,
                    command="module=cuda_project_man;cmd=session_load;info=%s;"%name,
                    caption=_('Project session:')+' '+name
                    )
                if name==cur_name:
                    menu_proc(id, MENU_SET_ENABLED, command=False)
        else:
            id = menu_proc(self.h_menu_cfg, MENU_ADD, caption=_('Project session:'))
            menu_proc(id, MENU_SET_ENABLED, command=False)

        menu_proc(self.h_menu_cfg, MENU_ADD, caption='-')
        id = menu_proc(self.h_menu_cfg, MENU_ADD, command='cuda_project_man.session_save_as', caption=_('Save session...'))
        if self.is_project_empty():
            menu_proc(id, MENU_SET_ENABLED, command=False)

        id = menu_proc(self.h_menu_cfg, MENU_ADD, command='cuda_project_man.session_delete', caption=_('Delete session...'))
        if not names:
            menu_proc(id, MENU_SET_ENABLED, command=False)

        id = menu_proc(self.h_menu_cfg, MENU_ADD, command='cuda_project_man.session_def', caption=_('Set default session...'))
        if not names:
            menu_proc(id, MENU_SET_ENABLED, command=False)

        id = menu_proc(self.h_menu_cfg, MENU_ADD, command='cuda_project_man.session_forget_ex', caption=_('Forget session, close all tabs'))
        sess = app_path(APP_FILE_SESSION)
        if os.path.basename(sess)==DEF_SES:
            menu_proc(id, MENU_SET_ENABLED, command=False)

        #menu_proc(self.h_menu_cfg, MENU_ADD, caption='-')
        #menu_proc(self.h_menu_cfg, MENU_ADD, command='cuda_project_man.action_project_properties', caption=_('Project properties...'))
        #menu_proc(self.h_menu_cfg, MENU_ADD, command='cuda_project_man.action_config', caption=_('Project Manager options...'))

        menu_proc(self.h_menu_cfg, MENU_SHOW)

    def update_global_data(self):
        global global_project_info
        global_project_info['filename'] = str(self.project_file_path) if self.project_file_path else ''
        global_project_info['nodes'] = self.project['nodes']
        global_project_info['vars'] = self.project.setdefault('vars', [])
        global_project_info['mainfile'] = self.project.setdefault('mainfile', '')

    def get_info(self, index):
        if index is None:
            return
        info = tree_proc(self.tree, TREE_ITEM_GET_PROPS, index)
        if info:
            return NodeInfo(info['text'], info['icon'])

    def get_location_by_index(self, index):
        '''
        path = []
        while index and index not in self.top_nodes:
            path.append(self.get_info(index).caption)
            index = tree_proc(self.tree, TREE_ITEM_GET_PROPS, index)['parent']

        path.reverse()
        node = self.top_nodes.get(index, None)
        full_path = Path(node / str.join(os.sep, path)) if node else Path('')

        return full_path
        '''
        p = tree_proc(self.tree, TREE_ITEM_GET_PROPS, index)
        return Path(p.get('data', ''))

    def save_options(self):
        d = copy.deepcopy(self.options)
        d["recent_projects"] = [collapse_macros('', fn) for fn in d["recent_projects"]]
        with self.options_filename.open(mode="w", encoding='utf8') as fout:
            json.dump(d, fout, indent=2)

    def menu_recents(self):
        items = list(self.options["recent_projects"])

        if '' in items:
            items.remove('')
        if str(self.project_file_path) in items:
            items.remove(str(self.project_file_path))
        items = [item for item in items if os.path.isfile(item)]
        if not items:
            return

        items_nice = [os.path.basename(fn)+'\t'+collapse_filename(str(os.path.dirname(fn))) for fn in items]
        res = dlg_menu(DMENU_LIST, items_nice, caption=_('Recent projects'))
        if res is None:
            return

        self.init_panel()
        self.action_open_project(items[res])

    def do_unfold_first(self):
        """unfold 1st item under root"""
        items = tree_proc(self.tree, TREE_ITEM_ENUM, 0)
        if not items:
            return
        items = tree_proc(self.tree, TREE_ITEM_ENUM, items[0][0])
        if not items:
            return
        tree_proc(self.tree, TREE_ITEM_UNFOLD, items[0][0])
        tree_proc(self.tree, TREE_ITEM_SELECT, items[0][0])

        path = self.get_location_by_index(self.selected)
        app_proc(PROC_SET_FOLDER, path)
        self.cur_dir = path

    def new_project_open_dir(self):

        fn = dlg_dir("")
        if fn is None: return

        if is_locked(fn):
            print(_('Project Manager: folder is locked: ') + fn)
            return

        self.init_panel()
        self.action_new_project()
        self.add_node(fn)
        self.do_unfold_first()

        app_proc(PROC_SIDEPANEL_ACTIVATE, self.title)

    def open_dir(self, dirname, new_proj=False):

        if not os.path.isdir(dirname):
            print(_('Project Manager: folder not found: ') + dirname)
            return

        #expand "." to fully qualified name
        dirname = os.path.abspath(dirname)

        if is_locked(dirname):
            print(_('Project Manager: folder is locked: ') + dirname)
            return

        self.init_panel()
        if new_proj:
            self.action_new_project()
        self.add_node(dirname)
        if new_proj:
            self.do_unfold_first()

        app_proc(PROC_SIDEPANEL_ACTIVATE, self.title)

    def on_open_pre(self, ed_self, filename):
        if filename.endswith(PROJECT_EXTENSION):
            self.init_panel()
            self.action_open_project(filename)
            return False #block opening

    def on_exit(self, ed_self):
        if self.project_file_path:
            self.action_save_project_as(self.project_file_path)

    def config(self):

        from .projman_dlg import dialog_config
        if dialog_config(self.options):
            print(_('Project Manager: saving options'))
            self.save_options()

            if self.h_dlg:
                dlg_proc(self.h_dlg, DLG_CTL_PROP_SET, name='bar', prop={
                    'vis': self.options.get('toolbar', True)
                    })
                button_proc(self.h_btn_sync, BTN_SET_VISIBLE, not self.options.get('always_sync', False))

            self.save_events()

    def save_events(self):
            ev = []

            if self.options.get('on_start', False):
                ev.append('on_start')

            v = self.options.get('check_git', None)
            if v is None:
                s = ini_read('plugins.ini', 'events', 'cuda_project_man', '')
                v = 'on_open' in s
            if v:
                ev.append('on_open')

            # save events to plugins.ini [events], they will work additionally to install.inf events
            if ev:
                ini_write('plugins.ini', 'events', 'cuda_project_man', ','.join(ev))
            else:
                ini_proc(INI_DELETE_KEY, 'plugins.ini', 'events', 'cuda_project_man')

    def config_proj(self):
        if not self.tree:
            msg_status(_('Project not loaded'))
            return

        from .projman_dlg import dialog_proj_prop
        if dialog_proj_prop(self.project):
            self.update_global_data()
            if self.project_file_path:
                self.action_save_project_as(self.project_file_path)

    def is_filename_ignored(self, fn, is_dir):
        if is_dir:
            msk = self.options.get("no_dirs", "")
        else:
            msk = self.options.get("no_files", "")
        if msk:
            return is_mask_listed(os.path.basename(fn), msk)
        else:
            return False

    def on_start(self, ed_self):
        and_activate = self.options.get("on_start_activate", False)
        self.init_panel(and_activate)

        items = self.options.get("recent_projects", [])
        if items:
            self.action_open_project(items[0])

    def contextmenu_add_dir(self):
        self.init_panel()
        self.action_add_folder()

    def contextmenu_add_file(self):
        self.init_panel()
        self.action_add_file()

    def contextmenu_new_proj(self):
        self.init_panel()
        self.action_new_project()

    def contextmenu_open_proj(self):
        self.init_panel()
        self.action_open_project()

    def contextmenu_save_proj_as(self):
        self.init_panel()
        self.action_save_project_as()

    def contextmenu_refresh(self):
        self.init_panel()
        self.action_refresh()

    def contextmenu_remove_node(self):
        self.init_panel()
        self.action_remove_node()

    def contextmenu_clear_proj(self):
        self.init_panel()
        self.action_clear_project()

    def contextmenu_set_as_main_file(self):
        self.init_panel()
        self.action_set_as_main_file()

    def contextmenu_copy_relative_path(self):
        self.init_panel()
        self.action_copy_relative_path()

    def contextmenu_find_in_directory(self):
        self.init_panel()
        self.action_find_in_directory()

    def contextmenu_focus_in_fileman(self):
        self.init_panel()
        self.action_focus_in_fileman()

    def enum_all(self, callback):
        """
        Enum for all items.
        Until callback gets false.
        """
        items = tree_proc(self.tree, TREE_ITEM_ENUM, 0)
        if items:
            return self.enum_subitems(items[0][0], callback)

    def enum_subitems(self, item, callback):
        """
        Callback for all subitems of given item.
        Until callback gets false.
        """
        items = tree_proc(self.tree, TREE_ITEM_ENUM_EX, item)
        if items:
            for i in items:
                subitem = i['id']
                fn = i.get('data', '')
                if not callback(fn, subitem):
                    return False
                if not self.enum_subitems(subitem, callback):
                    return False
        return True


    def enum_all_getfolds(self, unfolds):
        items = tree_proc(self.tree, TREE_ITEM_ENUM, 0)
        if items:
            return self.enum_subitems_getfolds(items[0][0], unfolds)

    def enum_subitems_getfolds(self, item, unfolds):
        items = tree_proc(self.tree, TREE_ITEM_ENUM_EX, item)
        if items:
            for i in items:
                if i['sub_items']:
                    id = i['id']
                    prop = tree_proc(self.tree, TREE_ITEM_GET_PROPS, id)
                    if not prop['folded']:
                        fn = i.get('data', '')
                        if fn:
                            unfolds.append(fn)
                    self.enum_subitems_getfolds(id, unfolds)


    def enum_all_setfolds(self, unfolds):
        if not unfolds:
            return
        items = tree_proc(self.tree, TREE_ITEM_ENUM, 0)
        if items:
            return self.enum_subitems_setfolds(items[0][0], unfolds)

    def enum_subitems_setfolds(self, item, unfolds):
        items = tree_proc(self.tree, TREE_ITEM_ENUM_EX, item)
        if items:
            for i in items:
                if i['sub_items']:
                    id = i['id']
                    fn = i.get('data', '')
                    if fn in unfolds:
                        tree_proc(self.tree, TREE_ITEM_UNFOLD, id)
                        unfolds.remove(fn)
                    #else:
                    #    tree_proc(self.tree, TREE_ITEM_FOLD, id)
                    self.enum_subitems_setfolds(id, unfolds)


    def enum_all_fn(self, filename, and_open):
        """
        Enum for all items.
        Find 'filename', and focus its node.
        """
        items = tree_proc(self.tree, TREE_ITEM_ENUM, 0)
        if items:
            return self.enum_subitems_fn(items[0][0], filename, and_open)

    def enum_subitems_fn(self, item_src, filename, and_open):
        """
        Callback for all subitems of given item_src.
        When found 'filename', focus it and return False
        """
        def _need(dirpath):
            return filename.startswith(dirpath+os.sep)

        prop_list = tree_proc(self.tree, TREE_ITEM_ENUM_EX, item_src) or []
        for prop in prop_list:
            fn = prop['data']
            is_dir = prop['sub_items']

            if is_dir:
                if _need(fn):
                    node = prop['id']
                    tree_proc(self.tree, TREE_ITEM_UNFOLD, node)
                    if not self.enum_subitems_fn(node, filename, and_open):
                        return False

            elif fn==filename:
                node = prop['id']
                tree_proc(self.tree, TREE_ITEM_SELECT, node)
                tree_proc(self.tree, TREE_ITEM_SHOW, node)
                if is_dir:
                    tree_proc(self.tree, TREE_ITEM_UNFOLD, node)
                if and_open:
                    _file_open(fn)
                return False

        return True


    def enum_all_sel(self, filename):
        """
        Enum for all items.
        Find 'filename', and select/show its node.
        """
        items = tree_proc(self.tree, TREE_ITEM_ENUM, 0)
        if items:
            return self.enum_subitems_sel(items[0][0], filename)

    def enum_subitems_sel(self, item_src, filename):
        """
        Callback for all subitems of given item_src.
        When found 'filename', focus it and return False
        """

        prop_list = tree_proc(self.tree, TREE_ITEM_ENUM_EX, item_src) or []
        for prop in prop_list:
            fn = prop['data']
            is_dir = prop['sub_items']

            if is_dir:
                node = prop['id']
                if not self.enum_subitems_sel(node, filename):
                    return False

            elif fn==filename:
                node = prop['id']
                tree_proc(self.tree, TREE_ITEM_SELECT, node)
                tree_proc(self.tree, TREE_ITEM_SHOW, node)
                #print('enum_subitems_sel found node!')
                return False

        return True


    def menu_goto(self):
        """ Show menu-dialog with all files in project, and jump to chosen file """
        if not self.tree:
            msg_status(_('Project not opened'))
            return

        files = self.enum_all_files()
        if self.is_project_empty():
            msg_status(_('Project is empty'))
            return

        files.sort()
        for item in self.goto_history:
            if item in files:
                files.remove(item)
        files = self.goto_history + files
        files_nice = [os.path.basename(fn)+'\t'+collapse_filename(os.path.dirname(fn)) for fn in files]

        res = dlg_menu(DMENU_LIST_ALT+DMENU_NO_FULLFILTER, #fuzzy search is needed for users
                       files_nice,
                       caption=_('Go to file')
                       )
        if res is None:
            return
        fn = files[res]

        if fn in self.goto_history:
            self.goto_history.remove(fn)
        self.goto_history.insert(0, fn)

        and_open = self.options.get('goto_open', False) \
            or not app_proc(PROC_SHOW_SIDEPANEL_GET, '') \
            or app_proc(PROC_SIDEPANEL_GET, '')!='Project'
        self.jump_to_filename(fn, and_open)

    def jump_to_filename(self, filename, and_open=False):
        """ Find filename in entire project and focus its tree node """
        msg_status(_('Project jump: ') + collapse_filename(filename))
        return self.enum_all_fn(filename, and_open)

    def sync_to_ed(self):
        """ Jump to active editor file, if it's in project """
        if not self.tree:
            msg_status(_('Project not loaded'))
            return

        fn = ed.get_filename()
        if fn:
            if self.jump_to_filename(fn): #gets False if found
                msg_status(_('Cannot jump to file: ') + collapse_filename(fn))


    def tree_on_unfold(self, id_dlg, id_ctl, data='', info=''):
        info = self.get_info(data)
        path = self.get_location_by_index(data)

        if not path.is_dir():
            tree_proc(self.tree, TREE_ITEM_DELETE, data)
            if str(path) in self.project["nodes"]:
                self.project["nodes"].remove(str(path))
            return

        if info.image != self.ICON_DIR:
            return
        items = tree_proc(self.tree, TREE_ITEM_ENUM, data)
        if items:
            for handle, _ in items:
                tree_proc(self.tree, TREE_ITEM_DELETE, handle)

        self.action_refresh_int(data) # call _int version, to avoid recursion

    def tree_on_menu(self, id_dlg, id_ctl, data='', info=''):

        self.generate_context_menu()
        menu_proc(self.h_menu, MENU_SHOW, command='')


    def do_open_current_file(self, options):

        info = self.get_info(self.selected)
        if not info:
            return
        path = self.get_location_by_index(self.selected)
        if not path:
            return

        if info.image in [self.ICON_BAD, self.ICON_DIR, self.ICON_PROJ]:
            return

        if not os.path.isfile(str(path)):
            tree_proc(self.tree, TREE_ITEM_SET_ICON, self.selected, image_index=self.ICON_BAD)
            return

        _file_open(str(path), options=options)


    def get_open_options(self):

        s = '/preview' if self.options.get('preview', True) else ''
        s += ' /nozip /nontext-view-text'
        return s

    def tree_on_click(self, id_dlg, id_ctl, data='', info=''):

        # set folder in project as current folder for Open/Save-as dialogs
        node = self.selected
        if not node: # may be from some OnClick events
            return
        s = str(self.get_location_by_index(node))
        if s and not s.startswith('.'): # skip parasitic '.' for project root node
            if os.path.isdir(s):
                app_proc(PROC_SET_FOLDER, s)
                self.cur_dir = s
            elif os.path.isfile(s):
                app_proc(PROC_SET_FOLDER, os.path.dirname(s))
                self.cur_dir = os.path.dirname(s)

        if self.options.get('d_click', False):
            return
        self.do_open_current_file(self.get_open_options())

    def tree_on_click_dbl(self, id_dlg, id_ctl, data='', info=''):

        if not self.options.get('d_click', False):
            #turn off 'preview' tab kind on dbl-click
            ed.set_prop(PROP_PREVIEW, False)
            return
        self.do_open_current_file(self.get_open_options())


    def set_imagelist_size(self, theme_name, imglist):

        res = re.match(r'^\S+x(\d+)$', theme_name)
        if not res:
            return msg_box(_('Project Manager: bad icons folder name: "%s"') % theme_name, MB_OK+MB_ICONERROR)
        n = int(res.group(1))
        if not 8<=n<=64:
            return msg_box(_('Project Manager: bad icons size: "%s"') % theme_name, MB_OK+MB_ICONERROR)

        imagelist_proc(imglist, IMAGELIST_SET_SIZE, (n, n))

    def icon_init(self):

        self.icon_theme = self.options.get('icon_theme', 'vscode_16x16')
        self.set_imagelist_size(self.icon_theme, self.tree_imglist)

        self.icon_dir = os.path.join(app_path(APP_DIR_DATA), 'filetypeicons', self.icon_theme)
        if not os.path.isdir(self.icon_dir):
            self.icon_dir = os.path.join(app_path(APP_DIR_DATA), 'filetypeicons', 'vscode_16x16')

        self.icon_json = os.path.join(self.icon_dir, 'icons.json')
        self.icon_json_dict = json.loads(open(self.icon_json, encoding='utf8').read())
        self.icon_indexes = {}


    def icon_get(self, key):

        s = self.icon_indexes.get(key, None)
        if s:
            return s

        fn = self.icon_json_dict.get(key, None)
        if fn is None:
            n = self.ICON_ALL
            self.icon_indexes[key] = n
            return n

        fn = os.path.join(self.icon_dir, fn)
        n = imagelist_proc(self.tree_imglist, IMAGELIST_ADD, value=fn)
        if n is None:
            print(_('Project Manager: incorrect filetype icon:'), fn)
            n = self.ICON_ALL
        self.icon_indexes[key] = n
        return n

    def get_node_type(self):
        node_type = None
        if self.selected is not None:
            n = self.get_info(self.selected).image
            if n == self.ICON_PROJ: node_type = NODE_PROJECT
            elif n == self.ICON_DIR: node_type = NODE_DIR
            elif n == self.ICON_BAD: node_type = NODE_BAD
            else: node_type = NODE_FILE
        return node_type

    def form_key_down(self, id_dlg, id_ctl, data):
        if id_ctl in [VK_SPACE, VK_ENTER, VK_F4]:
            self.do_open_current_file(self.get_open_options())
            return False #block key
        elif id_ctl == VK_DELETE:
            node_type = self.get_node_type()
            if node_type == NODE_FILE:
                self.action_delete_file()
            elif node_type == NODE_DIR:
                self.action_delete_directory()
            return False #block key
        elif (id_ctl == VK_F5) or (data == S_CTRL_API and (id_ctl in (ord('r'), ord('R')))):
            self.action_refresh()
            return False #block key
        elif id_ctl == VK_F2:
            self.action_rename()
            return False #block key
        elif id_ctl == VK_F7:
            self.action_new_directory()
            return False #block key
        elif (data == S_CTRL_API and (id_ctl in (ord('n'), ord('N')))):
            self.action_new_file()
            return False #block key
        elif (data == S_CTRL_API and (id_ctl in (ord('x'), ord('X')))):
            self.action_cut()
            return False #block key
        elif (data == S_CTRL_API and (id_ctl in (ord('c'), ord('C')))):
            self.action_copy()
            return False #block key
        elif (data == S_CTRL_API and (id_ctl in (ord('v'), ord('V')))):
            self.action_paste()
            return False #block key

    def add_current_file(self):

        if not self.tree:
            self.init_panel(False)

        fn = ed.get_filename()
        self.add_node(fn)

    def add_current_dir(self):

        if not self.tree:
            self.init_panel(False)

        fn = ed.get_filename()
        if fn:
            d = os.path.dirname(fn)
            if not (IS_WIN and d.endswith(':\\')):
                self.add_node(d)

    def add_opened_files(self):

        if not self.tree:
            self.init_panel(False)

        for h in ed_handles():
            e = Editor(h)
            fn = e.get_filename()
            self.add_node(fn)


    def goto_main(self):
        if not self.tree:
            msg_status(_('Project not opened'))
            return

        fn = self.project.get('mainfile', '')
        if not fn:
            msg_status(_('Project main file is not set'))
            return
        self.jump_to_filename(fn)

    def open_main(self):
        fn = self.project.get('mainfile', '')
        if fn:
            _file_open(fn)
        else:
            msg_status(_('Project main file is not set'))

    def is_project_empty(self):
        return not bool(self.project['nodes'])

    def enum_all_files(self):
        files, dirs = [], []
        for root in self.project['nodes']:
            if os.path.isdir(root):
                dirs.append(root)
            elif os.path.isfile(root):
                files.append(root)

        while dirs:
            try:
                next_dir = dirs.pop(0)
                for found in os.scandir(next_dir):
                    # Ignoring symlinks prevents infinite loops with cyclic directory layouts
                    if found.is_dir() and not found.is_symlink() and not self.is_filename_ignored(found.path, True):
                        dirs.append(found.path)
                    elif found.is_file() and not self.is_filename_ignored(found.path, False):
                        files.append(found.path)
            except (OSError, FileNotFoundError):
                pass # Permissions issue. Not much we can do
        return files

    def open_all(self):
        if not self.tree:
            msg_status(_('Project not opened'))
            return

        files = self.enum_all_files()
        if self.is_project_empty():
            msg_status(_('Project is empty'))
            return

        if msg_box(_('Open all %d file(s) in editor?') % len(files), MB_OKCANCEL+MB_ICONQUESTION)!=ID_OK:
            return

        for (i, fn) in enumerate(files):
            _file_open(fn, options="/nontext-cancel")
            if i%10==0:
                app_idle(False)

    def on_open(self, ed_self):

        if self.options.get('check_git', False):
            self.init_panel(False)

            if not self.project_file_path:
                fn = ed_self.get_filename('*')
                self.action_project_for_git(fn)

        self.on_tab_change(ed_self)

    def action_project_for_git(self, filename):

        dir = os.path.dirname(filename)
        while True:
            fn = os.path.join(dir, '.git')
            fn2 = os.path.join(dir, '.svn')
            if os.path.isdir(fn) or os.path.isdir(fn2):
                self.init_panel()
                self.new_project(True, False)
                self.add_node(dir)
                self.jump_to_filename(filename)
                print(_('Project Manager: opened project for version-controlled folder: ')+dir)
                return

            d = os.path.dirname(dir)
            if d=='/':
                return
            if d==dir:
                return
            dir = d

    def session_cur_name(self):

        fn_proj = str(self.project_file_path)
        if not fn_proj:
            return ''

        s = ''
        sess = app_path(APP_FILE_SESSION)
        if '|' in sess:
            l = sess.split('|')
            if l[0] == fn_proj:
                s = l[1]
                k = '/sessions/'
                if s.startswith(k):
                    s = s[len(k):]
        return s

    def session_get_names(self):

        res = []
        fn = str(self.project_file_path)
        if fn and os.path.isfile(fn):
            with open(fn, 'r', encoding='utf8') as f:
                data = json.load(f)
                k = data.get('sessions')
                if type(k)==dict:
                    res = list(k.keys())
        return res

    def session_def(self):

        names = self.session_get_names()
        if not names:
            return msg_status(_('No project sessions'))
        names = [_('(none)')]+names

        curname = self.project.get('def_session', '')
        if curname in names:
            focused = names.index(curname)
        else:
            focused = 0

        res = dlg_menu(DMENU_LIST, names, focused=focused, caption=_('Set default project session'))
        if res is None:
            return
        curname = names[res] if res>0 else ''
        self.project['def_session'] = curname

        fn = self.project_file_path
        if fn and os.path.isfile(fn):
            with open(fn, 'r', encoding='utf8') as f:
                data = json.load(f)
            data['def_session'] = curname
            with open(fn, 'w', encoding='utf8') as f:
                json.dump(data, f, indent=2)

    def session_delete(self):

        if self.is_project_empty():
            msg_status(_('Project is empty'))
            return

        names = self.session_get_names()
        if not names:
            return msg_status(_('No project sessions'))

        res = dlg_menu(DMENU_LIST, names, caption=_('Delete project session'))
        if res is None:
            return
        name = names[res]

        fn = self.project_file_path
        if fn and os.path.isfile(fn):
            # 1. delete session from .cuda-proj file
            with open(fn, 'r', encoding='utf8') as f:
                data = json.load(f)
            if data.get('sessions'):
                del data['sessions'][name]
            with open(fn, 'w', encoding='utf8') as f:
                json.dump(data, f, indent=2)

            # 2. also delete from memory dict (self.project)
            if self.project.get('sessions'):
                del self.project['sessions'][name]

            # 3. and forget current session if name is the same
            if name == self.session_cur_name():
                app_proc(PROC_SET_SESSION, DEF_SES)

    def session_delete_all(self):

        fn = self.project_file_path
        if fn and os.path.isfile(fn):
            with open(fn, 'r', encoding='utf8') as f:
                data = json.load(f)
            if data.get('sessions'):
                del data['sessions']
            with open(fn, 'w', encoding='utf8') as f:
                json.dump(data, f, indent=2)

    def session_save_as(self):

        fn = str(self.project_file_path)
        if not fn:
            msg_status(_('Untitled project'))

        if self.is_project_empty():
            msg_status(_('Project is empty'))
            return

        self.close_foreign_tabs(True)

        names = self.session_get_names()
        s = 'new'
        while True:
            s = dlg_input(_('Save session with name:'), s)
            if s is None:
                return
            s = s.strip()
            if not s:
                msg_status(_('Empty session name'))
                continue
            if not is_session_name(s):
                msg_status(_('Not allowed char(s) in the session name'))
                continue
            if s in names:
                msg_status(_('Session "%s" already exists')%s)
                continue
            break

        sess = fn+'|/sessions/'+s
        app_proc(PROC_SAVE_SESSION, sess)
        app_proc(PROC_SET_SESSION, sess)

        # update "self.project" dict
        with open(fn, 'r', encoding='utf8') as f:
            _newdata = json.load(f)
        self.project['sessions'] = _newdata['sessions']

    def session_load_menu(self):

        names = self.session_get_names()
        res = dlg_menu(DMENU_LIST, names, caption=_('Open project session'))
        if res is None:
            return
        self.session_load(names[res])

    def session_load(self, name='', confirm_save=True):

        if not name:
            return

        if not name in self.session_get_names():
            msg_status(_('Project session "%s" not found')%name)
            return

        fn = str(self.project_file_path)
        if not fn:
            msg_status(_('Untitled project'))

        if confirm_save:
            sess = app_path(APP_FILE_SESSION)
            sess = collapse_filename(sess)
            if msg_box(_('Save current state to the session "%s"?')%sess, MB_OKCANCEL+MB_ICONQUESTION)==ID_OK:
                app_proc(PROC_SAVE_SESSION, sess)

        app_proc(PROC_SET_SESSION, DEF_SES)

        fn += '|/sessions/'+name
        app_proc(PROC_LOAD_SESSION, fn)

    def is_project_filename(self, filename):
        '''Deprecated func'''

        if not filename:
            return False
        for fn in self.project["nodes"]:
            if os.path.isdir(fn):
                if filename.startswith(fn+os.sep):
                    return True
            else:
                if filename==fn:
                    return True
        return False

    def close_foreign_tabs(self, confirm=True):

        if not self.options.get('close_ext', True):
            return

        # don't detect if project is empty
        items = self.project['nodes']
        if not items:
            return

        files, dirs = [], []
        for root in items:
            if os.path.isdir(root):
                dirs.append(root)
            else:
                files.append(root)

        def is_from_proj(fn):
            if not fn: # untitled tabs: False
                return False
            for d in dirs:
                if fn.startswith(d+os.sep):
                    return True
            for f in files:
                if f==fn:
                    return True
            return False

        import cudatext_cmd as cmds

        res = []
        for h in ed_handles():
            e = Editor(h)
            fn = e.get_filename('*')

            #skip empty tabs
            if (not fn) and (not e.get_text_all()):
                continue

            if not is_from_proj(fn):
                res.append((h, fn))

        if res:
            msg_ = _('CudaText has opened %d tab(s) not belonging to the project "%s". Close them?')
            msg = msg_%(len(res), self.get_project_name())

            names = []
            for (h, fn) in res:
                if fn:
                    names.append(collapse_filename(fn))
                else:
                    e = Editor(h)
                    names.append(e.get_prop(PROP_TAB_TITLE))
                if len(names)>=8:
                    names.append('...')
                    break
            msg += '\n\n'+'\n'.join(names)

            if not confirm or msg_box(msg, MB_OKCANCEL+MB_ICONQUESTION)==ID_OK:
                for (h, fn) in reversed(res):
                    e = Editor(h)
                    e.set_prop(PROP_MODIFIED, False)
                    e.cmd(cmds.cmd_FileClose)
                    time.sleep(0.2)

    def session_forget(self):

        app_proc(PROC_SET_SESSION, DEF_SES)

    def session_forget_ex(self):

        self.session_forget()

        import cudatext_cmd as cmds
        ed.cmd(cmds.cmd_FileCloseAll)

    def on_delete_file(self, ed_self, fn):

        #print('on_delete_file', fn)
        if fn in self.project["nodes"]:
            self.project["nodes"].remove(fn)
            self.action_refresh()
            if self.project_file_path:
                self.action_save_project_as(self.project_file_path)

    def session_save(self, and_forget):

        cur_fn = str(self.project_file_path)
        cur_sess = self.session_cur_name()
        if cur_fn and cur_sess:
            sess = cur_fn+'|/sessions/'+cur_sess
            app_proc(PROC_SAVE_SESSION, sess)
            if and_forget:
                app_proc(PROC_SET_SESSION, DEF_SES)

    def is_path_in_root(self, path):

        return str(path) in self.project['nodes']

    def on_tab_change(self, ed_self):

        if self.options.get('always_sync', False):
            if self.tree:
                self.sync_to_ed()
