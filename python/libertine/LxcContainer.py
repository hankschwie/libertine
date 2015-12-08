# Copyright 2015 Canonical Ltd.
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3, as published
# by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranties of
# MERCHANTABILITY, SATISFACTORY QUALITY, or FITNESS FOR A PARTICULAR
# PURPOSE.  See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program.  If not, see <http://www.gnu.org/licenses/>.

import crypt
import lxc
import os
import shlex
import subprocess
from .Libertine import (
        BaseContainer, get_container_distro, get_host_architecture,
        create_libertine_user_data_dir)
from . import utils


home_path = os.environ['HOME']


def check_lxc_net_entry(entry):
    lxc_net_file = open('/etc/lxc/lxc-usernet')
    found = False

    for line in lxc_net_file:
        if entry in line:
            found = True
            break

    return found


def setup_host_environment(username, password):
    lxc_net_entry = "%s veth lxcbr0 10" % str(username)

    if not check_lxc_net_entry(lxc_net_entry):
        passwd = subprocess.Popen(["sudo", "--stdin", "usermod", "--add-subuids", "100000-165536",
                                   "--add-subgids", "100000-165536", str(username)],
                                  stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.STDOUT)
        passwd.communicate((password + '\n').encode('UTF-8'))

        add_user_cmd = "echo %s | sudo tee -a /etc/lxc/lxc-usernet > /dev/null" % lxc_net_entry
        subprocess.Popen(add_user_cmd, shell=True)


def get_lxc_default_config_path():
    return os.path.join(home_path, '.config', 'lxc')


def lxc_container(container_id):
    config_path = utils.get_libertine_containers_dir_path()
    container = lxc.Container(container_id, config_path)

    return container


class LibertineLXC(BaseContainer):
    """
    A concrete container type implemented using an LXC container.
    """

    def __init__(self, container_id):
        super().__init__(container_id)
        self.container = lxc_container(container_id)
        self.series = get_container_distro(container_id)

    def is_running(self):
        return self.container.running

    def start_container(self):
        if not self.container.running:
            if not self.container.start():
                raise RuntimeError("Container failed to start")
            if not self.container.wait("RUNNING", 10):
                raise RuntimeError("Container failed to enter the RUNNING state")

        if not self.container.get_ips(timeout=30):
            raise RuntimeError("Not able to connect to the network.")

        self.run_in_container("umount /tmp/.X11-unix")

    def stop_container(self):
        self.container.stop()

    def run_in_container(self, command_string):
        cmd_args = shlex.split(command_string)
        return self.container.attach_wait(lxc.attach_run_command, cmd_args)

    def destroy_libertine_container(self):
        if self.container.defined:
            self.container.stop()
            self.container.destroy()

    def create_libertine_container(self, password=None, verbosity=1):
        if password is None:
            return

        installed_release = self.series

        username = os.environ['USER']
        user_id = os.getuid()
        group_id = os.getgid()

        setup_host_environment(username, password)

        # Generate the default lxc default config, if it doesn't exist
        config_path = get_lxc_default_config_path()
        config_file = "%s/default.conf" % config_path

        if not os.path.exists(config_path):
            os.mkdir(config_path)

        if not os.path.exists(config_file):
            with open(config_file, "w+") as fd:
                fd.write("lxc.network.type = veth\n")
                fd.write("lxc.network.link = lxcbr0\n")
                fd.write("lxc.network.flags = up\n")
                fd.write("lxc.network.hwaddr = 00:16:3e:xx:xx:xx\n")
                fd.write("lxc.id_map = u 0 100000 %s\n" % user_id)
                fd.write("lxc.id_map = g 0 100000 %s\n" % group_id)
                fd.write("lxc.id_map = u %s %s 1\n" % (user_id, user_id))
                fd.write("lxc.id_map = g %s %s 1\n" % (group_id, group_id))
                fd.write("lxc.id_map = u %s %s %s\n" % (user_id + 1, (user_id + 1) + 100000, 65536 - (user_id + 1)))
                fd.write("lxc.id_map = g %s %s %s\n" % (group_id + 1, (group_id + 1) + 100000, 65536 - (user_id + 1)))

        create_libertine_user_data_dir(self.container_id)

        # Figure out the host architecture
        architecture = get_host_architecture()

        if not self.container.create("download", 0,
                                     {"dist": "ubuntu",
                                      "release": installed_release,
                                      "arch": architecture}):
            return False

        self.create_libertine_config()

        if verbosity == 1:
            print("starting container ...")
        self.start_container()
        self.run_in_container("userdel -r ubuntu")
        self.run_in_container("useradd -u {} -p {} -G sudo {}".format(
                str(user_id), crypt.crypt(password), str(username)))

        if verbosity == 1:
            print("Updating the contents of the container after creation...")
        self.update_packages(verbosity)

        if verbosity == 1:
            print("Installing Matchbox as the Xmir window manager...")
        self.install_package('matchbox', verbosity=verbosity)

        if verbosity == 1:
            print("stopping container ...")
        self.stop_container()

    def create_libertine_config(self):
        user_id = os.getuid()
        home_entry = (
            "%s %s none bind,create=dir"
            % (utils.get_libertine_container_userdata_dir_path(self.container_id),
               home_path.strip('/'))
        )

        # Bind mount the user's home directory
        self.container.append_config_item("lxc.mount.entry", home_entry)

        xdg_user_dirs = ['Documents', 'Music', 'Pictures', 'Videos']

        for user_dir in xdg_user_dirs:
            xdg_user_dir_entry = (
                "%s/%s %s/%s none bind,create=dir,optional"
                % (home_path, user_dir, home_path.strip('/'), user_dir)
            )
            self.container.append_config_item("lxc.mount.entry", xdg_user_dir_entry)

        # Setup the mounts for /run/user/$user_id
        run_user_entry = "/run/user/%s run/user/%s none rbind,create=dir" % (user_id, user_id)
        self.container.append_config_item("lxc.mount.entry", "tmpfs run tmpfs rw,nodev,noexec,nosuid,size=5242880")
        self.container.append_config_item("lxc.mount.entry",
                                          "none run/user tmpfs rw,nodev,noexec,nosuid,size=104857600,mode=0755,create=dir")
        self.container.append_config_item("lxc.mount.entry", run_user_entry)

        self.container.append_config_item("lxc.include", "/usr/share/libertine/libertine-lxc.conf")

        # Dump it all to disk
        self.container.save_config()
