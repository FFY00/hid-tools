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

import fcntl
import libevdev
import os
import pathlib

import logging

import hidtools.hid as hid
from hidtools.uhid import UHIDDevice

logger = logging.getLogger('hidtools.device.base_device')


class SysfsFile(object):
    def __init__(self, path):
        self.path = path

    def __set_value(self, value):
        with open(self.path, 'w') as f:
            return f.write(f'{value}\n')

    def __get_value(self):
        with open(self.path) as f:
            return f.read().strip()

    @property
    def int_value(self):
        return int(self.__get_value())

    @int_value.setter
    def int_value(self, v):
        self.__set_value(v)

    @property
    def str_value(self):
        return self.__get_value()

    @str_value.setter
    def str_value(self, v):
        self.__set_value(v)


class LED(object):
    def __init__(self, udev_object):
        self.sys_path = pathlib.Path(udev_object.sys_path)
        self.max_brightness = SysfsFile(self.sys_path / 'max_brightness').int_value
        self.__brightness = SysfsFile(self.sys_path / 'brightness')

    @property
    def brightness(self):
        return self.__brightness.int_value

    @brightness.setter
    def brightness(self, value):
        self.__brightness.int_value = value


class BaseDevice(UHIDDevice):
    input_type_mapping = {
        'ID_INPUT_TOUCHSCREEN': 'Touch Screen',
        'ID_INPUT_TOUCHPAD': 'Touch Pad',
        'ID_INPUT_TABLET': 'Pen',
        'ID_INPUT_MOUSE': 'Mouse',
        'ID_INPUT_KEY': 'Key',
        'ID_INPUT_JOYSTICK': 'Joystick',
        'ID_INPUT_ACCELEROMETER': 'Accelerometer',
    }

    def __init__(self, name, application, rdesc_str=None, rdesc=None, input_info=None):
        if rdesc_str is None and rdesc is None:
            raise Exception('Please provide at least a rdesc or rdesc_str')
        super().__init__()
        if name is None:
            name = f'uhid gamepad test {self.__class__.__name__}'
        if input_info is None:
            input_info = (3, 1, 2)
        self.name = name
        self.info = input_info
        self.default_reportID = None
        self.opened = False
        self.application = application
        self.input_nodes = {}
        self.led_classes = {}
        self._opened_files = []
        if rdesc is None:
            self.rdesc = hid.ReportDescriptor.from_human_descr(rdesc_str)
        else:
            self.rdesc = rdesc

    def match_evdev_rule(self, application, evdev):
        '''Replace this in subclasses if the device has multiple reports
        of the same type and we need to filter based on the actual evdev
        node.

        returning True will append the corresponding report to
        `self.input_nodes[type]`
        returning False  will ignore this report for the device.
        '''
        return True

    def udev_input_event(self, device):
        if 'DEVNAME' not in device.properties:
            return

        devname = device.properties['DEVNAME']
        if not devname.startswith('/dev/input/event'):
            return

        # associate the Input type to the matching HID application
        # we reuse the guess work from udev
        types = []
        for name, type in BaseDevice.input_type_mapping.items():
            if name in device.properties:
                types.append(type)

        if not types:
            # abort, the device has not been processed by udev
            print('abort', devname, list(device.properties.items()))
            return

        event_node = open(devname, 'rb')
        self._opened_files.append(event_node)
        evdev = libevdev.Device(event_node)

        fd = evdev.fd.fileno()
        flag = fcntl.fcntl(fd, fcntl.F_GETFD)
        fcntl.fcntl(fd, fcntl.F_SETFL, flag | os.O_NONBLOCK)

        for type in types:
            # check for custom defined matching
            if not self.match_evdev_rule(type, evdev):
                evdev.fd.close()
                continue
            self.input_nodes[type] = evdev

    def udev_led_event(self, device):
        led = LED(device)
        self.led_classes[led.sys_path.name] = led

    def udev_event(self, event):
        if event.action != 'add':
            return

        device = event

        subsystem = device.properties['SUBSYSTEM']

        if subsystem == 'input':
            return self.udev_input_event(device)
        elif subsystem == 'leds':
            return self.udev_led_event(device)

        logger.debug(f'{subsystem}: {device}')

    def open(self):
        self.opened = True

    def __del__(self):
        for evdev in self._opened_files:
            evdev.close()

    def close(self):
        self.opened = False

    def start(self, flags):
        pass

    def stop(self):
        to_remove = []
        for name, evdev in self.input_nodes.items():
            evdev.fd.close()
            to_remove.append(name)

        for name in to_remove:
            del(self.input_nodes[name])

    def next_sync_events(self):
        return list(self.evdev.events())

    @property
    def evdev(self):
        if self.application not in self.input_nodes:
            return None

        return self.input_nodes[self.application]
