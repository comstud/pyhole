#   Copyright 2010-2011 Josh Kearney
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.

"""Event-based IRC Class"""

import random
import re
import time
import urllib

from pyhole import irclib
from pyhole import plugin


class IRC(irclib.SimpleIRCClient):
    """An IRC connection"""

    def __init__(self, config, network, logger, version):
        irclib.SimpleIRCClient.__init__(self)

        self.log = logger
        self.version = version

        self.admins = config.get("admins", "list")
        self.command_prefix = config.get("command_prefix")
        self.reconnect_delay = config.get("reconnect_delay", "int")
        self.rejoin_delay = config.get("rejoin_delay", "int")
        self.cache = config.get("cache")

        self.server = network.get("server")
        self.password = network.get("password")
        self.port = network.get("port", "int")
        self.ssl = network.get("ssl", "bool")
        self.nick = network.get("nick")
        self.channels = network.get("channels", "list")

        self.addressed = False

        self.load_plugins()

        self.log.info("Connecting to %s:%d as %s" % (self.server, self.port,
                                                     self.nick))
        self.connect(self.server, self.port, self.nick, self.password,
                     ssl=self.ssl)

    def load_plugins(self, reload_plugins=False):
        """Load plugins and their commands respectively"""

        if reload_plugins:
            plugin.reload_plugins(irc=self)
        else:
            plugin.load_plugins("plugins", irc=self)
        self.log.info("Loaded Plugins: %s" % active_plugins())

    def run_hook_command(self, mod_name, f, arg, **kwargs):
        """Make a call to a plugin hook"""
        try:
            f(arg, **kwargs)
            if arg:
                self.log.debug("Calling: %s.%s(\"%s\")" % (mod_name,
                                                           f.__name__,
                                                           arg))
            else:
                self.log.debug("Calling: %s.%s(None)" % (mod_name,
                                                         f.__name__))
        except Exception, e:
            self.log.error(e)

    def run_msg_regexp_hooks(self, message, private):
        """Run regexp hooks"""
        for mod_name, f, msg_regex in plugin.hook_get_msg_regexs():
            m = re.search(msg_regex, message, re.I)
            if m:
                self.run_hook_command(mod_name, f, m, private=private,
                                      full_message=message)

    def run_keyword_hooks(self, message, private):
        """Run keyword hooks"""
        words = message.split(" ")

        for mod_name, f, kw in plugin.hook_get_keywords():
            for word in words:
                m = re.search("%s(.+)" % kw, word, re.I)
                if m:
                    self.run_hook_command(mod_name, f, m.group(1),
                                          private=private,
                                          full_message=message)

    def run_command_hooks(self, message, private):
        """Run command hooks"""
        for mod_name, f, cmd in plugin.hook_get_commands():
            self.addressed = False

            if private:
                m = re.search("^%s$|^%s\s(.*)$" % (cmd, cmd), message, re.I)
                if m:
                    self.run_hook_command(mod_name, f, m.group(1),
                                          private=private,
                                          addressed=self.addressed,
                                          full_message=message)

            if message.startswith(self.command_prefix):
                # Strip off command prefix
                msg_rest = message[len(self.command_prefix):]
            else:
                # Check for command starting with nick being addressed
                msg_start_upper = message[:len(self.nick) + 1].upper()
                if msg_start_upper == self.nick.upper() + ':':
                    # Get rest of string after "nick:" and white spaces
                    msg_rest = re.sub("^\s+", "",
                                      message[len(self.nick) + 1:])
                else:
                    continue

                self.addressed = True

            m = re.search("^%s$|^%s\s(.*)$" % (cmd, cmd), msg_rest, re.I)
            if m:
                self.run_hook_command(mod_name, f, m.group(1), private=private,
                                      addressed=self.addressed,
                                      full_message=message)

    def poll_messages(self, message, private=False):
        """Watch for known commands"""
        self.addressed = False

        self.run_command_hooks(message, private)
        self.run_keyword_hooks(message, private)
        self.run_msg_regexp_hooks(message, private)

    def reply(self, msg):
        """Send a privmsg"""
        if self.addressed:
            nick = self.source.split("!")[0]
            self.connection.privmsg(self.target, "%s: %s" % (nick, msg))
        else:
            self.connection.privmsg(self.target, msg)

    def privmsg(self, target, msg):
        """Send a privmsg"""
        self.connection.privmsg(target, msg)

    def op_user(self, params):
        """Op a user"""
        params = params.split(" ", 1)
        self.connection.mode(params[0], "+o %s" % params[1])

    def deop_user(self, params):
        """De-op a user"""
        params = params.split(" ", 1)
        self.connection.mode(params[0], "-o %s" % params[1])

    def set_nick(self, params):
        """Set IRC nick"""
        self.nick = params
        self.connection.nick(params)

    def join_channel(self, params):
        """Join a channel"""
        channel = params.split(" ", 1)
        self.reply("Joining %s" % channel[0])
        if irclib.is_channel(channel[0]):
            self.channels.append(channel[0])
            if len(channel) > 1:
                self.connection.join(channel[0], channel[1])
            else:
                self.connection.join(channel[0])

    def part_channel(self, params):
        """Part a channel"""
        self.channels.remove(params)
        self.reply("Parting %s" % params)
        self.connection.part(params)

    def fetch_url(self, url, name):
        """Fetch a URL"""
        try:
            return urllib.urlopen(url)
        except IOError:
            self.reply("Unable to fetch %s data" % name)
            return

    def on_nicknameinuse(self, connection, event):
        """Ensure the use of unique IRC nick"""
        random_int = random.randint(1, 100)
        self.log.info("IRC nick '%s' is currently in use" % self.nick)
        self.nick = "%s%d" % (self.nick, random_int)
        self.log.info("Setting IRC nick to '%s'" % self.nick)
        connection.nick("%s" % self.nick)

    def on_welcome(self, connection, event):
        """Join channels upon successful connection"""
        for channel in self.channels:
            c = channel.split(" ", 1)
            if irclib.is_channel(c[0]):
                self.log.info("Joining %s" % c[0])
                if len(c) > 1:
                    connection.join(c[0], c[1])
                else:
                    connection.join(c[0])

    def on_disconnect(self, connection, event):
        """Attempt to reconnect after disconnection"""
        self.log.info("Disconnected from %s:%d" % (self.server, self.port))
        self.log.info("Reconnecting in %d seconds" % self.reconnect_delay)
        time.sleep(self.reconnect_delay)
        self.log.info("Connecting to %s:%d as %s" % (self.server, self.port,
                                                     self.nick))
        self.connect(self.server, self.port, self.nick, self.password,
                     ssl=self.ssl)

    def on_kick(self, connection, event):
        """Automatically rejoin channel if kicked"""
        self.target = event.target()

        if event.arguments()[1] == self.nick:
            self.log.info("Kicked from %s" % self.target)
            self.log.info("Rejoining %s in %d seconds" % (self.target,
                                                          self.rejoin_delay))
            time.sleep(self.rejoin_delay)
            connection.join(self.target)

    def on_invite(self, connection, event):
        """Join a channel upon invitation"""
        self.source = event.source().split("@", 1)[0]
        self.target = irclib.nm_to_n(event.source())

        if self.source in self.admins:
            self.join_channel(event.arguments()[0])

    def on_ctcp(self, connection, event):
        """Respond to CTCP events"""
        self.source = irclib.nm_to_n(event.source())
        ctcp = event.arguments()[0]

        if ctcp == "VERSION":
            self.log.info("Received CTCP VERSION from %s" % self.source)
            connection.ctcp_reply(self.source, "VERSION %s" % self.version)
        elif ctcp == "PING":
            if len(event.arguments()) > 1:
                self.log.info("Received CTCP PING from %s" % self.source)
                connection.ctcp_reply(self.source,
                                      "PING %s" % event.arguments()[1])

    def on_action(self, connection, event):
        """Handle IRC actions"""
        self.source = event.source().split("@", 1)[0]
        self.target = event.target()
        nick = irclib.nm_to_n(event.source())
        msg = event.arguments()[0]

        self.log.info("%s * %s %s" % (self.target, nick, msg))

    def on_privmsg(self, connection, event):
        """Handle private messages"""
        self.source = event.source().split("@", 1)[0]
        self.target = irclib.nm_to_n(event.source())
        msg = event.arguments()[0]

        if self.target != self.nick:
            self.log.info("<%s> %s" % (self.target, msg))
            self.poll_messages(msg, private=True)

    def on_pubmsg(self, connection, event):
        """Handle public messages"""
        self.source = event.source().split("@", 1)[0]
        self.target = event.target()
        nick = irclib.nm_to_n(event.source())
        msg = event.arguments()[0]

        self.log.info("%s <%s> %s" % (self.target, nick, msg))
        self.poll_messages(msg)


def active_plugins():
    """List active plugins"""
    return ", ".join(plugin.active_plugins())


def active_commands():
    """List active commands"""
    return ", ".join(plugin.active_commands())


def active_keywords():
    """List active keywords"""
    return ", ".join(plugin.active_keywords())
