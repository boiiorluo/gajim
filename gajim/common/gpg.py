# Copyright (C) 2003-2014 Yann Leboulanger <asterix AT lagaule.org>
# Copyright (C) 2005 Alex Mauer <hawke AT hawkesnest.net>
# Copyright (C) 2005-2006 Nikos Kouremenos <kourem AT gmail.com>
# Copyright (C) 2007 Stephan Erb <steve-e AT h3c.de>
# Copyright (C) 2008 Jean-Marie Traissard <jim AT lapin.org>
#                    Jonathan Schleifer <js-gajim AT webkeks.org>
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
import logging
from gajim.common import app

if app.is_installed('GPG'):
    import gnupg
    gnupg.logger = logging.getLogger('gajim.c.gnupg')

    class GnuPG(gnupg.GPG):
        def __init__(self):
            use_agent = app.config.get('use_gpg_agent')
            gnupg.GPG.__init__(self, gpgbinary=app.get_gpg_binary(), use_agent=use_agent)
            encoding = app.config.get('pgp_encoding')
            if encoding:
                self.encoding = encoding
            self.decode_errors = 'replace'
            self.passphrase = None
            self.always_trust = [] # list of keyID to always trust

        def encrypt(self, str_, recipients, always_trust=False):
            trust = always_trust
            if not trust:
                # check if we trust all keys
                trust = True
                for key in recipients:
                    if key not in self.always_trust:
                        trust = False
            if not trust:
                # check that we'll be able to encrypt
                result = super(GnuPG, self).list_keys(keys=recipients)
                for key in result:
                    if key['trust'] not in ('f', 'u'):
                        if key['keyid'][-8:] not in self.always_trust:
                            return '', 'NOT_TRUSTED ' + key['keyid'][-8:]
                        else:
                            trust = True
            result = super(GnuPG, self).encrypt(str_.encode('utf8'), recipients,
                always_trust=trust, passphrase=self.passphrase)

            if result.ok:
                error = ''
            else:
                error = result.status

            return self._stripHeaderFooter(str(result)), error

        def decrypt(self, str_, keyID):
            data = self._addHeaderFooter(str_, 'MESSAGE')
            result = super(GnuPG, self).decrypt(data.encode('utf8'),
                passphrase=self.passphrase)

            return result.data.decode('utf8')

        def sign(self, str_, keyID):
            result = super(GnuPG, self).sign(str_.encode('utf8'), keyid=keyID, detach=True,
                passphrase=self.passphrase)

            if result.fingerprint:
                return self._stripHeaderFooter(str(result))
            if hasattr(result, 'status') and result.status == 'key expired':
                return 'KEYEXPIRED'
            return 'BAD_PASSPHRASE'

        def verify(self, str_, sign):
            if str_ is None:
                return ''
            # Hash algorithm is not transfered in the signed presence stanza so try
            # all algorithms. Text name for hash algorithms from RFC 4880 - section 9.4
            hash_algorithms = ['SHA512', 'SHA384', 'SHA256', 'SHA224', 'SHA1', 'RIPEMD160']
            for algo in hash_algorithms:
                data = os.linesep.join(
                    ['-----BEGIN PGP SIGNED MESSAGE-----',
                     'Hash: ' + algo,
                     '',
                     str_,
                     self._addHeaderFooter(sign, 'SIGNATURE')]
                    )
                result = super(GnuPG, self).verify(data.encode('utf8'))
                if result.valid:
                    return result.key_id

            return ''

        def get_key(self, keyID):
            return super(GnuPG, self).list_keys(keys=[keyID])

        def get_keys(self, secret=False):
            keys = {}
            result = super(GnuPG, self).list_keys(secret=secret)

            for key in result:
                # Take first not empty uid
                keys[key['keyid'][8:]] = [uid for uid in key['uids'] if uid][0]
            return keys

        def get_secret_keys(self):
            return self.get_keys(True)

        def _stripHeaderFooter(self, data):
            """
            Remove header and footer from data
            """
            if not data: return ''
            lines = data.splitlines()
            while lines[0] != '':
                lines.remove(lines[0])
            while lines[0] == '':
                lines.remove(lines[0])
            i = 0
            for line in lines:
                if line:
                    if line[0] == '-': break
                i = i+1
            line = '\n'.join(lines[0:i])
            return line

        def _addHeaderFooter(self, data, type_):
            """
            Add header and footer from data
            """
            out = "-----BEGIN PGP %s-----" % type_ + os.linesep
            out = out + "Version: PGP" + os.linesep
            out = out + os.linesep
            out = out + data + os.linesep
            out = out + "-----END PGP %s-----" % type_ + os.linesep
            return out
