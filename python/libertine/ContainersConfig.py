# Copyright 2016 Canonical Ltd.
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

import fcntl
import json
import libertine.utils
import os
import sys
from hashlib import md5
from libertine.HostInfo import HostInfo


def read_container_config_file():
    container_list = {}
    container_config_file = libertine.utils.get_libertine_database_file_path()

    if (os.path.exists(container_config_file) and
        os.path.getsize(container_config_file) != 0):
        with open(container_config_file, 'r') as fd:
            container_list = json.load(fd)

    return container_list


def write_container_config_file(container_list):
    container_config_file = libertine.utils.get_libertine_database_file_path()

    with open(container_config_file, 'w') as fd:
        fcntl.lockf(fd, fcntl.LOCK_EX)
        json.dump(container_list, fd, sort_keys=True, indent=4)
        fd.write('\n')
        fcntl.lockf(fd, fcntl.LOCK_UN)


def container_config_hash():
    checksum = md5()
    container_config_file = libertine.utils.get_libertine_database_file_path()
    if (os.path.exists(container_config_file) and os.path.getsize(container_config_file) != 0):
        with open(container_config_file, "rb") as f:
            for chunk in iter(lambda: f.read(128 * checksum.block_size), b""):
                checksum.update(chunk)
    return checksum.hexdigest()


class ContainersConfig(object):

    def __init__(self):
        self.checksum = None
        self.refresh_database()

        if "defaultContainer" in self.container_list:
            self.default_container_id = self.container_list['defaultContainer']
        else:
            self.default_container_id = None

    """
    Private helper methods
    """
    def _get_container_entry(self, container_id):
        if not self.container_list:
            return None

        for container in self.container_list['containerList']:
            if container['id'] == container_id:
                return container

        return None

    def _get_value_by_key(self, container_id, key):
        container = self._get_container_entry(container_id)

        if not container or key not in container:
            return None
        else:
            return container[key]

    def _get_array_object_value_by_key(self, container_id, array_key, object_key, matcher, key):
        for item in self._get_value_by_key(container_id, array_key):
            if item[object_key] == matcher:
                return item[key]

    def _set_value_by_key(self, container_id, key, value):
        container = self._get_container_entry(container_id)

        if not container:
            return

        if type(value) is str:
            container[key] = value
        elif type(value) is dict:
            if key not in container:
                container[key] = [value]
            else:
                container[key].append(value)

        write_container_config_file(self.container_list)

    def _set_array_object_value_by_key(self, container_id, array_key, object_key, matcher, key, value):
        container = self._get_container_entry(container_id)

        if not container:
            return

        for item in container[array_key]:
            if item[object_key] == matcher:
                item[key] = value
                write_container_config_file(self.container_list)
                return

    def _delete_array_object_by_key_value(self, container_id, array_key, object_key, value):
        container = self._get_container_entry(container_id)

        if not container:
            return

        for item in container[array_key]:
            if item[object_key] == value:
                container[array_key].remove(item)
                write_container_config_file(self.container_list)
                return

    def _test_key_value_exists(self, container_id, key, value=None):
        key_value = self._get_value_by_key(container_id, key)

        if not key_value:
            return False
        elif key == 'id':
            return True
        elif key_value == value:
            return True
        else:
            return False

    def _test_array_object_key_value_exists(self, container_id, array_key, object_key, value):
        array = self._get_value_by_key(container_id, array_key)

        if not array:
            return False

        for item in array:
            if item[object_key] == value:
                return True

        return False

    """
    Miscellaneous ContainersConfig.json operations
    """
    def refresh_database(self):
        checksum = container_config_hash()
        if checksum != self.checksum:
            self.container_list = read_container_config_file()
            self.checksum = checksum

    def _find_duplicate_container_entry(self, container_list, container_id):
        for container in container_list['containerList']:
            if container['id'] == container_id:
                return container

        return None

    def merge_container_config_files(self, filepath):
        merged_json = []

        with open(filepath, 'r') as fd:
            merge_source = json.load(fd)

        if "containerList" in self.container_list:
            # Finds any duplicate entries and assumes we want to update the main config
            # with entries from the merge source.
            for i, container in enumerate(self.container_list['containerList']):
                merge_container = self._find_duplicate_container_entry(merge_source, container['id'])
                if merge_container:
                    self.container_list['containerList'][i] = merge_container
                    merge_source['containerList'].remove(merge_container)

            # Merges in any remaining non-duplicate entries.
            for container in merge_source['containerList']:
                self.container_list['containerList'].append(container)

        else:
            self.container_list = merge_source

        write_container_config_file(self.container_list)

    def check_container_id(self, container_id):
        if container_id and not self.container_exists(container_id):
            print("Container id \'%s\' does not exist." % container_id, file=sys.stderr)
            sys.exit(1)
        elif not container_id:
            return self.get_default_container_id()

        return container_id

    def get_default_container_id(self):
        return self.default_container_id

    def set_default_container_id(self, container_id, write_json=False):
        self.default_container_id = container_id
        self.container_list['defaultContainer'] = container_id

        if write_json:
            write_container_config_file(self.container_list)

    def clear_default_container_id(self, write_json=False):
        self.default_container_id = None
        self.container_list.pop('defaultContainer', None)

        if write_json:
            write_container_config_file(self.container_list)

    """
    Operations for the container itself.
    """
    def add_new_container(self, container_id, container_name, container_type, container_distro):
        container_obj = {'id': container_id, 'installStatus': 'new', 'type': container_type,
                         'distro': container_distro, 'name': container_name, 'installedApps': []}

        if "defaultContainer" not in self.container_list:
            self.set_default_container_id(container_id)

        if "containerList" not in self.container_list:
            self.container_list['containerList'] = [container_obj]
        else:
            self.container_list['containerList'].append(container_obj)

        write_container_config_file(self.container_list)

    def delete_container(self, container_id):
        if not self.container_list:
            print("Unable to delete container.  No containers defined.")
            sys.exit(1)

        container = self._get_container_entry(container_id)

        self.container_list['containerList'].remove(container)

        # Set a new defaultContainer if the current default is being deleted.
        if self.container_list['defaultContainer'] == container_id and self.container_list['containerList']:
            self.set_default_container_id(self.container_list['containerList'][0]['id'])
        # Remove the defaultContainer if there are no more containers left.
        elif not self.container_list['containerList']:
            self.clear_default_container_id()

        write_container_config_file(self.container_list)

    def update_container_install_status(self, container_id, new_status):
        self._set_value_by_key(container_id, 'installStatus', new_status)

    def container_exists(self, container_id):
        return self._test_key_value_exists(container_id, 'id')

    def update_container_multiarch_support(self, container_id, multiarch):
        if multiarch == 'enabled' and HostInfo().get_host_architecture() == 'i386':
            multiarch = 'disabled'

        self._set_value_by_key(container_id, 'multiarch', multiarch)

    def get_container_multiarch_support(self, container_id):
        multiarch_support = self._get_value_by_key(container_id, 'multiarch')

        if multiarch_support == None:
            return 'disabled'
        else:
            return multiarch_support

    """
    Operations for archive (PPA) maintenance in a Libertine container.
    """
    def add_container_archive(self, container_id, archive_name):
        archive_obj = {'archiveName': archive_name, 'archiveStatus': 'new'}
        self._set_value_by_key(container_id, 'extraArchives', archive_obj)

    def delete_container_archive(self, container_id, archive_name):
        self._delete_array_object_by_key_value(container_id, 'extraArchives',
                                               'archiveName', archive_name)

    def update_archive_install_status(self, container_id, archive_name, new_status):
        self._set_array_object_value_by_key(container_id, 'extraArchives', 'archiveName',
                                            archive_name, 'archiveStatus', new_status)

    def get_archive_install_status(self, container_id, archive_name):
        return self._get_array_object_value_by_key(container_id, 'extraArchives', 'archiveName',
                                            archive_name, 'archiveStatus')

    def archive_exists(self, container_id, archive_name):
        return self._test_array_object_key_value_exists(container_id, 'extraArchives', 'archiveName',
                                                        archive_name)

    """
    Operations for package maintenance in a Libertine container.
    """
    def add_new_package(self, container_id, package_name):
        package_obj = {'packageName': package_name, 'appStatus': 'new'}
        self._set_value_by_key(container_id, 'installedApps', package_obj)

    def delete_package(self, container_id, package_name):
        self._delete_array_object_by_key_value(container_id, 'installedApps',
                                               'packageName', package_name)

    def update_package_install_status(self, container_id, package_name, new_status):
        self._set_array_object_value_by_key(container_id, 'installedApps', 'packageName',
                                            package_name, 'appStatus', new_status)

    def get_package_install_status(self, container_id, package_name):
        return self._get_array_object_value_by_key(container_id, 'installedApps', 'packageName',
                                                   package_name, 'appStatus')

    def package_exists(self, container_id, package_name):
        return self._test_array_object_key_value_exists(container_id, 'installedApps', 'packageName',
                                                        package_name)

    """
    Fetcher functions for various configuration information.
    """
    def get_container_distro(self, container_id):
        return self._get_value_by_key(container_id, 'distro')

    def get_container_type(self, container_id):
        return self._get_value_by_key(container_id, 'type')
