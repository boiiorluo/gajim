# -*- coding:utf-8 -*-
## src/application.py
##
## Copyright (C) 2016 Emmanuel Gil Peyrot <linkmauve AT linkmauve.fr>
##
## This file is part of Gajim.
##
## Gajim is free software; you can redistribute it and/or modify
## it under the terms of the GNU General Public License as published
## by the Free Software Foundation; version 3 only.
##
## Gajim is distributed in the hope that it will be useful,
## but WITHOUT ANY WARRANTY; without even the implied warranty of
## MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
## GNU General Public License for more details.
##
## You should have received a copy of the GNU General Public License
## along with Gajim. If not, see <http://www.gnu.org/licenses/>.
##

import sys
import os
import logging
import signal
import locale
from gi.repository import GLib, Gio, Gtk
from common import i18n
from common import logging_helpers
from common import crypto
try:
    PYOPENSSL_PRNG_PRESENT = True
    import OpenSSL.rand
except ImportError:
    print('PyOpenSSL not available, impossible to generate entropy', file=sys.stderr)
    PYOPENSSL_PRNG_PRESENT = False

logging_helpers.init(sys.stderr.isatty())
log = logging.getLogger('gajim.gajim')

MIN_NBXMPP_VER = "0.5.3"


class GajimApplication(Gtk.Application):
    '''Main class handling activation and command line.'''

    def __init__(self):
        Gtk.Application.__init__(self, application_id='org.gajim.Gajim',
                                 flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)

        self.add_main_option('version', ord('V'), GLib.OptionFlags.NONE,
                             GLib.OptionArg.NONE,
                             _('Show the application\'s version'))
        self.add_main_option('quiet', ord('q'), GLib.OptionFlags.NONE,
                             GLib.OptionArg.NONE,
                             _('Show only critical errors'))
        self.add_main_option('separate', ord('s'), GLib.OptionFlags.NONE,
                             GLib.OptionArg.NONE,
                             _('Separate profile files completely (even '
                               'history db and plugins)'))
        self.add_main_option('verbose', ord('v'), GLib.OptionFlags.NONE,
                             GLib.OptionArg.NONE,
                             _('Print XML stanzas and other debug '
                               'information'))
        self.add_main_option('profile', ord('p'), GLib.OptionFlags.NONE,
                             GLib.OptionArg.STRING,
                             _('Use defined profile in configuration '
                               'directory'), 'NAME')
        self.add_main_option('config-path', ord('c'), GLib.OptionFlags.NONE,
                             GLib.OptionArg.STRING,
                             _('Set configuration directory'), 'PATH')
        self.add_main_option('loglevel', ord('l'), GLib.OptionFlags.NONE,
                             GLib.OptionArg.STRING,
                             _('Configure logging system'), 'LEVEL')

        self.profile = ''
        self.config_path = None
        self.profile_separation = False
        self.interface = None
        self.rng_seed = None

        GLib.set_prgname('gajim')
        GLib.set_application_name('Gajim')

    def do_startup(self):
        Gtk.Application.do_startup(self)

        import gtkexcepthook
        gtkexcepthook.init()

        try:
            import nbxmpp
        except ImportError:
            print('Gajim needs python-nbxmpp to run. Quitting…')
            sys.exit(1)

        from distutils.version import LooseVersion as V
        if V(nbxmpp.__version__) < V(MIN_NBXMPP_VER):
            print('Gajim needs python-nbxmpp >= %s to run. '
                  'Quitting…' % MIN_NBXMPP_VER)
            sys.exit(1)

        # Create and initialize Application Paths & Databases
        import common.configpaths
        common.configpaths.gajimpaths.init(
            self.config_path, self.profile, self.profile_separation)

        if hasattr(sys, 'frozen'):
            self.frozen_logging(common.configpaths.gajimpaths.config_root)

        from common import gajim
        from common import check_paths
        from common import exceptions
        from common import logger
        from common import caps_cache
        try:
            gajim.logger = logger.Logger()
            caps_cache.initialize(gajim.logger)
            check_paths.check_and_possibly_create_paths()
        except exceptions.DatabaseMalformed:
            dlg = Gtk.MessageDialog(
                None,
                Gtk.DialogFlags.DESTROY_WITH_PARENT | Gtk.DialogFlags.MODAL,
                Gtk.MessageType.ERROR,
                Gtk.ButtonsType.OK,
                _('Database Error'))
            dlg.format_secondary_text(
                _('The database file (%s) cannot be read. Try to repair it '
                  '(see http://trac.gajim.org/wiki/DatabaseBackup) or remove it '
                  '(all history will be lost).') % gajim.gajimpaths['LOG_DB'])
            dlg.run()
            dlg.destroy()
            sys.exit()

        if os.name == 'nt':
            import gettext
            # needed for docutils
            sys.path.append('.')
            APP = 'gajim'
            DIR = '../po'
            lang, enc = locale.getdefaultlocale()
            os.environ['LANG'] = lang
            gettext.bindtextdomain(APP, DIR)
            gettext.textdomain(APP)
            gettext.install(APP, DIR)

        # This is for Windows translation which is currently not
        # working on GTK 3.18.9
        #    locale.setlocale(locale.LC_ALL, '')
        #    import ctypes
        #    import ctypes.util
        #    libintl_path = ctypes.util.find_library('intl')
        #    if libintl_path == None:
        #        local_intl = os.path.join('gtk', 'bin', 'intl.dll')
        #        if os.path.exists(local_intl):
        #            libintl_path = local_intl
        #    if libintl_path == None:
        #        raise ImportError('intl.dll library not found')
        #    libintl = ctypes.cdll.LoadLibrary(libintl_path)
        #    libintl.bindtextdomain(APP, DIR)
        #    libintl.bind_textdomain_codeset(APP, 'UTF-8')
        #    plugins_locale_dir = os.path.join(common.configpaths.gajimpaths[
        #       'PLUGINS_USER'], 'locale').encode(locale.getpreferredencoding())
        #    libintl.bindtextdomain('gajim_plugins', plugins_locale_dir)
        #    libintl.bind_textdomain_codeset('gajim_plugins', 'UTF-8')

        if Gtk.Widget.get_default_direction() == Gtk.TextDirection.RTL:
            i18n.direction_mark = '\u200F'

        from ctypes import CDLL
        from ctypes.util import find_library
        import platform

        sysname = platform.system()
        if sysname in ('Linux', 'FreeBSD', 'OpenBSD', 'NetBSD'):
            libc = CDLL(find_library('c'))

            # The constant defined in <linux/prctl.h> which is used to set the name
            # of the process.
            PR_SET_NAME = 15

            if sysname == 'Linux':
                libc.prctl(PR_SET_NAME, 'gajim')
            elif sysname in ('FreeBSD', 'OpenBSD', 'NetBSD'):
                libc.setproctitle('gajim')

        # Seed the OpenSSL pseudo random number generator from file and initialize
        if PYOPENSSL_PRNG_PRESENT:
            self.rng_seed = gajim.gajimpaths['RNG_SEED']
            # Seed from file
            try:
                OpenSSL.rand.load_file(self.rng_seed)
            except TypeError:
                OpenSSL.rand.load_file(self.rng_seed.encode('utf-8'))
            crypto.add_entropy_sources_OpenSSL()
            try:
                OpenSSL.rand.write_file(self.rng_seed)
            except TypeError:
                OpenSSL.rand.write_file(self.rng_seed.encode('utf-8'))

        def sigint_cb(num, stack):
            print('SIGINT/SIGTERM received')
            self.quit()
        # ^C exits the application normally
        signal.signal(signal.SIGINT, sigint_cb)
        signal.signal(signal.SIGTERM, sigint_cb)

        log.info("Encodings: d:%s, fs:%s, p:%s", sys.getdefaultencoding(),
                 sys.getfilesystemencoding(), locale.getpreferredencoding())

        from gui_interface import Interface
        self.interface = Interface()
        self.interface.run(self)

    def do_activate(self):
        Gtk.Application.do_activate(self)
        # If a second instance starts do_activate() is called
        # We bringt the Roster window to the front, GTK exits afterwards.
        if self.interface:
            self.interface.roster.window.present()
            return

    def do_shutdown(self, *args):
        Gtk.Application.do_shutdown(self)
        # Save the entropy from OpenSSL PRNG
        if PYOPENSSL_PRNG_PRESENT and self.rng_seed:
            try:
                OpenSSL.rand.write_file(self.rng_seed)
            except TypeError:
                OpenSSL.rand.write_file(self.rng_seed.encode('utf-8'))
        # Shutdown GUI and save config
        if hasattr(self.interface, 'roster') and self.interface.roster:
            self.interface.roster.prepare_quit()

    def do_handle_local_options(self, options: GLib.VariantDict) -> int:
        if options.contains('profile'):
            # Incorporate profile name into application id
            # to have a single app instance for each profile.
            profile = options.lookup_value('profile').get_string()
            app_id = '%s.%s' % (self.get_application_id(), profile)
            self.set_application_id(app_id)
            self.profile = profile
        if options.contains('separate'):
            self.profile_separation = True
        if options.contains('config-path'):
            self.config_path = options.lookup_value('config-path').get_string()
        if options.contains('version'):
            from common.defs import version
            print(version)
            return 0
        return -1

    def do_command_line(self, command_line: Gio.ApplicationCommandLine) -> int:
        Gtk.Application.do_command_line(self, command_line)
        options = command_line.get_options_dict()
        if options.contains('quiet'):
            logging_helpers.set_quiet()
        if options.contains('verbose'):
            logging_helpers.set_verbose()
        if options.contains('loglevel'):
            string = options.lookup_value('loglevel').get_string()
            logging_helpers.set_loglevels(string)
        self.activate()
        return 0

    def frozen_logging(self, path):
        import warnings
        if not os.path.exists(path):
            os.mkdir(path, 0o700)

        class MyStd(object):
            _file = None
            _error = None
            log_file = os.path.join(path, 'gajim.log')

            def write(self, text):
                if self._file is None and self._error is None:
                    try:
                        self._file = open(self.log_file, 'a')
                    except Exception as details:
                        self._error = details
                if self._file is not None:
                    self._file.write(text)
                    self._file.flush()

            def flush(self):
                if self._file is not None:
                    self._file.flush()

            def isatty(self):
                return False

        outerr = MyStd()
        sys.stdout = outerr
        sys.stderr = outerr
        warnings.filterwarnings(action='ignore')
