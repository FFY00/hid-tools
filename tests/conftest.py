#!/bin/env python3
# -*- coding: utf-8 -*-
#
# Copyright (c) 2017 Benjamin Tissoires <benjamin.tissoires@gmail.com>
# Copyright (c) 2017 Red Hat, Inc.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#

import os
import pytest
import resource
import uuid
from base import create_udev_rule, teardown_udev_rule


@pytest.fixture(autouse=True, scope="session")
def udev_rules_setup():
    uid = uuid.uuid4()
    print("setting up the udev rule")
    create_udev_rule(uid)
    yield
    print("tearing down the udev rule")
    teardown_udev_rule(uid)


@pytest.fixture(autouse=True, scope='session')
def setup_rlimit():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


@pytest.fixture(autouse=True, scope='session')
def setup_environ_var():
    os.environ['PYTEST_RUNNING'] = '1'


def skipIfTainted(func):
    def wrapper(*args, **kwargs):
        try:
            with open('/proc/sys/kernel/tainted', 'r') as f:
                if int(f.readline()) & (1 << 5):  # TAINT_BAD_PAGE
                    print('skipping...')
                    return
        except (FileNotFoundError, ValueError):
            pass
        func(*args, **kwargs)
    return wrapper
