#!/bin/env python3
# -*- coding: utf-8 -*-
#
# Copyright (c) 2018 Red Hat, Inc.
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

import argparse
import sys
import os

import parse

import hidtools.hidraw

devices = {}


def get_devices():
    for fname in os.listdir('/dev/'):
        if fname.startswith('hidraw'):
            yield fname


def add_device(path):
    if os.path.exists(path):
        try:
            f = open(path)
            devices[path] = hidtools.hidraw.HidrawDevice(f)
        except IOError as e:
            print(f"can't open '{path}': {str(e)}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description='List HID devices')
    parser.add_argument('-v', '--verbose',
                        action='store_true',
                        help='Tells lshid to be verbose and display detailed information about the devices shown. This includes HID report descriptors.')
    parser.add_argument('-s', metavar='devnum',
                        type=int, dest='devnum',
                        help='Show only devices in specified devnum. ID is given in decimal.')
    parser.add_argument('-d', metavar='[[bus]:][vendor]:[product]',
                        type=str, dest='hid_info',
                        help='Show only devices with the specified bus and/or vendor and product ID. The bus ID is given in decimal, the vendor and product IDs are given in hexadecimal.')
    parser.add_argument('-D', metavar='/dev/hidrawX',
                        type=str, dest='file',
                        help='Do not scan the /dev/ directory, instead display only information about the device whose device file is given.')
    parser.add_argument('-V', '--version',
                        action='store_true',
                        help='Tells lshid to be verbose and display detailed information about the devices shown. This includes HID report descriptors.')
    args = parser.parse_args()

    if args.version:
        print(f'lshid (hid-tools) {hidtools.__version__}')
        return

    if args.devnum is not None:
        add_device(f'/dev/hidraw{args.devnum}')
    elif args.file is not None:
        add_device(args.file)
    else:
        for device in get_devices():
            add_device(f'/dev/{device}')

        if args.hid_info:
            bus_pattern = parse.compile('{bus:d}:{vid:x}:{pid:x}')
            sep_pattern = parse.compile(':{vid:x}:{pid:x}')
            dev_pattern = parse.compile('{vid:x}:{pid:x}')
            for key, device in devices.copy().items():
                for pattern in [bus_pattern, sep_pattern, dev_pattern]:
                    keys = pattern.parse(args.hid_info)
                    if keys:
                        if (
                            'bus' in keys and device.bustype != keys['bus'] or
                            device.vendor_id != keys['vid'] or
                            device.product_id != keys['pid']
                        ):
                            del devices[key]
                        break

    if not devices:
        exit(1)

    first = True
    for key, device in devices.items():
        if args.verbose and not first:
            print()
        print(f'Device {key} Bus {device.bustype:03d}: ID {device.vendor_id:04x}:{device.product_id:04x} {device.name}')
        if args.verbose:
            print('Report Descriptor:')
            device.report_descriptor.dump(output_type='lsusb')
        first = False


if __name__ == '__main__':
    if sys.version_info < (3, 6):
        sys.exit('Python 3.6 or later required')

    main()
