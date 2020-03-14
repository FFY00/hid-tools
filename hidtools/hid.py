#!/bin/env python3
# -*- coding: utf-8 -*-
#
# Copyright (c) 2012-2017 Benjamin Tissoires <benjamin.tissoires@gmail.com>
# Copyright (c) 2012-2017 Red Hat, Inc.
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

import copy
import sys
from hidtools.hut import HUT
from hidtools.util import twos_comp, to_twos_comp
from parse import parse as _parse
import logging
logger = logging.getLogger('hidtools.hid')

hid_items = {
    "Main": {
        "Input"			: 0b10000000,
        "Output"		: 0b10010000,
        "Feature"		: 0b10110000,
        "Collection"		: 0b10100000,
        "End Collection"	: 0b11000000,
    },

    "Global": {
        "Usage Page"		: 0b00000100,
        "Logical Minimum"	: 0b00010100,
        "Logical Maximum"	: 0b00100100,
        "Physical Minimum"	: 0b00110100,
        "Physical Maximum"	: 0b01000100,
        "Unit Exponent"		: 0b01010100,
        "Unit"			: 0b01100100,
        "Report Size"		: 0b01110100,
        "Report ID"		: 0b10000100,
        "Report Count"		: 0b10010100,
        "Push"			: 0b10100100,
        "Pop"			: 0b10110100,
    },

    "Local": {
        "Usage"			: 0b00001000,
        "Usage Minimum"		: 0b00011000,
        "Usage Maximum"		: 0b00101000,
        "Designator Index"	: 0b00111000,
        "Designator Minimum"	: 0b01001000,
        "Designator Maximum"	: 0b01011000,
        "String Index"		: 0b01111000,
        "String Minimum"	: 0b10001000,
        "String Maximum"	: 0b10011000,
        "Delimiter"		: 0b10101000,
    },
}

collections = {
    'PHYSICAL'			: 0,
    'APPLICATION'		: 1,
    'LOGICAL'			: 2,
    'REPORT'			: 3,
    'NAMED_ARRAY'		: 4,
    'USAGE_SWITCH'		: 5,
    'USAGE_MODIFIER'		: 6,
}

sensor_mods = {
    0x00: 'Mod None',
    0x10: 'Mod Change Sensitivity Abs',
    0x20: 'Mod Max',
    0x30: 'Mod Min',
    0x40: 'Mod Accuracy',
    0x50: 'Mod Resolution',
    0x60: 'Mod Threshold High',
    0x70: 'Mod Threshold Low',
    0x80: 'Mod Calibration Offset',
    0x90: 'Mod Calibration Multiplier',
    0xa0: 'Mod Report Interval',
    0xb0: 'Mod Frequency Max',
    0xc0: 'Mod Period Max',
    0xd0: 'Mod Change Sensitivity Range Percent',
    0xe0: 'Mod Change Sensitivity Rel Percent',
    0xf0: 'Mod Vendor Reserved',
}

inv_hid = {}  # e.g 0b10000000 : "Input"
hid_type = {}  # e.g. "Input" : "Main"
for type, items in hid_items.items():
    for k, v in items.items():
        inv_hid[v] = k
        hid_type[k] = type


INV_COLLECTIONS = dict([(v, k) for k, v in collections.items()])


class ParseError(Exception):
    """Exception thrown during report descriptor parsing"""
    pass


class RangeError(Exception):
    """Exception thrown for an out-of-range value

    .. attribute:: value

        The invalid value

    .. attribute:: range

        A ``(min, max)`` tuple for the allowed logical range

    .. attribute:: field

        The :class:`HidField` that triggered this exception
    """
    def __init__(self, field, value):
        self.field = field
        self.range = field.logical_min, field.logical_max
        self.value = value

    def __str__(self):
        min, max = self.range
        return f'Value {self.value} is outside range {min}, {max} for {self.field.usage_name}'


class _HidRDescItem(object):
    """Represents one item in the Report Descriptor. This is a variable-sized
    element with one header byte and 0, 1, 2, 4 payload bytes.

    :param int index_in_report:
        The index within the report descritor
    :param int hid:
        The numerical hid type (e.g. ``0b00000100`` for Usage Page)
    :param int value:
        The 8, 16, or 32 bit value
    :param list raw_values:
        The payload bytes' raw values, LSB first


    These items are usually parsed from a report descriptor, see
    :meth:`from_bytes`. The report descriptor
    bytes are::

                H P P H H P H P

    where each header byte looks like this

    +---------+---+---+---+---+---+---+---+---+
    | bit     | 7 | 6 | 5 | 4 | 3 | 2 | 1 | 0 |
    +=========+===+===+===+===+===+===+===+===+
    |         |   hid item type       | size  |
    +---------+-----------------------+-------+

    .. note:: a size of 0x3 means payload size 4

    To create a _HidRDescItem from a human-readable description, use
    :meth:`from_human_descr`.



    .. attribute:: index_in_report

        The numerical index of this item in the report descriptor.

    .. attribute:: raw_value

        A list of the payload's raw values

    .. attribute:: hid

        The hid item as number (e.g. ``0b00000100`` for Usage Page)

    .. attribute:: item

        The hid item as string (e.g. "Usage Page")

    .. attribute:: value

        The payload value as single number

    """
    def __init__(self, index_in_report, hid, value, raw_values):
        self.index_in_report = index_in_report
        self.raw_value = raw_values
        self.hid = hid
        self.value = value
        try:
            self.item = inv_hid[self.hid]
        except KeyError:
            error = f'error while parsing {hid:02x}'
            raise KeyError(error)

        if self.item in ("Logical Minimum",
                         "Physical Minimum",
                         # "Logical Maximum",
                         # "Physical Maximum",
                         ):
            self._twos_comp()
        if self.item == "Unit Exponent" and self.value > 7:
            self.value -= 16

    def _twos_comp(self):
        self.value = twos_comp(self.value, (self.size - 1) * 8)
        return self.value

    @property
    def size(self):
        """The size in bytes, including header byte"""
        return 1 + len(self.raw_value)

    @property
    def bytes(self):
        """
        Return this in the original format in bytes, i.e. a header byte
        followed by (if any) payload bytes.

        :returns: a list of bytes that are this item
        """
        if len(self.raw_value) == 4:
            h = self.hid | 0x3
        else:
            h = self.hid | len(self.raw_value)
        return [h] + self.raw_value.copy()

    def __repr__(self):
        data = [f'{i:02x}' for i in self.bytes]
        return f'{" ".join(data)}'

    def _get_raw_values(self):
        """The raw values as comma-separated hex numbers"""
        data = str(self)
        # prefix each individual value by "0x" and insert "," in between
        data = f'0x{data.replace(" ", ", 0x")},'
        return data

    def get_human_descr(self, indent):
        """
        Return a human-readable description of this item

        :param int indent: The indentation to prefix
        """
        item = self.item
        value = self.value
        up = self.usage_page
        descr = item
        if item in ("Report ID",
                    "Usage Minimum",
                    "Usage Maximum",
                    "Logical Minimum",
                    "Physical Minimum",
                    "Logical Maximum",
                    "Physical Maximum",
                    "Report Size",
                    "Report Count",
                    "Unit Exponent"):
            descr += f' ({str(value)})'
        elif item == "Collection":
            descr += f' ({INV_COLLECTIONS[value].capitalize()})'
            indent += 1
        elif item == "End Collection":
            indent -= 1
        elif item == "Usage Page":
            try:
                descr += f' ({HUT[value].page_name})'
            except KeyError:
                descr += f' (Vendor Usage Page 0x{value:02x})'
        elif item == "Usage":
            usage = value | up
            try:
                descr += f' ({HUT[up >> 16][value]})'
            except KeyError:
                if (up >> 16) == HUT.usage_page_from_name('Sensor').page_id:
                    mod = (usage & 0xF000) >> 8
                    usage &= ~0xF000
                    mod_descr = sensor_mods[mod]
                    page_id = (usage & 0xFF00) >> 16
                    try:
                        descr += f' ({HUT[page_id][usage & 0xFF]}  | {mod_descr})'
                    except KeyError:
                        descr += f' (Unknown Usage 0x{value:02x})'
                else:
                    descr += f' (Vendor Usage 0x{value:02x})'
        elif item == "Input" \
                or item == "Output" \
                or item == "Feature":
            descr += " ("
            if value & (0x1 << 0):
                descr += "Cnst,"
            else:
                descr += "Data,"
            if value & (0x1 << 1):
                descr += "Var,"
            else:
                descr += "Arr,"
            if value & (0x1 << 2):
                descr += "Rel"
            else:
                descr += "Abs"
            if value & (0x1 << 3):
                descr += ",Wrap"
            if value & (0x1 << 4):
                descr += ",NonLin"
            if value & (0x1 << 5):
                descr += ",NoPref"
            if value & (0x1 << 6):
                descr += ",Null"
            if value & (0x1 << 7):
                descr += ",Vol"
            if value & (0x1 << 8):
                descr += ",Buff"
            descr += ")"
        elif item == "Unit":
            systems = ("None", "SILinear", "SIRotation",
                       "EngLinear", "EngRotation")
            lengths = ("None", "Centimeter", "Radians", "Inch", "Degrees")
            masses = ("None", "Gram", "Gram", "Slug", "Slug")
            times = ("Seconds", "Seconds", "Seconds", "Seconds")
            temperatures = ("None", "Kelvin", "Kelvin", "Fahrenheit", "Fahrenheit")
            currents = ("Ampere", "Ampere", "Ampere", "Ampere")
            luminous_intensisties = ("Candela", "Candela", "Candela", "Candela")
            units = (lengths, masses, times, temperatures,
                     currents, luminous_intensisties)

            system = value & 0xf

            descr += " ("
            for i in range(len(units), 0, -1):
                v = (value >> i * 4) & 0xf
                v = twos_comp(v, 4)
                if v:
                    descr += units[i - 1][system]
                    if v != 1:
                        descr += '^' + str(v)
                    descr += ","
            descr += systems[system] + ')'
        elif item == "Push":
            pass
        elif item == "Pop":
            pass
        eff_indent = indent
        if item == "Collection":
            eff_indent -= 1
        return ' ' * eff_indent + descr, indent

    @classmethod
    def _one_item_from_bytes(cls, rdesc):
        """
        Parses a single (the first) item from the given report descriptor.

        :param rdesc: a series of bytes representing the report descriptor

        :returns: a single _HidRDescItem from the first ``item.size`` bytes
                of the descriptor

        .. note:: ``item.index_in_report`` is always 0 when using this function
        """
        idx = 0
        header = rdesc[idx]
        if header == 0 and idx == len(rdesc) - 1:
            # some devices present a trailing 0, skipping it
            return None

        index_in_report = 0  # always zero, oh well
        size = header & 0x3
        if size == 3:
            size = 4
        hid = header & 0xfc
        if hid == 0:
            raise ParseError(f'Unexpected HID type 0 in {header:02x}')

        value = 0
        raw_values = []

        idx += 1
        if size >= 1:
            v = rdesc[idx]
            idx += 1
            raw_values.append(v)
            value |= v
        if size >= 2:
            v = rdesc[idx]
            idx += 1
            raw_values.append(v)
            value |= v << 8
        if size >= 4:
            v = rdesc[idx]
            idx += 1
            raw_values.append(v)
            value |= v << 16
            v = rdesc[idx]
            idx += 1
            raw_values.append(v)
            value |= v << 24

        return _HidRDescItem(index_in_report, hid, value, raw_values)

    @classmethod
    def from_bytes(cls, rdesc):
        """
        Parses a series of bytes into items.

        :param list rdesc: a series of bytes that are a HID report
                descriptor

        :returns: a list of items representing this report descriptor
        """
        items = []
        idx = 0
        while idx < len(rdesc):
            item = _HidRDescItem._one_item_from_bytes(rdesc[idx:])
            if item is None:
                break
            item.index_in_report = idx
            items.append(item)
            idx += item.size

        return items

    @classmethod
    def from_human_descr(cls, line, usage_page):
        """
        Parses a line from human-readable HID report descriptor e.g.::

            Usage Page (Digitizers)
            Usage (Finger)
            Collection (Logical)
             Report Size (1)
             Report Count (1)
             Logical Minimum (0)
             Logical Maximum (1)
             Usage (Tip Switch)
             Input (Data,Var,Abs)


        :param str line: a single line in the report descriptor
        :param int usage_page: the usage page to set for this item

        :returns: a single item representing the current line
        """
        data = None
        if '(' in line:
            r = _parse('{ws:s}{name} ({data})', line)
            assert(r is not None)
            name = r['name']
            data = r['data']
            if data.lower().startswith('0x'):
                try:
                    data = int(data[2:], 16)
                except ValueError:
                    pass
            else:
                try:
                    data = int(data)
                except ValueError:
                    pass
        else:
            name = line.strip()

        value = None

        if isinstance(data, str):
            if name == "Usage Page":
                value = HUT.usage_page_from_name(data).page_id
                usage_page = value
            elif name == "Usage":
                value = HUT[usage_page].from_name[data].usage
            elif name == "Collection":
                value = collections[data.upper()]
            elif name in 'Input Output Feature':
                value = 0
                possible_types = (
                    'Cnst',
                    'Var',
                    'Rel',
                    'Wrap',
                    'NonLin',
                    'NoPref',
                    'Null',
                    'Vol',
                    'Buff',
                )
                for i, v in enumerate(possible_types):
                    if v in data:
                        value |= (0x1 << i)
            elif name == 'Unit':
                systems = ("None", "SILinear", "SIRotation", "EngLinear", "EngRotation")
                lengths = ("None", "Centimeter", "Radians", "Inch", "Degrees")
                masses = ("None", "Gram", "Gram", "Slug", "Slug")
                times = ("Seconds", "Seconds", "Seconds", "Seconds")
                temperatures = ("None", "Kelvin", "Kelvin", "Fahrenheit", "Fahrenheit")
                currents = ("Ampere", "Ampere", "Ampere", "Ampere")
                luminous_intensisties = ("Candela", "Candela", "Candela", "Candela")
                units = (lengths, masses, times, temperatures,
                         currents, luminous_intensisties)

                r = None
                if '^' in data:
                    r = _parse('{unit}^{exp:d},{system}', data)
                    assert(r is not None)
                else:
                    r = _parse('{unit},{system}', data)
                    assert(r is not None)
                unit = r['unit']
                try:
                    exp = r['exp']
                except KeyError:
                    exp = 1
                system = r['system']

                system = systems.index(system)

                for i, u in enumerate(units):
                    if unit in u:
                        unit = i + 1
                        break

                unit_value = to_twos_comp(exp, 4)
                unit_value <<= unit * 4

                value = unit_value | system
        else:  # data has been converted to an int already
            if name == "Usage Page":
                usage_page = data
            value = data

        bit_size = 0
        if value is not None:
            bit_size = len(f'{value + 1:x}') * 4
        else:
            value = 0
        tag = hid_items[hid_type[name]][name]
        v_count = 0
        if bit_size == 0:
            pass
        elif bit_size <= 8:
            v_count = 1
        elif bit_size <= 16:
            v_count = 2
        else:
            v_count = 4

        if name == "Unit Exponent" and value < 0:
            value += 16
            value = to_twos_comp(value, v_count * 8)

        v = value
        vs = []
        for i in range(v_count):
            vs.append(v & 0xff)
            v >>= 8

        item = _HidRDescItem(0, tag, value, vs)
        item.usage_page = usage_page << 16

        return item

    def dump_rdesc_kernel(self, indent, dump_file):
        """
        Write the HID item to the file a C-style format, e.g. ::

            0x05, 0x01,			/* Usage Page (Generic Desktop)			*/

        :param int indent: indentation to prefix
        :param File dump_file: file to write to
        """
        # offset = self.index_in_report
        line = self._get_raw_values()
        line += "\t" * (int((40 - len(line)) / 8))

        descr, indent = self.get_human_descr(indent)

        descr += "\t" * (int((52 - len(descr)) / 8))
        # dump_file.write(f'{line}/* {descr} {str(offset)} */\n')
        dump_file.write(f'\t{line}/* {descr}*/\n')
        return indent

    def dump_rdesc_array(self, indent, dump_file):
        """
        Format the hid item in hexadecimal format with a
        double-slash comment, e.g. ::

           0x05, 0x01,                    // Usage Page (Generic Desktop)        0

        :param int indent: indentation to prefix
        :param File dump_file: file to write to
        """
        offset = self.index_in_report
        line = self._get_raw_values()
        line += " " * (30 - len(line))

        descr, indent = self.get_human_descr(indent)

        descr += " " * (35 - len(descr))
        dump_file.write(f'{line} // {descr} {str(offset)}\n')
        return indent

    def dump_rdesc_lsusb(self, indent, dump_file):
        """
        Format the hid item in a lsusb -v format.
        """
        descr, indent = self.get_human_descr(indent)
        dump_file.write(f'  {descr}\n')
        return indent


class HidField(object):
    """
    Represents one field in a HID report. A field is one element of a HID
    report that matches a specific set of bits in that report.

    .. attribute:: usage

        A string with HID field's Usage, e.g. "Wheel". If the field has
        multiple usages, this refers to the first one.

    .. attribute:: usage_page

        The string with HID field's Usage Page, e.g. "Generic Desktop"

    .. attribute:: report_ID

        The numeric Report ID this HID field belongs to

    .. attribute:: logical_min

        The logical minimum of this HID field

    .. attribute:: logical_max

        The logical maximum of this HID field

    .. attribute:: size

        Report Size in bits for this HID field

    .. attribute:: count

        Report Count for this HID field
    """
    def __init__(self,
                 report_ID,
                 logical,
                 physical,
                 application,
                 collection,
                 value,
                 usage_page,
                 usage,
                 logical_min,
                 logical_max,
                 item_size,
                 count):
        self.report_ID = report_ID
        self.logical = logical
        self.physical = physical
        self.application = application
        self.collection = collection
        self.type = value
        self.usage_page = usage_page
        self.usage = usage
        self.usages = None
        self.logical_min = logical_min
        self.logical_max = logical_max
        self.size = item_size
        self.count = count

    def copy(self):
        """
        Return a full copy of this HIDField.
        """
        c = copy.copy(self)
        if self.usages is not None:
            c.usages = self.usages[:]
        return c

    def _usage_name(self, usage):
        usage_page = usage >> 16
        value = usage & 0x0000FFFF
        if usage_page in HUT:
            if HUT[usage_page].page_name == "Button":
                name = f'B{str(value)}'
            else:
                try:
                    name = HUT[usage_page][value]
                except KeyError:
                    name = f'0x{usage:04x}'
        else:
            name = f'0x{usage:04x}'
        return name

    @property
    def usage_name(self):
        """
        The Usage name for this field (e.g. "Wheel").
        """
        return self._usage_name(self.usage)

    def get_usage_name(self, index):
        """
        Return the Usage name for this field at the given index. Use this
        function when the HID field has multiple Usages.
        """
        return self._usage_name(self.usages[index])

    @property
    def physical_name(self):
        """
        The physical name or ``None``
        """
        phys = self.physical
        if phys is None:
            return phys

        try:
            page_id = phys >> 16
            value = phys & 0xFF
            phys = HUT[page_id][value]
        except KeyError:
            try:
                phys = f'0x{phys:04x}'
            except:
                pass
        return phys

    def _get_value(self, report, idx):
        """
        Extract the bits that are this HID field in the list of bytes
        ``report``

        :param list report: a list of bytes that represent a HID report
        :param int idx: which field index to fetch, only greater than 0 if
            :attr:`count` is larger than 1
        """
        value = 0
        start_bit = self.start + self.size * idx
        end_bit = start_bit + self.size * (idx + 1)
        data = report[int(start_bit / 8): int(end_bit / 8 + 1)]
        if len(data) == 0:
            return ["<.>"]
        for d in range(len(data)):
            value |= data[d] << (8 * d)

        bit_offset = start_bit % 8
        value = value >> bit_offset
        garbage = (value >> self.size) << self.size
        value = value - garbage
        if self.logical_min < 0 and self.size > 1:
            value = twos_comp(value, self.size)
        return value

    def get_values(self, report):
        """
        Assume ``report`` is a list of bytes that are a full HID report,
        extract the values that are this HID field.

        Example:

        - if this field is Usage ``X`` , this returns ``[x-value]``
        - if this field is Usage ``X``, ``Y``, this returns ``[x, y]``
        - if this field is a button mask, this returns ``[1, 0, 1, ...]``, i.e. one value for each
          button

        :param list report: a list of bytes that are a HID report
        :returns: a list of integer values of len :attr:`count`
        """
        return [self._get_value(report, i) for i in range(self.count)]

    def _fill_value(self, report, value, idx):
        start_bit = self.start + self.size * idx
        n = self.size

        max = (1 << n) - 1
        if value > max:
            raise Exception(f'_set_value(): value {value} is larger than size {self.size}')

        byte_idx = int(start_bit / 8)
        bit_shift = start_bit % 8
        bits_to_set = 8 - bit_shift

        while n - bits_to_set >= 0:
            report[byte_idx] &= ~(0xff << bit_shift)
            report[byte_idx] |= (value << bit_shift) & 0xff
            value >>= bits_to_set
            n -= bits_to_set
            bits_to_set = 8
            bit_shift = 0
            byte_idx += 1

        # last nibble
        if n:
            bit_mask = (1 << n) - 1
            report[byte_idx] &= ~(bit_mask << bit_shift)
            report[byte_idx] |= value << bit_shift

    def fill_values(self, report, data):
        """
        Assuming ``data`` is the value for this HID field and ``report`` is
        a HID report's bytes, this method sets those bits in ``report`` that
        are his HID field to ``value``.

        Example:

        - if this field is Usage ``X`` , use ``fill_values(report, [x-value])``
        - if this field is Usage ``X``, ``Y``, use ``fill_values(report, [x, y])``
        - if this field is a button mask, use
          ``fill_values(report, [1, 0, 1, ...]``, i.e. one value for each
          button

        ``data`` must have at least :attr:`count` elements, matching this
        field's Report Count.


        :param list report: an integer array representing this report,
            modified in place
        :param list data: the data for this hid field with one element for
            each Usage.
        """
        if len(data) != self.count:
            raise Exception("-EINVAL")

        for idx in range(self.count):
            v = data[idx]

            if self.is_null:
                # FIXME: handle the signed case too
                if v >= (1 << self.size):
                    raise RangeError(self, v)
            elif self.usage_name not in ['Contact Id', 'Contact Max', 'Contact Count']:
                if v < self.logical_min or v > self.logical_max:
                    raise RangeError(self, v)
            if self.logical_min < 0:
                v = to_twos_comp(v, self.size)
            self._fill_value(report, v, idx)

    @property
    def is_array(self):
        """
        ``True`` if this HID field is an array
        """
        return not (self.type & (0x1 << 1))  # Variable

    @property
    def is_const(self):
        """
        ``True`` if this HID field is const
        """
        return self.type & (0x1 << 0)

    @property
    def is_null(self):
        """
        ``True`` if this HID field is null
        """
        return self.type & (0x1 << 6)

    @property
    def usage_page_name(self):
        """
        The Usage Page name for this field, e.g. "Generic Desktop"
        """
        usage_page_name = ''
        usage_page = self.usage_page >> 16
        try:
            usage_page_name = HUT[usage_page].page_name
        except KeyError:
            pass
        return usage_page_name

    @classmethod
    def getHidFields(cls,
                     report_ID,
                     logical,
                     physical,
                     application,
                     collection,
                     value,
                     usage_page,
                     usages,
                     usage_min,
                     usage_max,
                     logical_min,
                     logical_max,
                     item_size,
                     count):
        """
        This is a function to be called by a HID report descriptor parser.

        Given the current parser state and the various arguments, create the
        required number of :class:`HidField` objects.

        :returns: a list of :class:`HidField` objects
        """

        # Note: usage_page is a 32-bit value here and usage
        # is usage_page | usage

        usage = usage_min
        if len(usages) > 0:
            usage = usages[0]

        item = cls(report_ID,
                   logical,
                   physical,
                   application,
                   collection,
                   value,
                   usage_page,
                   usage,
                   logical_min,
                   logical_max,
                   item_size,
                   1)
        items = []

        if value & (0x1 << 0):  # Const item
            item.size *= count
            return [item]
        elif value & (0x1 << 1):  # Variable item
            if usage_min and usage_max:
                usage = usage_min
                for i in range(count):
                    item = item.copy()
                    item.usage = usage
                    items.append(item)
                    if usage < usage_max:
                        usage += 1
            else:
                for i in range(count):
                    if i < len(usages):
                        usage = usages[i]
                    else:
                        usage = usages[-1]
                    item = item.copy()
                    item.usage = usage
                    items.append(item)
        else:  # Array item
            if usage_min and usage_max:
                usages = list(range(usage_min, usage_max + 1))
            item.usages = usages
            item.count = count
            return [item]
        return items


class HidReport(object):
    """
    Represents a HidReport, one of ``Input``, ``Output``, ``Feature``. A
    :class:`ReportDescriptor` may contain one or more
    HidReports of different types. These comprise of a number of
    :class:`HidField` making up the exact description of a
    report.

    :param int report_ID: the report ID
    :param int application: the HID application

    .. attribute:: fields

        The HidFields comprising this report

    """
    def __init__(self, report_ID, application):
        self.fields = []
        self.report_ID = report_ID
        self.application = application
        self._application_name = None
        self._bitsize = 0
        if self.numbered:
            self._bitsize = 8

    def append(self, field):
        """
        Add a :class:`HidField` to this report

        :param HidField field: the object to add to this report
        """
        self.fields.append(field)
        field.start = self._bitsize
        self._bitsize += field.size

    def extend(self, fields):
        """
        Extend this report by the list of :class:`HidField`
        objects

        :param list fields: a list of objects to append to this report
        """
        self.fields.extend(fields)
        for f in fields:
            f.start = self._bitsize
            self._bitsize += f.size * f.count

    @property
    def application_name(self):
        if self.application is None:
            return 'Vendor'

        try:
            page_id = self.application >> 16
            value = self.application & 0xff
            return HUT[page_id][value]
        except KeyError:
            return 'Vendor'

    @property
    def numbered(self):
        """
        True if the HidReport was initialized with a report ID
        """
        return self.report_ID >= 0

    @property
    def bitsize(self):
        """
        The size of the HidReport in bits
        """
        return self._bitsize

    @property
    def size(self):
        """
        The size of the HidReport in bytes
        """
        return self._bitsize >> 3

    def __iter__(self):
        return iter(self.fields)

    def _fix_xy_usage_for_mt_devices(self, usage):
        if usage not in self.prev_seen_usages:
            return usage

        # multitouch devices might have 2 X for CX, TX
        if usage == 'X' and ('Y' not in self.prev_seen_usages or
                             'CY' in self.prev_seen_usages):
            usage = 'CX'

        # multitouch devices might have 2 Y for CY, TY
        if usage == 'Y' and ('X' not in self.prev_seen_usages or
                             'CX' in self.prev_seen_usages):
            usage = 'CY'

        return usage

    def _format_one_event(self, data, global_data, hidInputItem, r_out):
        """
        Fill in the report array ``r_out`` with the data for this input
        item. ``r_out`` is modified in place with the values from ``data``
        or ``global_data`` (as fallback) that apply to this input item.

        :param object data: an object with attributes matching the HID usage names
            in this report. Where an attribute is undefined, the
            `global_data` is used instead.
        :param object global_data: an object with attributes matching the
            HID usage names, used whenever the `data` doesn't have an
            attribute defined.
        :param HidField hidInputItem: the input item of this report to set
        :param list r_out: the integer array of values for this report,
            modified in-place.
        """
        if hidInputItem.is_const:
            return

        usage = hidInputItem.usage_name

        usage = self._fix_xy_usage_for_mt_devices(usage)

        if (self.prev_collection is not None and
           self.prev_collection != hidInputItem.collection and
           usage in self.prev_seen_usages):
            if len(data) > 0:
                data.pop(0)
            self.prev_seen_usages.clear()

        value = 0
        # Match the HID usage with our attributes, so
        # Contact Count -> contactcount, etc.
        field = usage.replace(' ', '').lower()

        if len(data) > 0 and hasattr(data[0], field):
            value = getattr(data[0], field)
        elif global_data is not None and hasattr(global_data, field):
            value = getattr(global_data, field)

        try:
            value[0]
        except TypeError:
            value = [value]

        hidInputItem.fill_values(r_out, value)
        self.prev_collection = hidInputItem.collection
        self.prev_seen_usages.append(usage)

    def create_report(self, data, global_data):
        """
        Convert the data object to an array of ints representing this report.
        Each property of the given data object is matched against the field
        usage name (using ``hasattr``) and filled in accordingly.::

            mouse = MouseData()
            mouse.b1 = int(l)
            mouse.b2 = int(r)
            mouse.b3 = int(m)
            mouse.x = x
            mouse.y = y

            data_bytes = hid_report.create_report(mouse)

        The HidReport will create the report according to the device's
        report descriptor.
        """
        self.prev_seen_usages = []
        self.prev_collection = None
        r = [0] * self.size

        if self.numbered:
            r[0] = self.report_ID

        for item in self:
            self._format_one_event(data, global_data, item, r)

        if len(data) > 0:
            # remove the last item we just processed
            data.pop(0)

        return r

    def format_report(self, data, split_lines=True):
        """
        Format the HID Report provided as a list of 8-bit integers into a
        human-readable format.

        :param list data: a list of 8-bit integers that are this report
        :param boolean split_lines: ``True`` if the format can be split
            across multiple lines. This makes for easier reading but harder
            automated processing.
        """

        output = ''

        self.prev_seen_usages = []
        self.prev_collection = None
        sep = ''
        if self.numbered:
            assert self.report_ID == data[0]
            output += f'ReportID: {self.report_ID} '
            sep = '/'
        prev = None
        for report_item in self:
            if report_item.is_const:
                output += f'{sep} # '
                continue

            # get the value and consumes bits
            values = report_item.get_values(data)

            if not report_item.is_array:
                value_format = "{:d}"
                if report_item.size > 1:
                    value_format = f'{{:{str(len(str(1 << report_item.size)) + 1)}d}}'
                if isinstance(values[0], str):
                    value_format = "{}"
                if report_item.usage_page_name == 'Button':
                    if report_item.usage_name == 'B1':
                        usage_name = 'Button'
                        usage = f' {usage_name}:'
                    else:
                        usage_name = ''
                        sep = ''
                        usage = ''
                else:
                    usage_name = self._fix_xy_usage_for_mt_devices(report_item.usage_name)
                    usage = f' {usage_name}:'

                # if we don't get a key error this is a duplicate in
                # this report descriptor and we need a linebreak
                if (split_lines and
                   self.prev_collection is not None and
                   self.prev_collection != report_item.collection):
                    self.prev_seen_usages = []
                    output += '\n'
                self.prev_collection = report_item.collection
                self.prev_seen_usages.append(usage_name)

                # do not reapeat the usage name if several are in a row
                if (prev and
                   prev.type == report_item.type and
                   prev.usage == report_item.usage):
                    sep = ","
                    usage = ""
                output += f'{sep}{usage} {value_format.format(values[0])} '
            else:
                usage_page_name = report_item.usage_page_name
                if not usage_page_name:
                    usage_page_name = "Array"
                usages = []
                for v in values:
                    if (v < report_item.logical_min or
                       v > report_item.logical_max):
                        usages.append('')
                    else:
                        usage = ""
                        if isinstance(values[0], str):
                            usage = v
                        else:
                            usage = f'{v:02x}'
                        if ('vendor' not in usage_page_name.lower() and
                           v > 0 and
                           v < len(report_item.usages)):
                            usage = report_item.get_usage_name(v)
                            if "no event indicated" in usage.lower():
                                usage = ''
                        usages.append(f'\'{usage}\'')
                output += f'{sep}{usage_page_name} [{", ".join(usages)}] '
            sep = '|'
            prev = report_item
        return output


class ReportDescriptor(object):
    """
    Represents a fully parsed HID report descriptor.

    When creating a ``ReportDescriptor`` object,

    - if your source is a stream of bytes, use
      :meth:`from_bytes`
    - if your source is a human-readable descriptor, use
      :meth:`from_human_descr`

    .. attribute:: win8

        ``True`` if the device is Windows8 compatible, ``False`` otherwise

    .. attribute:: input_reports

        All :class:`HidReport` of type ``Input``, addressable by the report ID

    .. attribute:: output_reports

        All :class:`HidReport` of type ``Output``, addressable by the report ID

    .. attribute:: feature_reports

        All :class:`HidReport` of type ``Feature``, addressable by the report ID
    """
    class _Globals(object):
        """
        HID report descriptors uses a stack-based model where some values
        are pushed to the global state and apply to all subsequent items
        until changed or reset.
        """
        def __init__(self, other=None):
            self.usage_page = 0
            self.logical = None
            self.physical = None
            self.application = None
            self.logical_min = 0
            self.logical_max = 0
            self.count = 0
            self.item_size = 0
            if other is not None:
                self.usage_page = other.usage_page
                self.logical = other.logical
                self.physical = other.physical
                self.application = other.application
                self.logical_min = other.logical_min
                self.logical_max = other.logical_max
                self.count = other.count
                self.item_size = other.item_size

    class _Locals(object):
        """
        HID report descriptors uses a stack-based model where values
        apply until the next Output/InputReport/FeatureReport item.
        """
        def __init__(self):
            self.usages = []
            self.usage_sizes = []
            self.usage_min = 0
            self.usage_min_size = 0
            self.usage_max = 0
            self.usage_max_size = 0
            self.report_ID = -1

    def __init__(self, items):
        self.input_reports = {}
        self.feature_reports = {}
        self.output_reports = {}
        self.win8 = False
        self.rdesc_items = items

        # variables only used during parsing
        self.global_stack = []
        self.collection = [0, 0, 0]  # application, physical, logical
        self.local = ReportDescriptor._Locals()
        self.glob = ReportDescriptor._Globals()
        self.current_report = {}
        self.current_item = None

        index_in_report = 0
        for item in items:
            item.index_in_report = index_in_report
            index_in_report += item.size
            self._parse_item(item)

        # Drop the parsing-only variables so we don't leak them later
        del self.current_item
        del self.glob
        del self.global_stack
        del self.local
        del self.current_report
        del self.collection

    def get(self, reportID, reportSize):
        """
        Return the input report with the given Report ID or ``None``
        """
        try:
            report = self.input_reports[reportID]
        except KeyError:
            try:
                report = self.input_reports[-1]
            except KeyError:
                return None

        # if the report is larger than it should, it's OK
        if report.size >= reportSize:
            return report

        return None

    def get_report_from_application(self, application):
        """
        Return the Input report that matches the application or ``None``
        """
        for r in self.input_reports.values():
            if r.application == application or r.application_name == application:
                return r
        return None

    def _get_current_report(self, type):
        report_lists = {
            'Input': self.input_reports,
            'Output': self.output_reports,
            'Feature': self.feature_reports,
        }

        try:
            cur = self.current_report[type]
        except KeyError:
            cur = None

        if cur is not None and cur.local.report_ID != self.local.report_ID:
            cur = None

        if cur is None:
            try:
                cur = report_lists[type][self.local.report_ID]
            except KeyError:
                cur = HidReport(self.local.report_ID, self.glob.application)
                report_lists[type][self.local.report_ID] = cur
        return cur

    def _concatenate_usages(self):
        if self.local.usage_max and self.local.usage_max_size <= 2:
            if self.local.usage_max & 0xFFFF0000 != self.glob.usage_page:
                self.local.usage_max &= 0xFFFF
                self.local.usage_max |= self.glob.usage_page
                self.local.usage_min &= 0xFFFF
                self.local.usage_min |= self.glob.usage_page

        for i, v in reversed(list(enumerate(self.local.usages))):
            if self.local.usage_sizes[i] > 2:
                continue
            if v & 0xFFFF0000 == self.glob.usage_page:
                break
            self.local.usages[i] = v & 0xFFFF | self.glob.usage_page

    def _parse_item(self, rdesc_item):
        # store current usage_page in rdesc_item
        rdesc_item.usage_page = self.glob.usage_page
        item = rdesc_item.item
        value = rdesc_item.value
        size = rdesc_item.size - 1

        if item == "Report ID":
            self.local.report_ID = value
        elif item == "Push":
            self.global_stack.append(self.glob)
            self.glob = ReportDescriptor._Globals(self.glob)
        elif item == "Pop":
            self.glob = self.global_stack.pop()
        elif item == "Usage Page":
            self.glob.usage_page = value << 16
        elif item == "Collection":
            self._concatenate_usages()

            try:
                c = INV_COLLECTIONS[value]
            except KeyError:
                if value in range(0x07, 0x7F):
                    c = 'RESERVED'
                elif value in range(0x80, 0xFF):
                    c = 'VENDOR_DEFINED'
                else:  # not supposed to happen
                    raise

            try:
                if c == 'PHYSICAL':
                    self.collection[1] += 1
                    self.glob.physical = self.local.usages[-1]
                elif c == 'APPLICATION':
                    self.collection[0] += 1
                    self.glob.application = self.local.usages[-1]
                elif c == 'LOGICAL':
                    self.collection[2] += 1
                    self.glob.logical = self.local.usages[-1]
            except IndexError:
                pass
            # reset the usage list
            self.local.usages = []
            self.local.usage_sizes = []
            self.local.usage_min = 0
            self.local.usage_min_size = 0
            self.local.usage_max = 0
            self.local.usage_max_size = 0
        elif item == "Usage Minimum":
            if size <= 2:
                self.local.usage_min = value | self.glob.usage_page
            else:
                self.local.usage_min = value
            self.local.usage_min_size = size
        elif item == "Usage Maximum":
            if size <= 2:
                self.local.usage_max = value | self.glob.usage_page
            else:
                self.local.usage_max = value
            self.local.usage_max_size = size
        elif item == "Logical Minimum":
            self.glob.logical_min = value
        elif item == "Logical Maximum":
            self.glob.logical_max = value
        elif item == "Usage":
            if size <= 2:
                self.local.usages.append(value | self.glob.usage_page)
            else:
                self.local.usages.append(value)
            self.local.usage_sizes.append(size)
        elif item == "Report Count":
            self.glob.count = value
        elif item == "Report Size":
            self.glob.item_size = value
        elif item in ("Input", "Feature", "Output"):
            self.current_input_report = self._get_current_report(item)

            self._concatenate_usages()

            inputItems = HidField.getHidFields(self.local.report_ID,
                                               self.glob.logical,
                                               self.glob.physical,
                                               self.glob.application,
                                               tuple(self.collection),
                                               value,
                                               self.glob.usage_page,
                                               self.local.usages,
                                               self.local.usage_min,
                                               self.local.usage_max,
                                               self.glob.logical_min,
                                               self.glob.logical_max,
                                               self.glob.item_size,
                                               self.glob.count)
            self.current_input_report.extend(inputItems)
            if item == "Feature" and len(self.local.usages) > 0 and \
                    self.local.usages[-1] == 0xff0000c5:
                self.win8 = True
            self.local.usages = []
            self.local.usage_sizes = []
            self.local.usage_min = 0
            self.local.usage_min_size = 0
            self.local.usage_max = 0
            self.local.usage_max_size = 0

    def dump(self, dump_file=sys.stdout, output_type='default'):
        """
        Write this ReportDescriptor into the given file

        The "default" format prints each item as hexadecimal format with a
        double-slash comment, e.g. ::

           0x05, 0x01,                    // Usage Page (Generic Desktop)        0
           0x09, 0x02,                    // Usage (Mouse)                       2


        The "kernel" format prints each item in valid C format, for easy
        copy-paste into a kernel or C source file: ::

               0x05, 0x01,         /* Usage Page (Generic Desktop)         */
               0x09, 0x02,         /* Usage (Mouse)                        */

        :param File dump_file: the file to write to
        :param str output_type: the output format, one of "default" or "kernel"
        """
        assert output_type in ["default", "kernel"]

        indent = 0
        for rdesc_item in self.rdesc_items:
            if output_type == "default":
                indent = rdesc_item.dump_rdesc_array(indent, dump_file)
            elif output_type == "kernel":
                indent = rdesc_item.dump_rdesc_kernel(indent, dump_file)

    @property
    def size(self):
        """
        Returns the size of the report descriptor in bytes.
        """
        return sum([item.size for item in self.rdesc_items])

    @property
    def bytes(self):
        """
        This report descriptor as a list of 8-bit integers.
        """
        data = []
        for item in self.rdesc_items:
            data.extend(item.bytes)
        return data

    @classmethod
    def from_bytes(cls, rdesc):
        """
        Parse the given list of 8-bit integers.

        :param list rdesc: a list of bytes that are this report descriptor
        """
        items = _HidRDescItem.from_bytes(rdesc)

        return ReportDescriptor(items)

    @classmethod
    def from_string(cls, rdesc):
        """
        Parse a string in the format of series of hex numbers::

           12 34 ab cd ...

        and the first number in that series is the count of bytes, excluding
        that first number. This is the format returned by your
        ``/dev/hidraw`` event node, so just pass it along.


        :param list rdesc: a string that represents the list of bytes
        """

        rdesc = [int(r, 16) for r in rdesc.split()[1:]]
        items = _HidRDescItem.from_bytes(rdesc)

        return ReportDescriptor(items)

    @classmethod
    def from_human_descr(cls, rdesc_str):
        """
        Parse the given human-readable report descriptor, e.g. ::

            Usage Page (Digitizers)
            Usage (Finger)
            Collection (Logical)
             Report Size (1)
             Report Count (1)
             Logical Minimum (0)
             Logical Maximum (1)
             Usage (Tip Switch)
             Input (Data,Var,Abs)
             Report Size (7)
             Logical Maximum (127)
             Input (Cnst,Var,Abs)
             Report Size (8)
             Logical Maximum (255)
             Usage (Contact Id)

        """
        usage_page = 0
        items = []
        for line in rdesc_str.splitlines():
            if line.strip() == '':
                continue
            item = _HidRDescItem.from_human_descr(line, usage_page)
            usage_page = item.usage_page >> 16
            items.append(item)

        return ReportDescriptor(items)

    def create_report(self, data, global_data=None, reportID=None, application=None):
        """
        Convert the data object to an array of ints representing the report.
        Each property of the given data object is matched against the field
        usage name (think ``hasattr``) and filled in accordingly. ::

            rdesc = ReportDescriptor.from_bytes([10, ab, 20, cd, ...])

            mouse = MouseData()
            mouse.b1 = int(l)
            mouse.b2 = int(r)
            mouse.b3 = int(m)
            mouse.x = x
            mouse.y = y

            data_bytes = rdesc.create_report(mouse)

        The UHIDDevice will create the report according to the device's
        report descriptor.
        """
        # make sure the data is iterable
        try:
            iter(data)
        except TypeError:
            data = [data]

        rdesc = None

        if application is not None:
            rdesc = self.get_report_from_application(application)
        else:
            if reportID is None:
                reportID = -1
            rdesc = self.input_reports[reportID]

        return rdesc.create_report(data, global_data)

    def format_report(self, data, split_lines=True):
        """
        Format the HID Report provided as a list of 8-bit integers into a
        human-readable format.

        :param list data: a list of 8-bit integers that are this report
        :param boolean split_lines: ``True`` if the format can be split
            across multiple lines. This makes for easier reading but harder
            automated processing.
        """
        report = self.get(data[0], len(data))
        if report is None:
            return None

        return report.format_report(data, split_lines)
