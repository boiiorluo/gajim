# Copyright (C) 2006 Dimitur Kirov <dkirov AT gmail.com>
# Copyright (C) 2006-2014 Yann Leboulanger <asterix AT lagaule.org>
# Copyright (C) 2006-2008 Jean-Marie Traissard <jim AT lapin.org>
#                         Nikos Kouremenos <kourem AT gmail.com>
#                         Travis Shirk <travis AT pobox.com>
# Copyright (C) 2007 Lukas Petrovicky <lukas AT petrovicky.net>
#                    Julien Pivotto <roidelapluie AT gmail.com>
# Copyright (C) 2007-2008 Brendan Taylor <whateley AT gmail.com>
#                         Stephan Erb <steve-e AT h3c.de>
# Copyright (C) 2008 Jonathan Schleifer <js-gajim AT webkeks.org>
#
# This file is part of Gajim.
#
# Gajim is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published
# by the Free Software Foundation; version 3 only.
#
# Gajim is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Gajim. If not, see <http://www.gnu.org/licenses/>.

import os
import re
import time
from tempfile import TemporaryDirectory

from gi.repository import Gtk
from gi.repository import Gdk
from gi.repository import GLib
from gi.repository import Gio

from gajim.common import events
from gajim.common import app
from gajim.common import helpers
from gajim.common import ged
from gajim.common import i18n
from gajim.common.i18n import _
from gajim.common.helpers import AdditionalDataDict
from gajim.common.contacts import GC_Contact
from gajim.common.connection_handlers_events import MessageOutgoingEvent
from gajim.common.const import StyleAttr
from gajim.common.const import Chatstate

from gajim import gtkgui_helpers
from gajim import message_control

from gajim.message_control import MessageControl
from gajim.conversation_textview import ConversationTextview
from gajim.message_textview import MessageTextView

from gajim.gtk.dialogs import NewConfirmationDialog
from gajim.gtk.dialogs import DialogButton
from gajim.gtk import util
from gajim.gtk.util import convert_rgb_to_hex
from gajim.gtk.util import at_the_end
from gajim.gtk.util import get_show_in_roster
from gajim.gtk.util import get_show_in_systray
from gajim.gtk.util import get_primary_accel_mod
from gajim.gtk.util import get_hardware_key_codes
from gajim.gtk.emoji_chooser import emoji_chooser

from gajim.command_system.implementation.middleware import ChatCommandProcessor
from gajim.command_system.implementation.middleware import CommandTools

# The members of these modules are not referenced directly anywhere in this
# module, but still they need to be kept around. Importing them automatically
# registers the contained CommandContainers with the command system, thereby
# populating the list of available commands.
# pylint: disable=unused-import
from gajim.command_system.implementation import standard
from gajim.command_system.implementation import execute
# pylint: enable=unused-import

if app.is_installed('GSPELL'):
    from gi.repository import Gspell  # pylint: disable=ungrouped-imports


################################################################################
class ChatControlBase(MessageControl, ChatCommandProcessor, CommandTools):
    """
    A base class containing a banner, ConversationTextview, MessageTextView
    """

    # This is needed so copying text from the conversation textview
    # works with different language layouts. Pressing the key c on a russian
    # layout yields another keyval than with the english layout.
    # So we match hardware keycodes instead of keyvals.
    # Multiple hardware keycodes can trigger a keyval like Gdk.KEY_c.
    keycodes_c = get_hardware_key_codes(Gdk.KEY_c)

    def make_href(self, match):
        url_color = app.css_config.get_value('.gajim-url', StyleAttr.COLOR)
        color = convert_rgb_to_hex(url_color)
        url = match.group()
        if '://' not in url:
            url = 'https://' + url
        return '<a href="%s"><span foreground="%s">%s</span></a>' % (
            url, color, match.group())

    def get_nb_unread(self):
        jid = self.contact.jid
        if self.resource:
            jid += '/' + self.resource
        type_ = self.type_id
        return len(app.events.get_events(self.account, jid, ['printed_' + type_,
                type_]))

    def draw_banner(self):
        """
        Draw the fat line at the top of the window that houses the icon, jid, etc

        Derived types MAY implement this.
        """
        self.draw_banner_text()
        self._update_banner_state_image()
        app.plugin_manager.gui_extension_point('chat_control_base_draw_banner',
            self)

    def update_toolbar(self):
        """
        update state of buttons in toolbar
        """
        self._update_toolbar()
        app.plugin_manager.gui_extension_point(
            'chat_control_base_update_toolbar', self)

    def draw_banner_text(self):
        """
        Derived types SHOULD implement this
        """

    def update_ui(self):
        """
        Derived types SHOULD implement this
        """
        self.draw_banner()

    def repaint_themed_widgets(self):
        """
        Derived types MAY implement this
        """
        self.draw_banner()

    def _update_banner_state_image(self):
        """
        Derived types MAY implement this
        """

    def _update_toolbar(self):
        """
        Derived types MAY implement this
        """

    def _nec_our_status(self, obj):
        if self.account != obj.conn.name:
            return
        if obj.show == 'offline' or (obj.show == 'invisible' and \
        obj.conn.is_zeroconf):
            self.got_disconnected()
        else:
            # Other code rejoins all GCs, so we don't do it here
            if not self.type_id == message_control.TYPE_GC:
                self.got_connected()
        if self.parent_win:
            self.parent_win.redraw_tab(self)

    def setup_seclabel(self):
        self.seclabel_combo.hide()
        self.seclabel_combo.set_no_show_all(True)
        lb = Gtk.ListStore(str)
        self.seclabel_combo.set_model(lb)
        cell = Gtk.CellRendererText()
        cell.set_property('xpad', 5)  # padding for status text
        self.seclabel_combo.pack_start(cell, True)
        # text to show is in in first column of liststore
        self.seclabel_combo.add_attribute(cell, 'text', 0)
        con = app.connections[self.account]
        jid = self.contact.jid
        if self.TYPE_ID == 'pm':
            jid = self.gc_contact.room_jid
        if con.get_module('SecLabels').supported:
            con.get_module('SecLabels').request_catalog(jid)

    def _sec_labels_received(self, event):
        if event.account != self.account:
            return

        jid = self.contact.jid
        if self.TYPE_ID == 'pm':
            jid = self.gc_contact.room_jid

        if event.jid != jid:
            return
        model = self.seclabel_combo.get_model()
        model.clear()

        sel = 0
        _label, labellist, default = event.catalog
        for index, label in enumerate(labellist):
            model.append([label])
            if label == default:
                sel = index

        self.seclabel_combo.set_active(sel)
        self.seclabel_combo.set_no_show_all(False)
        self.seclabel_combo.show_all()

    def __init__(self, type_id, parent_win, widget_name, contact, acct,
    resource=None):
        # Undo needs this variable to know if space has been pressed.
        # Initialize it to True so empty textview is saved in undo list
        self.space_pressed = True

        if resource is None:
            # We very likely got a contact with a random resource.
            # This is bad, we need the highest for caps etc.
            c = app.contacts.get_contact_with_highest_priority(acct,
                contact.jid)
            if c and not isinstance(c, GC_Contact):
                contact = c

        MessageControl.__init__(self, type_id, parent_win, widget_name,
            contact, acct, resource=resource)

        if self.TYPE_ID != message_control.TYPE_GC:
            # Create banner and connect signals
            widget = self.xml.get_object('banner_eventbox')
            id_ = widget.connect('button-press-event',
                self._on_banner_eventbox_button_press_event)
            self.handlers[id_] = widget

        self.urlfinder = re.compile(
            r"(www\.(?!\.)|[a-z][a-z0-9+.-]*://)[^\s<>'\"]+[^!,\.\s<>\)'\"\]]")

        self.banner_status_label = self.xml.get_object('banner_label')
        if self.banner_status_label is not None:
            id_ = self.banner_status_label.connect('populate_popup',
                self.on_banner_label_populate_popup)
            self.handlers[id_] = self.banner_status_label

        # Init DND
        self.TARGET_TYPE_URI_LIST = 80
        self.dnd_list = [Gtk.TargetEntry.new('text/uri-list', 0,
            self.TARGET_TYPE_URI_LIST), Gtk.TargetEntry.new('MY_TREE_MODEL_ROW',
            Gtk.TargetFlags.SAME_APP, 0)]
        id_ = self.widget.connect('drag_data_received',
            self._on_drag_data_received)
        self.handlers[id_] = self.widget
        self.widget.drag_dest_set(Gtk.DestDefaults.MOTION |
            Gtk.DestDefaults.HIGHLIGHT | Gtk.DestDefaults.DROP,
            self.dnd_list, Gdk.DragAction.COPY)

        # Create textviews and connect signals
        self.conv_textview = ConversationTextview(self.account)
        id_ = self.conv_textview.connect('quote', self.on_quote)
        self.handlers[id_] = self.conv_textview.tv
        id_ = self.conv_textview.tv.connect('key_press_event',
            self._conv_textview_key_press_event)
        self.handlers[id_] = self.conv_textview.tv
        # FIXME: DND on non editable TextView, find a better way
        self.drag_entered = False
        id_ = self.conv_textview.tv.connect('drag_data_received',
            self._on_drag_data_received)
        self.handlers[id_] = self.conv_textview.tv
        id_ = self.conv_textview.tv.connect('drag_motion', self._on_drag_motion)
        self.handlers[id_] = self.conv_textview.tv
        id_ = self.conv_textview.tv.connect('drag_leave', self._on_drag_leave)
        self.handlers[id_] = self.conv_textview.tv
        self.conv_textview.tv.drag_dest_set(Gtk.DestDefaults.MOTION |
            Gtk.DestDefaults.HIGHLIGHT | Gtk.DestDefaults.DROP,
            self.dnd_list, Gdk.DragAction.COPY)

        self.conv_scrolledwindow = self.xml.get_object(
            'conversation_scrolledwindow')
        self.conv_scrolledwindow.add(self.conv_textview.tv)
        widget = self.conv_scrolledwindow.get_vadjustment()
        id_ = widget.connect('changed',
            self.on_conversation_vadjustment_changed)
        self.handlers[id_] = widget

        vscrollbar = self.conv_scrolledwindow.get_vscrollbar()
        id_ = vscrollbar.connect('button-release-event',
                                 self._on_scrollbar_button_release)
        self.handlers[id_] = vscrollbar

        self.correcting = False
        self.last_sent_msg = None

        # add MessageTextView to UI and connect signals
        self.msg_textview = MessageTextView()
        self.msg_scrolledwindow = ScrolledWindow()
        self.msg_scrolledwindow.add(self.msg_textview)

        hbox = self.xml.get_object('hbox')
        hbox.pack_start(self.msg_scrolledwindow, True, True, 0)

        id_ = self.msg_textview.connect('paste-clipboard',
            self._on_message_textview_paste_event)
        self.handlers[id_] = self.msg_textview
        id_ = self.msg_textview.connect('key_press_event',
            self._on_message_textview_key_press_event)
        self.handlers[id_] = self.msg_textview
        id_ = self.msg_textview.connect('populate_popup',
            self.on_msg_textview_populate_popup)
        self.handlers[id_] = self.msg_textview
        # Setup DND
        id_ = self.msg_textview.connect('drag_data_received',
            self._on_drag_data_received)
        self.handlers[id_] = self.msg_textview
        self.msg_textview.drag_dest_set(Gtk.DestDefaults.MOTION |
            Gtk.DestDefaults.HIGHLIGHT, self.dnd_list, Gdk.DragAction.COPY)

        self._overlay = self.xml.get_object('overlay')

        # the following vars are used to keep history of user's messages
        self.sent_history = []
        self.sent_history_pos = 0
        self.received_history = []
        self.received_history_pos = 0
        self.orig_msg = None

        self.set_emoticon_popover()

        # Attach speller
        self.set_speller()
        self.conv_textview.tv.show()

        # For XEP-0172
        self.user_nick = None

        self.command_hits = []
        self.last_key_tabs = False

        # Security Labels
        self.seclabel_combo = self.xml.get_object('label_selector')

        con = app.connections[self.account]
        con.get_module('Chatstate').set_active(self.contact)

        id_ = self.msg_textview.connect('text-changed',
            self._on_message_tv_buffer_changed)
        self.handlers[id_] = self.msg_textview
        if parent_win is not None:
            id_ = parent_win.window.connect('motion-notify-event',
                self._on_window_motion_notify)
            self.handlers[id_] = parent_win.window

        self.encryption = self.get_encryption_state()
        self.conv_textview.encryption_enabled = self.encryption is not None

        # PluginSystem: adding GUI extension point for ChatControlBase
        # instance object (also subclasses, eg. ChatControl or GroupchatControl)
        app.plugin_manager.gui_extension_point('chat_control_base', self)

        app.ged.register_event_handler('our-show', ged.GUI1,
            self._nec_our_status)
        app.ged.register_event_handler('ping-sent', ged.GUI1,
            self._nec_ping)
        app.ged.register_event_handler('ping-reply', ged.GUI1,
            self._nec_ping)
        app.ged.register_event_handler('ping-error', ged.GUI1,
            self._nec_ping)
        app.ged.register_event_handler('sec-catalog-received', ged.GUI1,
            self._sec_labels_received)
        app.ged.register_event_handler('style-changed', ged.GUI1,
            self._style_changed)

        # This is basically a very nasty hack to surpass the inability
        # to properly use the super, because of the old code.
        CommandTools.__init__(self)

    def add_actions(self):
        action = Gio.SimpleAction.new_stateful(
            "set-encryption-%s" % self.control_id,
            GLib.VariantType.new("s"),
            GLib.Variant("s", self.encryption or 'disabled'))
        action.connect("change-state", self.change_encryption)
        self.parent_win.window.add_action(action)

        action = Gio.SimpleAction.new(
            'send-file-%s' % self.control_id, None)
        action.connect('activate', self._on_send_file)
        action.set_enabled(False)
        self.parent_win.window.add_action(action)

        action = Gio.SimpleAction.new(
            'send-file-httpupload-%s' % self.control_id, None)
        action.connect('activate', self._on_send_httpupload)
        action.set_enabled(False)
        self.parent_win.window.add_action(action)

        action = Gio.SimpleAction.new(
            'send-file-jingle-%s' % self.control_id, None)
        action.connect('activate', self._on_send_jingle)
        action.set_enabled(False)
        self.parent_win.window.add_action(action)

    # Actions

    def change_encryption(self, action, param):
        encryption = param.get_string()
        if encryption == 'disabled':
            encryption = None

        if self.encryption == encryption:
            return

        if encryption:
            plugin = app.plugin_manager.encryption_plugins[encryption]
            if not plugin.activate_encryption(self):
                return

        action.set_state(param)
        self.set_encryption_state(encryption)
        self.set_encryption_menu_icon()
        self.set_lock_image()

    def set_encryption_state(self, encryption):
        config_key = '%s-%s' % (self.account, self.contact.jid)
        self.encryption = encryption
        self.conv_textview.encryption_enabled = encryption is not None
        app.config.set_per('encryption', config_key,
                           'encryption', self.encryption or '')

    def get_encryption_state(self):
        config_key = '%s-%s' % (self.account, self.contact.jid)
        state = app.config.get_per('encryption', config_key, 'encryption')
        if not state:
            return None
        if state not in app.plugin_manager.encryption_plugins:
            self.set_encryption_state(None)
            return None
        return state

    def set_encryption_menu_icon(self):
        image = self.encryption_menu.get_image()
        if image is None:
            image = Gtk.Image()
            self.encryption_menu.set_image(image)
        if not self.encryption:
            image.set_from_icon_name('channel-insecure-symbolic',
                                     Gtk.IconSize.MENU)
        else:
            image.set_from_icon_name('channel-secure-symbolic',
                                     Gtk.IconSize.MENU)

    def set_speller(self):
        if not app.is_installed('GSPELL') or not app.config.get('use_speller'):
            return

        gspell_lang = self.get_speller_language()
        if gspell_lang is None:
            return

        spell_checker = Gspell.Checker.new(gspell_lang)
        spell_buffer = Gspell.TextBuffer.get_from_gtk_text_buffer(
            self.msg_textview.get_buffer())
        spell_buffer.set_spell_checker(spell_checker)
        spell_view = Gspell.TextView.get_from_gtk_text_view(self.msg_textview)
        spell_view.set_inline_spell_checking(False)
        spell_view.set_enable_language_menu(True)

        spell_checker.connect('notify::language', self.on_language_changed)

    def get_speller_language(self):
        per_type = 'contacts'
        if self.type_id == 'gc':
            per_type = 'rooms'
        lang = app.config.get_per(
            per_type, self.contact.jid, 'speller_language')
        if not lang:
            # use the default one
            lang = app.config.get('speller_language')
            if not lang:
                lang = i18n.LANG
        gspell_lang = Gspell.language_lookup(lang)
        if gspell_lang is None:
            gspell_lang = Gspell.language_get_default()
        return gspell_lang

    def on_language_changed(self, checker, param):
        gspell_lang = checker.get_language()
        per_type = 'contacts'
        if self.type_id == message_control.TYPE_GC:
            per_type = 'rooms'
        if not app.config.get_per(per_type, self.contact.jid):
            app.config.add_per(per_type, self.contact.jid)
        app.config.set_per(per_type, self.contact.jid,
                           'speller_language', gspell_lang.get_code())

    def on_banner_label_populate_popup(self, label, menu):
        """
        Override the default context menu and add our own menuitems
        """
        item = Gtk.SeparatorMenuItem.new()
        menu.prepend(item)

        menu2 = self.prepare_context_menu()  # pylint: disable=assignment-from-none
        i = 0
        for item in menu2:
            menu2.remove(item)
            menu.prepend(item)
            menu.reorder_child(item, i)
            i += 1
        menu.show_all()

    def shutdown(self):
        super(ChatControlBase, self).shutdown()
        # PluginSystem: removing GUI extension points connected with ChatControlBase
        # instance object
        app.plugin_manager.remove_gui_extension_point('chat_control_base', self)
        app.plugin_manager.remove_gui_extension_point(
            'chat_control_base_draw_banner', self)
        app.plugin_manager.remove_gui_extension_point(
            'chat_control_base_update_toolbar', self)
        app.ged.remove_event_handler('our-show', ged.GUI1,
            self._nec_our_status)
        app.ged.remove_event_handler('sec-catalog-received', ged.GUI1,
            self._sec_labels_received)
        app.ged.remove_event_handler('style-changed', ged.GUI1,
            self._style_changed)


    def on_msg_textview_populate_popup(self, textview, menu):
        """
        Override the default context menu and we prepend an option to switch
        languages
        """
        item = Gtk.MenuItem.new_with_mnemonic(_('_Undo'))
        menu.prepend(item)
        id_ = item.connect('activate', self.msg_textview.undo)
        self.handlers[id_] = item

        item = Gtk.SeparatorMenuItem.new()
        menu.prepend(item)

        item = Gtk.MenuItem.new_with_mnemonic(_('_Clear'))
        menu.prepend(item)
        id_ = item.connect('activate', self.msg_textview.clear)
        self.handlers[id_] = item

        paste_item = Gtk.MenuItem.new_with_label(_('Paste as quote'))
        id_ = paste_item.connect('activate', self.paste_clipboard_as_quote)
        self.handlers[id_] = paste_item
        menu.append(paste_item)

        menu.show_all()

    def insert_as_quote(self, text: str) -> None:
        self.msg_textview.remove_placeholder()
        text = '> ' + text.replace('\n', '\n> ') + '\n'
        message_buffer = self.msg_textview.get_buffer()
        message_buffer.insert_at_cursor(text)

    def paste_clipboard_as_quote(self, _item: Gtk.MenuItem) -> None:
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        text = clipboard.wait_for_text()
        self.insert_as_quote(text)

    def on_quote(self, widget, text):
        self.insert_as_quote(text)

    # moved from ChatControl
    def _on_banner_eventbox_button_press_event(self, widget, event):
        """
        If right-clicked, show popup
        """
        if event.button == 3:  # right click
            self.parent_win.popup_menu(event)

    def _conv_textview_key_press_event(self, _widget, event):
        if (event.get_state() & get_primary_accel_mod() and
                event.hardware_keycode in self.keycodes_c):
            return Gdk.EVENT_PROPAGATE

        if (event.get_state() & Gdk.ModifierType.SHIFT_MASK and
                event.keyval in (Gdk.KEY_Page_Down, Gdk.KEY_Page_Up)):
            self._on_scroll(None, event.keyval)
            return Gdk.EVENT_PROPAGATE

        self.parent_win.notebook.event(event)
        return Gdk.EVENT_STOP

    def _on_message_textview_paste_event(self, texview):
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        image = clipboard.wait_for_image()
        if image is not None:
            if not app.config.get('confirm_paste_image'):
                self._paste_event_confirmed(image)
                return
            NewConfirmationDialog(
                _('Warning'),
                _('You are trying to paste an image'),
                _('Are you sure you want to paste your '
                  'clipboard image in the chat?'),
                [DialogButton.make('Cancel'),
                 DialogButton.make('OK',
                                   callback=lambda: self._paste_event_confirmed(image))],
                ).show()

    def _paste_event_confirmed(self, image):
        tmp_dir = TemporaryDirectory()
        dir_ = tmp_dir.name

        # get file transfer preference
        ft_pref = app.config.get_per('accounts', self.account,
                                     'filetransfer_preference')
        path = os.path.join(dir_, '0.png')
        image.savev(path, 'png', [], [])
        con = app.connections[self.account]
        win = self.parent_win.window
        httpupload = win.lookup_action(
            'send-file-httpupload-%s' % self.control_id)
        jingle = win.lookup_action('send-file-jingle-%s' % self.control_id)

        if self.type_id == message_control.TYPE_GC:
            # groupchat only supports httpupload on drag and drop
            if httpupload.get_enabled():
                # use httpupload
                con.get_module('HTTPUpload').check_file_before_transfer(
                    path, self.encryption, self.contact,
                    self.session, groupchat=True)
        else:
            if httpupload.get_enabled() and jingle.get_enabled():
                if ft_pref == 'httpupload':
                    con.get_module('HTTPUpload').check_file_before_transfer(
                        path, self.encryption, self.contact, self.session)
                else:
                    ft = app.interface.instances['file_transfers']
                    ft.send_file(self.account, self.contact, path)
            elif httpupload.get_enabled():
                con.get_module('HTTPUpload').check_file_before_transfer(
                    path, self.encryption, self.contact, self.session)
            elif jingle.get_enabled():
                ft = app.interface.instances['file_transfers']
                ft.send_file(self.account, self.contact, path)

        tmp_dir.cleanup()

    def _on_message_textview_key_press_event(self, widget, event):
        if event.keyval == Gdk.KEY_space:
            self.space_pressed = True

        elif (self.space_pressed or self.msg_textview.undo_pressed) and \
        event.keyval not in (Gdk.KEY_Control_L, Gdk.KEY_Control_R) and \
        not (event.keyval == Gdk.KEY_z and event.get_state() & Gdk.ModifierType.CONTROL_MASK):
            # If the space key has been pressed and now it hasn't,
            # we save the buffer into the undo list. But be careful we're not
            # pressing Control again (as in ctrl+z)
            _buffer = widget.get_buffer()
            start_iter, end_iter = _buffer.get_bounds()
            self.msg_textview.save_undo(_buffer.get_text(start_iter, end_iter, True))
            self.space_pressed = False

        # Ctrl [+ Shift] + Tab are not forwarded to notebook. We handle it here
        if self.widget_name == 'groupchat_control':
            if event.keyval not in (Gdk.KEY_ISO_Left_Tab, Gdk.KEY_Tab):
                self.last_key_tabs = False
        if event.get_state() & Gdk.ModifierType.SHIFT_MASK:
            # CTRL + SHIFT + TAB
            if event.get_state() & Gdk.ModifierType.CONTROL_MASK and \
                            event.keyval == Gdk.KEY_ISO_Left_Tab:
                self.parent_win.move_to_next_unread_tab(False)
                return True
            # SHIFT + PAGE_[UP|DOWN]: send to conv_textview
            if event.keyval == Gdk.KEY_Page_Down or \
                            event.keyval == Gdk.KEY_Page_Up:
                self.conv_textview.tv.event(event)
                return True
        if event.get_state() & Gdk.ModifierType.CONTROL_MASK:
            if event.keyval == Gdk.KEY_Tab:  # CTRL + TAB
                self.parent_win.move_to_next_unread_tab(True)
                return True

        message_buffer = self.msg_textview.get_buffer()
        event_state = event.get_state()
        if event.keyval == Gdk.KEY_Tab:
            start, end = message_buffer.get_bounds()
            position = message_buffer.get_insert()
            end = message_buffer.get_iter_at_mark(position)
            text = message_buffer.get_text(start, end, False)
            splitted = text.split()
            if (text.startswith(self.COMMAND_PREFIX) and not
            text.startswith(self.COMMAND_PREFIX * 2) and len(splitted) == 1):
                text = splitted[0]
                bare = text.lstrip(self.COMMAND_PREFIX)
                if len(text) == 1:
                    self.command_hits = []
                    for command in self.list_commands():
                        for name in command.names:
                            self.command_hits.append(name)
                else:
                    if (self.last_key_tabs and self.command_hits and
                            self.command_hits[0].startswith(bare)):
                        self.command_hits.append(self.command_hits.pop(0))
                    else:
                        self.command_hits = []
                        for command in self.list_commands():
                            for name in command.names:
                                if name.startswith(bare):
                                    self.command_hits.append(name)

                if self.command_hits:
                    message_buffer.delete(start, end)
                    message_buffer.insert_at_cursor(self.COMMAND_PREFIX + \
                        self.command_hits[0] + ' ')
                    self.last_key_tabs = True
                return True
            if self.widget_name != 'groupchat_control':
                self.last_key_tabs = False
        if event.keyval == Gdk.KEY_Up:
            if event_state & Gdk.ModifierType.CONTROL_MASK:
                if event_state & Gdk.ModifierType.SHIFT_MASK: # Ctrl+Shift+UP
                    self.scroll_messages('up', message_buffer, 'received')
                else:  # Ctrl+UP
                    self.scroll_messages('up', message_buffer, 'sent')
                return True
        elif event.keyval == Gdk.KEY_Down:
            if event_state & Gdk.ModifierType.CONTROL_MASK:
                if event_state & Gdk.ModifierType.SHIFT_MASK: # Ctrl+Shift+Down
                    self.scroll_messages('down', message_buffer, 'received')
                else:  # Ctrl+Down
                    self.scroll_messages('down', message_buffer, 'sent')
                return True
        elif event.keyval == Gdk.KEY_Return or \
        event.keyval == Gdk.KEY_KP_Enter:  # ENTER
            message_textview = widget
            message_buffer = message_textview.get_buffer()
            message_textview.replace_emojis()
            start_iter, end_iter = message_buffer.get_bounds()
            message = message_buffer.get_text(start_iter, end_iter, False)
            xhtml = self.msg_textview.get_xhtml()

            if event_state & Gdk.ModifierType.SHIFT_MASK:
                send_message = False
            else:
                is_ctrl_enter = bool(event_state & Gdk.ModifierType.CONTROL_MASK)
                send_message = is_ctrl_enter == app.config.get('send_on_ctrl_enter')

            if send_message and not app.account_is_connected(self.account):
                # we are not connected
                app.interface.raise_dialog('not-connected-while-sending')
            elif send_message:
                self.send_message(message, xhtml=xhtml)
            else:
                message_buffer.insert_at_cursor('\n')
                mark = message_buffer.get_insert()
                iter_ = message_buffer.get_iter_at_mark(mark)
                if message_buffer.get_end_iter().equal(iter_):
                    GLib.idle_add(util.scroll_to_end, self.msg_scrolledwindow)

            return True
        elif event.keyval == Gdk.KEY_z: # CTRL+z
            if event_state & Gdk.ModifierType.CONTROL_MASK:
                self.msg_textview.undo()
                return True

        return False

    def _on_drag_data_received(self, widget, context, x, y, selection,
                    target_type, timestamp):
        """
        Derived types SHOULD implement this
        """

    def _on_drag_leave(self, *args):
        # FIXME: DND on non editable TextView, find a better way
        self.drag_entered = False
        self.conv_textview.tv.set_editable(False)

    def _on_drag_motion(self, *args):
        # FIXME: DND on non editable TextView, find a better way
        if not self.drag_entered:
            # We drag new data over the TextView, make it editable to catch dnd
            self.drag_entered_conv = True
            self.conv_textview.tv.set_editable(True)

    def drag_data_file_transfer(self, contact, selection, widget):
        # get file transfer preference
        ft_pref = app.config.get_per('accounts', self.account,
                                     'filetransfer_preference')
        win = self.parent_win.window
        con = app.connections[self.account]
        httpupload = win.lookup_action(
            'send-file-httpupload-%s' % self.control_id)
        jingle = win.lookup_action('send-file-jingle-%s' % self.control_id)

        # we may have more than one file dropped
        uri_splitted = selection.get_uris()
        for uri in uri_splitted:
            path = helpers.get_file_path_from_dnd_dropped_uri(uri)
            if not os.path.isfile(path):  # is it a file?
                continue
            if self.type_id == message_control.TYPE_GC:
                # groupchat only supports httpupload on drag and drop
                if httpupload.get_enabled():
                    # use httpupload
                    con.get_module('HTTPUpload').check_file_before_transfer(
                        path, self.encryption, contact,
                        self.session, groupchat=True)
            else:
                if httpupload.get_enabled() and jingle.get_enabled():
                    if ft_pref == 'httpupload':
                        con.get_module('HTTPUpload').check_file_before_transfer(
                            path, self.encryption, contact, self.session)
                    else:
                        ft = app.interface.instances['file_transfers']
                        ft.send_file(self.account, contact, path)
                elif httpupload.get_enabled():
                    con.get_module('HTTPUpload').check_file_before_transfer(
                        path, self.encryption, contact, self.session)
                elif jingle.get_enabled():
                    ft = app.interface.instances['file_transfers']
                    ft.send_file(self.account, contact, path)

    def get_seclabel(self):
        idx = self.seclabel_combo.get_active()
        if idx == -1:
            return

        con = app.connections[self.account]
        jid = self.contact.jid
        if self.TYPE_ID == 'pm':
            jid = self.gc_contact.room_jid
        catalog = con.get_module('SecLabels').get_catalog(jid)
        labels, label_list, _ = catalog
        lname = label_list[idx]
        label = labels[lname]
        return label

    def send_message(self, message, type_='chat',
    resource=None, xhtml=None, process_commands=True, attention=False):
        """
        Send the given message to the active tab. Doesn't return None if error
        """
        if not message or message == '\n':
            return None

        if process_commands and self.process_as_command(message):
            return

        label = self.get_seclabel()

        if self.correcting and self.last_sent_msg:
            correct_id = self.last_sent_msg
        else:
            correct_id = None

        con = app.connections[self.account]
        chatstate = con.get_module('Chatstate').get_active_chatstate(
            self.contact)

        app.nec.push_outgoing_event(MessageOutgoingEvent(None,
            account=self.account, jid=self.contact.jid, message=message,
            type_=type_, chatstate=chatstate,
            resource=resource, user_nick=self.user_nick, xhtml=xhtml,
            label=label, control=self, attention=attention, correct_id=correct_id,
            automatic_message=False, encryption=self.encryption))

        # Record the history of sent messages
        self.save_message(message, 'sent')

        # Be sure to send user nickname only once according to JEP-0172
        self.user_nick = None

        # Clear msg input
        message_buffer = self.msg_textview.get_buffer()
        message_buffer.set_text('') # clear message buffer (and tv of course)

    def _on_window_motion_notify(self, *args):
        """
        It gets called no matter if it is the active window or not
        """
        if not self.parent_win:
            # when a groupchat is minimized there is no parent window
            return
        if self.parent_win.get_active_jid() == self.contact.jid:
            # if window is the active one, set last interaction
            con = app.connections[self.account]
            con.get_module('Chatstate').set_mouse_activity(
                self.contact, self.msg_textview.has_text())

    def _on_message_tv_buffer_changed(self, textview, textbuffer):
        if textbuffer.get_char_count() and self.encryption:
            app.plugin_manager.extension_point(
                'typing' + self.encryption, self)

        con = app.connections[self.account]
        con.get_module('Chatstate').set_keyboard_activity(self.contact)
        if not textview.has_text():
            con.get_module('Chatstate').set_chatstate_delayed(self.contact,
                                                              Chatstate.ACTIVE)
            return
        con.get_module('Chatstate').set_chatstate(self.contact,
                                                  Chatstate.COMPOSING)

    def save_message(self, message, msg_type):
        # save the message, so user can scroll though the list with key up/down
        if msg_type == 'sent':
            history = self.sent_history
            pos = self.sent_history_pos
        else:
            history = self.received_history
            pos = self.received_history_pos
        size = len(history)
        scroll = pos != size
        # we don't want size of the buffer to grow indefinitely
        max_size = app.config.get('key_up_lines')
        for _i in range(size - max_size + 1):
            if pos == 0:
                break
            history.pop(0)
            pos -= 1
        history.append(message)
        if not scroll or msg_type == 'sent':
            pos = len(history)
        if msg_type == 'sent':
            self.sent_history_pos = pos
            self.orig_msg = None
        else:
            self.received_history_pos = pos

    def add_info_message(self, text):
        self.conv_textview.print_conversation_line(
            text, 'info', '', None, graphics=False)

    def add_status_message(self, text):
        self.conv_textview.print_conversation_line(
            text, 'status', '', None)

    def add_message(self, text, kind, name, tim,
                    other_tags_for_name=None, other_tags_for_time=None,
                    other_tags_for_text=None, restored=False, subject=None,
                    old_kind=None, xhtml=None,
                    displaymarking=None, msg_log_id=None,
                    message_id=None, correct_id=None, additional_data=None,
                    encrypted=None):
        """
        Print 'chat' type messages
        correct_id = (message_id, correct_id)
        """
        jid = self.contact.jid
        full_jid = self.get_full_jid()
        textview = self.conv_textview
        end = False
        if self.conv_textview.autoscroll or kind == 'outgoing':
            end = True

        if other_tags_for_name is None:
            other_tags_for_name = []
        if other_tags_for_time is None:
            other_tags_for_time = []
        if other_tags_for_text is None:
            other_tags_for_text = []
        if additional_data is None:
            additional_data = AdditionalDataDict()

        textview.print_conversation_line(text, kind, name, tim,
            other_tags_for_name, other_tags_for_time, other_tags_for_text,
            subject, old_kind, xhtml,
            displaymarking=displaymarking, message_id=message_id,
            correct_id=correct_id, additional_data=additional_data,
            encrypted=encrypted)

        if restored:
            return

        if kind == 'incoming':
            if not self.type_id == message_control.TYPE_GC or \
            app.config.notify_for_muc(jid) or \
            'marked' in other_tags_for_text:
                # it's a normal message, or a muc message with want to be
                # notified about if quitting just after
                # other_tags_for_text == ['marked'] --> highlighted gc message
                app.last_message_time[self.account][full_jid] = time.time()

        if kind in ('incoming', 'incoming_queue'):
            # Record the history of received messages
            self.save_message(text, 'received')

        if kind in ('incoming', 'incoming_queue', 'error'):
            gc_message = False
            if self.type_id == message_control.TYPE_GC:
                gc_message = True

            if ((self.parent_win and (not self.parent_win.get_active_control() or \
            self != self.parent_win.get_active_control() or \
            not self.parent_win.is_active() or not end)) or \
            (gc_message and \
            jid in app.interface.minimized_controls[self.account])) and \
            kind in ('incoming', 'incoming_queue', 'error'):
                # we want to have save this message in events list
                # other_tags_for_text == ['marked'] --> highlighted gc message
                if gc_message:
                    if 'marked' in other_tags_for_text:
                        event_type = events.PrintedMarkedGcMsgEvent
                    else:
                        event_type = events.PrintedGcMsgEvent
                    event = 'gc_message_received'
                else:
                    if self.type_id == message_control.TYPE_CHAT:
                        event_type = events.PrintedChatEvent
                    else:
                        event_type = events.PrintedPmEvent
                    event = 'message_received'
                show_in_roster = get_show_in_roster(event, self.session)
                show_in_systray = get_show_in_systray(
                    event_type.type_, self.contact.jid)

                event = event_type(text, subject, self, msg_log_id,
                    show_in_roster=show_in_roster,
                    show_in_systray=show_in_systray)
                app.events.add_event(self.account, full_jid, event)
                # We need to redraw contact if we show in roster
                if show_in_roster:
                    app.interface.roster.draw_contact(self.contact.jid,
                        self.account)

        if not self.parent_win:
            return

        if (not self.parent_win.get_active_control() or \
        self != self.parent_win.get_active_control() or \
        not self.parent_win.is_active() or not end) and \
        kind in ('incoming', 'incoming_queue', 'error'):
            self.parent_win.redraw_tab(self)
            if not self.parent_win.is_active():
                self.parent_win.show_title(True, self) # Enabled Urgent hint
            else:
                self.parent_win.show_title(False, self) # Disabled Urgent hint

    def toggle_emoticons(self):
        """
        Hide show emoticons_button
        """
        if app.config.get('emoticons_theme'):
            self.emoticons_button.set_no_show_all(False)
            self.emoticons_button.show()
        else:
            self.emoticons_button.set_no_show_all(True)
            self.emoticons_button.hide()

    def set_emoticon_popover(self):
        if not app.config.get('emoticons_theme'):
            return

        if not self.parent_win:
            return

        emoji_chooser.text_widget = self.msg_textview
        emoticons_button = self.xml.get_object('emoticons_button')
        emoticons_button.set_popover(emoji_chooser)

    def on_color_menuitem_activate(self, widget):
        color_dialog = Gtk.ColorChooserDialog(None, self.parent_win.window)
        color_dialog.set_use_alpha(False)
        color_dialog.connect('response', self.msg_textview.color_set)
        color_dialog.show_all()

    def on_font_menuitem_activate(self, widget):
        font_dialog = Gtk.FontChooserDialog(None, self.parent_win.window)
        start, finish = self.msg_textview.get_active_iters()
        font_dialog.connect('response', self.msg_textview.font_set, start, finish)
        font_dialog.show_all()

    def on_formatting_menuitem_activate(self, widget):
        tag = widget.get_name()
        self.msg_textview.set_tag(tag)

    def on_clear_formatting_menuitem_activate(self, widget):
        self.msg_textview.clear_tags()

    def _style_changed(self, *args):
        self.update_tags()

    def update_tags(self):
        self.conv_textview.update_tags()

    def clear(self, tv):
        buffer_ = tv.get_buffer()
        start, end = buffer_.get_bounds()
        buffer_.delete(start, end)

    def _on_send_file(self, action, param):
        # get file transfer preference
        ft_pref = app.config.get_per('accounts', self.account,
                                     'filetransfer_preference')

        win = self.parent_win.window
        httpupload = win.lookup_action(
            'send-file-httpupload-%s' % self.control_id)
        jingle = win.lookup_action('send-file-jingle-%s' % self.control_id)

        if httpupload.get_enabled() and jingle.get_enabled():
            if ft_pref == 'httpupload':
                httpupload.activate()
            else:
                jingle.activate()
        elif httpupload.get_enabled():
            httpupload.activate()
        elif jingle.get_enabled():
            jingle.activate()

    def _on_send_httpupload(self, action, param):
        app.interface.send_httpupload(self)

    def _on_send_jingle(self, action, param):
        self._on_send_file_jingle()

    def _on_send_file_jingle(self, gc_contact=None):
        """
        gc_contact can be set when we are in a groupchat control
        """
        def _on_ok(c):
            app.interface.instances['file_transfers'].show_file_send_request(
                self.account, c)
        if self.type_id == message_control.TYPE_PM:
            gc_contact = self.gc_contact

        if not gc_contact:
            _on_ok(self.contact)
            return

        # gc or pm
        gc_control = app.interface.msg_win_mgr.get_gc_control(
            gc_contact.room_jid, self.account)
        self_contact = app.contacts.get_gc_contact(self.account,
                                                   gc_control.room_jid,
                                                   gc_control.nick)
        if (gc_control.is_anonymous and
                gc_contact.affiliation.value not in ['admin', 'owner'] and
                self_contact.affiliation.value in ['admin', 'owner']):
            contact = app.contacts.get_contact(self.account, gc_contact.jid)
            if not contact or contact.sub not in ('both', 'to'):

                NewConfirmationDialog(
                    _('Privacy'),
                    _('Warning'),
                    _('If you send a file to <b>%s</b>, '
                      'your real JID will be revealed.' % gc_contact.name),
                    [DialogButton.make('Cancel'),
                     DialogButton.make(
                         'OK',
                         text=_('Continue'),
                         callback=lambda: _on_ok(gc_contact))]).show()
                return
        _on_ok(gc_contact)

    def on_notify_menuitem_toggled(self, widget):
        app.config.set_per('rooms', self.contact.jid, 'notify_on_all_messages',
            widget.get_active())

    def set_control_active(self, state):
        con = app.connections[self.account]
        if state:
            self.set_emoticon_popover()
            jid = self.contact.jid
            if self.conv_textview.autoscroll:
                # we are at the end
                type_ = ['printed_' + self.type_id]
                if self.type_id == message_control.TYPE_GC:
                    type_ = ['printed_gc_msg', 'printed_marked_gc_msg']
                if not app.events.remove_events(self.account, self.get_full_jid(),
                types=type_):
                    # There were events to remove
                    self.redraw_after_event_removed(jid)
            # send chatstate inactive to the one we're leaving
            # and active to the one we visit
            if self.msg_textview.has_text():
                con.get_module('Chatstate').set_chatstate(self.contact,
                                                          Chatstate.PAUSED)
            else:
                con.get_module('Chatstate').set_chatstate(self.contact,
                                                          Chatstate.ACTIVE)
        else:
            con.get_module('Chatstate').set_chatstate(self.contact,
                                                      Chatstate.INACTIVE)

    def scroll_to_end(self, force=False):
        self.conv_textview.scroll_to_end(force)

    def _on_edge_reached(self, scrolledwindow, pos):
        if pos != Gtk.PositionType.BOTTOM:
            return
        # Remove all events and set autoscroll True
        app.log('autoscroll').info('Autoscroll enabled')
        self.conv_textview.autoscroll = True
        if self.resource:
            jid = self.contact.get_full_jid()
        else:
            jid = self.contact.jid
        types_list = []
        type_ = self.type_id
        if type_ == message_control.TYPE_GC:
            type_ = 'gc_msg'
            types_list = ['printed_' + type_, type_, 'printed_marked_gc_msg']
        else: # Not a GC
            types_list = ['printed_' + type_, type_]

        if not app.events.get_events(self.account, jid, types_list):
            return
        if not self.parent_win:
            return
        if self.parent_win.get_active_control() == self and \
        self.parent_win.window.is_active():
            # we are at the end
            if not app.events.remove_events(
                    self.account, jid, types=types_list):
                # There were events to remove
                self.redraw_after_event_removed(jid)

    def _on_scrollbar_button_release(self, scrollbar, event):
        if event.get_button()[1] != 1:
            # We want only to catch the left mouse button
            return
        if not at_the_end(scrollbar.get_parent()):
            app.log('autoscroll').info('Autoscroll disabled')
            self.conv_textview.autoscroll = False

    def has_focus(self):
        if self.parent_win:
            if self.parent_win.window.get_property('has-toplevel-focus'):
                if self == self.parent_win.get_active_control():
                    return True
        return False

    def _on_scroll(self, widget, event):
        if not self.conv_textview.autoscroll:
            # autoscroll is already disabled
            return

        if widget is None:
            # call from _conv_textview_key_press_event()
            # SHIFT + Gdk.KEY_Page_Up
            if event != Gdk.KEY_Page_Up:
                return
        else:
            # On scrolling UP disable autoscroll
            # get_scroll_direction() sets has_direction only TRUE
            # if smooth scrolling is deactivated. If we have smooth
            # smooth scrolling we have to use get_scroll_deltas()
            has_direction, direction = event.get_scroll_direction()
            if not has_direction:
                direction = None
                smooth, delta_x, delta_y = event.get_scroll_deltas()
                if smooth:
                    if delta_y < 0:
                        direction = Gdk.ScrollDirection.UP
                    elif delta_y > 0:
                        direction = Gdk.ScrollDirection.DOWN
                    elif delta_x < 0:
                        direction = Gdk.ScrollDirection.LEFT
                    elif delta_x > 0:
                        direction = Gdk.ScrollDirection.RIGHT
                else:
                    app.log('autoscroll').warning(
                        'Scroll directions cant be determined')

            if direction != Gdk.ScrollDirection.UP:
                return
        # Check if we have a Scrollbar
        adjustment = self.conv_scrolledwindow.get_vadjustment()
        if adjustment.get_upper() != adjustment.get_page_size():
            app.log('autoscroll').info('Autoscroll disabled')
            self.conv_textview.autoscroll = False

    def on_conversation_vadjustment_changed(self, adjustment):
        self.scroll_to_end()

    def redraw_after_event_removed(self, jid):
        """
        We just removed a 'printed_*' event, redraw contact in roster or
        gc_roster and titles in roster and msg_win
        """
        self.parent_win.redraw_tab(self)
        self.parent_win.show_title()
        # TODO : get the contact and check get_show_in_roster()
        if self.type_id == message_control.TYPE_PM:
            room_jid, nick = app.get_room_and_nick_from_fjid(jid)
            groupchat_control = app.interface.msg_win_mgr.get_gc_control(
                    room_jid, self.account)
            if room_jid in app.interface.minimized_controls[self.account]:
                groupchat_control = \
                        app.interface.minimized_controls[self.account][room_jid]
            contact = app.contacts.get_contact_with_highest_priority(
                self.account, room_jid)
            if contact:
                app.interface.roster.draw_contact(room_jid, self.account)
            if groupchat_control:
                groupchat_control.draw_contact(nick)
                if groupchat_control.parent_win:
                    groupchat_control.parent_win.redraw_tab(groupchat_control)
        else:
            app.interface.roster.draw_contact(jid, self.account)
            app.interface.roster.show_title()

    def scroll_messages(self, direction, msg_buf, msg_type):
        if msg_type == 'sent':
            history = self.sent_history
            pos = self.sent_history_pos
            self.received_history_pos = len(self.received_history)
        else:
            history = self.received_history
            pos = self.received_history_pos
            self.sent_history_pos = len(self.sent_history)
        size = len(history)
        if self.orig_msg is None:
            # user was typing something and then went into history, so save
            # whatever is already typed
            start_iter = msg_buf.get_start_iter()
            end_iter = msg_buf.get_end_iter()
            self.orig_msg = msg_buf.get_text(start_iter, end_iter, False)
        if pos == size and size > 0 and direction == 'up' and \
        msg_type == 'sent' and not self.correcting and (not \
        history[pos - 1].startswith('/') or history[pos - 1].startswith('/me')):
            self.correcting = True
            gtkgui_helpers.add_css_class(
                self.msg_textview, 'gajim-msg-correcting')
            message = history[pos - 1]
            msg_buf.set_text(message)
            return
        if self.correcting:
            # We were previously correcting
            gtkgui_helpers.remove_css_class(
                self.msg_textview, 'gajim-msg-correcting')
        self.correcting = False
        pos += -1 if direction == 'up' else +1
        if pos == -1:
            return
        if pos >= size:
            pos = size
            message = self.orig_msg
            self.orig_msg = None
        else:
            message = history[pos]
        if msg_type == 'sent':
            self.sent_history_pos = pos
        else:
            self.received_history_pos = pos
            if self.orig_msg is not None:
                message = '> %s\n' % message.replace('\n', '\n> ')
        msg_buf.set_text(message)

    def widget_set_visible(self, widget, state):
        """
        Show or hide a widget
        """
        # make the last message visible, when changing to "full view"
        if not state:
            self.scroll_to_end()

        widget.set_no_show_all(state)
        if state:
            widget.hide()
        else:
            widget.show_all()

    def got_connected(self):
        self.msg_textview.set_sensitive(True)
        self.msg_textview.set_editable(True)
        self.update_toolbar()

    def got_disconnected(self):
        self.msg_textview.set_sensitive(False)
        self.msg_textview.set_editable(False)
        self.conv_textview.tv.grab_focus()

        self.no_autonegotiation = False
        self.update_toolbar()


class ScrolledWindow(Gtk.ScrolledWindow):
    def __init__(self, *args, **kwargs):
        Gtk.ScrolledWindow.__init__(self, *args, **kwargs)

        self.set_overlay_scrolling(False)
        self.set_max_content_height(100)
        self.set_propagate_natural_height(True)
        self.get_style_context().add_class('scrolled-no-border')
        self.get_style_context().add_class('no-scroll-indicator')
        self.get_style_context().add_class('scrollbar-style')
        self.set_shadow_type(Gtk.ShadowType.IN)

    def do_get_preferred_height(self):
        min_height, natural_height = Gtk.ScrolledWindow.do_get_preferred_height(self)
        # Gtk Bug: If policy is set to Automatic, the ScrolledWindow
        # has a min size of around 46-82 depending on the System. Because
        # we want it smaller, we set policy NEVER if the height is < 90
        # so the ScrolledWindow will shrink to around 26 (1 line height).
        # Once it gets over 90 its no problem to restore the policy.
        if natural_height < 90:
            GLib.idle_add(self.set_policy,
                          Gtk.PolicyType.AUTOMATIC,
                          Gtk.PolicyType.NEVER)
        else:
            GLib.idle_add(self.set_policy,
                          Gtk.PolicyType.AUTOMATIC,
                          Gtk.PolicyType.AUTOMATIC)

        return min_height, natural_height
