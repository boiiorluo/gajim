##	systray.py
##
## Gajim Team:
##	- Yann Le Boulanger <asterix@lagaule.org>
##	- Vincent Hanquez <tab@snarc.org>
##	- Nikos Kouremenos <kourem@gmail.com>
##	- Dimitur Kirov <dkirov@gmail.com>
##
##	Copyright (C) 2003-2005 Gajim Team
##
## This program is free software; you can redistribute it and/or modify
## it under the terms of the GNU General Public License as published
## by the Free Software Foundation; version 2 only.
##
## This program is distributed in the hope that it will be useful,
## but WITHOUT ANY WARRANTY; without even the implied warranty of
## MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
## GNU General Public License for more details.
##

import gtk
import gtk.glade
import gobject
import dialogs
import os

import tooltips

from common import gajim
from common import helpers
from common import i18n

try:
	import egg.trayicon as trayicon	# gnomepythonextras trayicon
except:
	try:
		import trayicon # our trayicon
	except:
		gajim.log.debug('No trayicon module available')
		pass

_ = i18n._
APP = i18n.APP
gtk.glade.bindtextdomain(APP, i18n.DIR)
gtk.glade.textdomain(APP)

GTKGUI_GLADE = 'gtkgui.glade'

class Systray:
	'''Class for icon in the notification area
	This class is both base class (for systraywin32.py) and normal class
	for trayicon in GNU/Linux'''
	
	def __init__(self, plugin):
		self.plugin = plugin
		self.jids = []
		self.new_message_handler_id = None
		self.t = None
		self.img_tray = gtk.Image()
		self.status = 'offline'
		self.xml = gtk.glade.XML(GTKGUI_GLADE, 'systray_context_menu', APP)
		self.systray_context_menu = self.xml.get_widget('systray_context_menu')
		self.xml.signal_autoconnect(self)

	def set_img(self):
		if len(self.jids) > 0:
			state = 'message'
		else:
			state = self.status
		image = self.plugin.roster.jabber_state_images[state]
		if image.get_storage_type() == gtk.IMAGE_ANIMATION:
			self.img_tray.set_from_animation(image.get_animation())
		elif image.get_storage_type() == gtk.IMAGE_PIXBUF:
			self.img_tray.set_from_pixbuf(image.get_pixbuf())

	def add_jid(self, jid, account):
		l = [account, jid]
		if not l in self.jids:
			self.jids.append(l)
			self.set_img()
		# we append to the number of unread messages
		nb = self.plugin.roster.nb_unread
		for acct in gajim.connections:
			# in chat / groupchat windows
			for kind in ['chats', 'gc']:
				jids = self.plugin.windows[acct][kind]
				for jid in jids:
					if jid != 'tabbed':
						nb += jids[jid].nb_unread[jid]

	def remove_jid(self, jid, account):
		l = [account, jid]
		if l in self.jids:
			self.jids.remove(l)
			self.set_img()
		# we remove from the number of unread messages
		nb = self.plugin.roster.nb_unread
		for acct in gajim.connections:
			# in chat / groupchat windows
			for kind in ['chats', 'gc']:
				for jid in self.plugin.windows[acct][kind]:
					if jid != 'tabbed':
						nb += self.plugin.windows[acct][kind][jid].nb_unread[jid]
	
	def change_status(self, global_status):
		''' set tray image to 'global_status' '''
		# change image and status, only if it is different 
		if global_status is not None and self.status != global_status:
			self.status = global_status
		self.set_img()
	
	def start_chat(self, widget, account, jid):
		if self.plugin.windows[account]['chats'].has_key(jid):
			self.plugin.windows[account]['chats'][jid].window.present()
			self.plugin.windows[account]['chats'][jid].set_active_tab(jid)
		elif gajim.contacts[account].has_key(jid):
			self.plugin.roster.new_chat(
				gajim.contacts[account][jid][0], account)
			self.plugin.windows[account]['chats'][jid].set_active_tab(jid)
	
	def on_new_message_menuitem_activate(self, widget, account):
		"""When new message menuitem is activated:
		call the NewMessageDialog class"""
		dialogs.NewMessageDialog(self.plugin, account)

	def make_menu(self, event = None):
		'''create chat with and new message (sub) menus/menuitems
		event is None when we're in Windows
		'''
		
		chat_with_menuitem = self.xml.get_widget('chat_with_menuitem')
		new_message_menuitem = self.xml.get_widget('new_message_menuitem')
		status_menuitem = self.xml.get_widget('status_menu')
		
		if self.new_message_handler_id:
			new_message_menuitem.handler_disconnect(
				self.new_message_handler_id)
			self.new_message_handler_id = None

		sub_menu = gtk.Menu()
		status_menuitem.set_submenu(sub_menu)

		# We need our own set of status icons, let's make 'em!
		iconset = gajim.config.get('iconset')
		if not iconset:
			iconset = 'sun'
		path = os.path.join(gajim.DATA_DIR, 'iconsets/' + iconset + '/16x16/')
		state_images = self.plugin.roster.load_iconset(path)
		
		for show in ['online', 'chat', 'away', 'xa', 'dnd', 'invisible',
				'offline']:

			if show == 'offline': # We add a sep before offline item
				item = gtk.SeparatorMenuItem()
				sub_menu.append(item)

			uf_show = helpers.get_uf_show(show)
			item = gtk.ImageMenuItem(uf_show)
			item.set_image(state_images[show])
			sub_menu.append(item)
			item.connect('activate', self.on_show_menuitem_activate, show)

		iskey = len(gajim.connections) > 0
		chat_with_menuitem.set_sensitive(iskey)
		new_message_menuitem.set_sensitive(iskey)
		
		if len(gajim.connections) >= 2: # 2 or more connections? make submenus
			account_menu_for_chat_with = gtk.Menu()
			chat_with_menuitem.set_submenu(account_menu_for_chat_with)

			account_menu_for_new_message = gtk.Menu()
			new_message_menuitem.set_submenu(account_menu_for_new_message)

			for account in gajim.connections:
				our_jid = gajim.config.get_per('accounts', account, 'name') + '@' +\
					gajim.config.get_per('accounts', account, 'hostname')
				#for chat_with
				item = gtk.MenuItem(_('as ') + our_jid)
				account_menu_for_chat_with.append(item)
				group_menu = self.make_groups_submenus_for_chat_with(account)
				item.set_submenu(group_menu)
				#for new_message
				item = gtk.MenuItem(_('as ') + our_jid)
				item.connect('activate',
					self.on_new_message_menuitem_activate, account)
				account_menu_for_new_message.append(item)
				
		elif len(gajim.connections) == 1: # one account
			# one account, no need to show 'as jid'
			# for chat_with
			account = gajim.connections.keys()[0]
			
			group_menu = self.make_groups_submenus_for_chat_with(account)
			chat_with_menuitem.set_submenu(group_menu)
					
			# for new message
			self.new_message_handler_id = new_message_menuitem.connect(
				'activate', self.on_new_message_menuitem_activate, account)

		if event is not None: # None means windows (we explicitly popup in systraywin32.py)
			self.systray_context_menu.popup(None, None, None, event.button, event.time)
		self.systray_context_menu.show_all()

	def on_preferences_menuitem_activate(self, widget):
		if self.plugin.windows['preferences'].window.get_property('visible'):
			self.plugin.windows['preferences'].window.present()
		else:
			self.plugin.windows['preferences'].window.show_all()

	def on_quit_menuitem_activate(self, widget):	
		self.plugin.roster.on_quit_menuitem_activate(widget)

	def make_groups_submenus_for_chat_with(self, account):
		groups_menu = gtk.Menu()
		
		for group in gajim.groups[account].keys():
			if group == _('Transports'):
				continue
			# at least one 'not offline' or 'without errors' in this group
			at_least_one = False
			item = gtk.MenuItem(group)
			groups_menu.append(item)
			contacts_menu = gtk.Menu()
			item.set_submenu(contacts_menu)
			for users in gajim.contacts[account].values():
				user = users[0]
				if group in user.groups and user.show != 'offline' and \
						user.show != 'error':
					at_least_one = True
					show = helpers.get_uf_show(user.show)
					s = user.name.replace('_', '__') + ' (' + show + ')'
					item = gtk.MenuItem(s)
					item.connect('activate', self.start_chat, account,\
							user.jid)
					contacts_menu.append(item)
			
			if not at_least_one:
				message = _('All contacts in this group are offline or have errors')
				item = gtk.MenuItem(message)
				item.set_sensitive(False)
				contacts_menu.append(item)

		return groups_menu

	def on_left_click(self):
		win = self.plugin.roster.window
		if len(self.jids) == 0:
			if win.get_property('visible'):
				win.hide()
			else:
				win.present()
		else:
			account = self.jids[0][0]
			jid = self.jids[0][1]
			acc = self.plugin.windows[account]
			w = None
			if acc['gc'].has_key(jid):
				w = acc['gc'][jid]
			elif acc['chats'].has_key(jid):
				w = acc['chats'][jid]
			else:
				self.plugin.roster.new_chat(
					gajim.contacts[account][jid][0], account)
				acc['chats'][jid].set_active_tab(jid)
				acc['chats'][jid].window.present()
			if w:
				w.set_active_tab(jid)
				w.window.present()
				tv = w.xmls[jid].get_widget('conversation_textview')
				w.scroll_to_end(tv)
	
	def on_middle_click(self):
		win = self.plugin.roster.window
		if win.is_active():
			win.hide()
		else:
			win.present()

	def on_clicked(self, widget, event):
		self.on_tray_leave_notify_event(widget, None)
		if event.button == 1: # Left click
			self.on_left_click()
		if event.button == 2: # middle click
			self.on_middle_click()
		if event.button == 3: # right click
			self.make_menu(event)
	
	def on_show_menuitem_activate(self, widget, show):
		l = ['online', 'chat', 'away', 'xa', 'dnd', 'invisible', 'offline']
		index = l.index(show)
		self.plugin.roster.status_combobox.set_active(index)
	
	def show_tooltip(self, widget):
		position = widget.window.get_origin()
		if self.tooltip.id == position:
			size = widget.window.get_size()
			self.tooltip.show_tooltip('', 
				(widget.window.get_pointer()[0], size[1]), position)
			
	def on_tray_motion_notify_event(self, widget, event):
		wireq=widget.size_request()
		position = widget.window.get_origin()
		if self.tooltip.timeout > 0:
			if self.tooltip.id != position:
				self.tooltip.hide_tooltip()
		if self.tooltip.timeout == 0 and \
			self.tooltip.id != position:
			self.tooltip.id = position
			self.tooltip.timeout = gobject.timeout_add(500,
				self.show_tooltip, widget)
	
	def on_tray_leave_notify_event(self, widget, event):
		position = widget.window.get_origin()
		if self.tooltip.timeout > 0 and \
			self.tooltip.id == position:
			self.tooltip.hide_tooltip()
		
	def show_icon(self):
		if not self.t:
			self.t = trayicon.TrayIcon('Gajim')
			eb = gtk.EventBox()
			# avoid draw seperate bg color in some gtk themes
			eb.set_visible_window(False)
			eb.set_events(gtk.gdk.POINTER_MOTION_MASK)
			eb.connect('button-press-event', self.on_clicked)
			eb.connect('motion-notify-event', self.on_tray_motion_notify_event)
			eb.connect('leave-notify-event', self.on_tray_leave_notify_event)
			self.tooltip = tooltips.NotificationAreaTooltip(self.plugin)

			self.img_tray = gtk.Image()
			eb.add(self.img_tray)
			self.t.add(eb)
			self.set_img()
		self.t.show_all()
	
	def hide_icon(self):
		if self.t:
			self.t.destroy()
			self.t = None