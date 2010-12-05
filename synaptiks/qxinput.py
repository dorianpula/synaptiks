# -*- coding: utf-8 -*-
# Copyright (C) 2010 Sebastian Wiesner <lunaryorn@googlemail.com>
# All rights reserved.

# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:

# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in the
#    documentation and/or other materials provided with the distribution.

# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

"""
    synaptiks.qxinput
    =================

    Qt-based API to access XInput functionality

    .. moduleauthor::  Sebastian Wiesner  <lunaryorn@googlemail.com>
"""


import ctypes
import struct
from functools import wraps, partial
from collections import Mapping
from operator import eq

import sip
sip.setapi('QString', 2)
sip.setapi('QVariant', 2)
from PyQt4.QtGui import QX11Info

from synaptiks._bindings import xlib, xinput
from synaptiks._bindings.util import scoped_pointer
from synaptiks.util import assert_byte_string, assert_unicode_string


class QX11Display(object):
    """
    Wrapper around the Qt X11 Display (as returned by
    :meth:`PyQt4.QtGui.QX11Info.display()`) to make the display available as
    argument for ctypes-wrapped foreign functions from Xlib.

    If used as argument to a foreign function, this object is cast into a
    proper Display pointer.
    """

    def __init__(self):
        display = QX11Info.display()
        if not display:
            raise ValueError('A Qt X11 display connection is required. '
                             'Create a QApplication object')
        display_address = sip.unwrapinstance(display)
        self._as_parameter_ = ctypes.cast(display_address, xlib.Display_p)


class XInputVersionError(Exception):
    """
    Raised on unexpected XInput versions.
    """

    @property
    def expected_version(self):
        """
        The expected XInput version as ``(major, minor)`` tuple.  Both
        components are integers.
        """
        return self.args[0]

    @property
    def actual_version(self):
        """
        The actual XInput version as ``(major, minor)`` tuple.  Both
        components are integers.
        """
        return self.args[1]

    def __str__(self):
        return ('XI Version Error:  Expected {0.expected_version}, '
                'got {0.actual_version}')


def assert_xinput_version():
    """
    Check, that the XInput version on the server side is sufficiently
    recent.

    Currently, at least version 2.0 is required.

    Raise :exc:`XInputVersionError`, if the version isn't sufficient.
    """
    matched, actual_version = xinput.query_version(QX11Display(), (2,0))
    if not matched:
        raise QXInputVersionError((2, 0), actual_version)


def is_property_defined(name):
    """
    Check, if the given property is defined on the server side.

    ``name`` is the property name as byte or unicode string.  A unicode
    string is converted into a byte string according to the encoding of the
    current locale.

    Return ``True``, if the property is defined, ``False`` otherwise.
    """
    atom = xlib.intern_atom(QX11Display(), assert_byte_string(name), True)
    return atom != xlib.NONE


class UndefinedPropertyError(KeyError):
    """
    Raised if a property is undefined on the server side.  Subclass of
    :exc:`~exceptions.KeyError`.
    """

    @property
    def name(self):
        """
        The name of the undefined property
        """
        return self.args[0]


def _get_property_atom(name):
    """
    Get a :class:`~synaptiks._bindings.xlib.Atom` for the given property.

    ``name`` is the property name as byte or unicode string.  A unicode
    string is converted into a byte string according to the encoding of the
    current locale.

    Return the :class:`~synaptiks._bindings.xlib.Atom` for the given
    property.

    Raise :exc:`UndefinedPropertyError`, if there is no atom for the given
    property.
    """
    atom = xlib.intern_atom(QX11Display(), assert_byte_string(name), True)
    if atom == xlib.NONE:
        raise UndefinedPropertyError(name)
    return atom


class InputDeviceNotFoundError(Exception):
    """
    Raised if a device with a certain id does not exist anymore.
    """

    @property
    def id(self):
        """
        The id of the non-existing device.
        """
        return self.args[0]

    def __str__(self):
        return 'The device with id {0} does not exist'.format(self.id)


class PropertyTypeError(ValueError):
    """
    Raised if a property value has an unexpected type.  Subclass of
    :exc:`~exceptions.ValueError`.
    """

    @property
    def type(self):
        """
        The property type that caused this error
        """
        return self.args[0]

    def __str__(self):
        return 'Unexpected property type: {0}'.format(self.type)


#: maps property formats to :mod:`struct` format codes
_FORMAT_CODE_MAPPING = {8: 'B', 16: 'S', 32: 'L'}


class InputDevice(Mapping):
    """
    An input device register on the X11 server.

    This class subclasses the Mapping ABC, providing a read-only dictionary
    mapping device property names to the corresponding values. Therefore all
    well-known dicitionary methods and operators (e.g. .keys(), .items(),
    in) are available to access the properties of a input device.

    :class:`InputDevice` objects compare equal and unequal to other devices
    and to strings (based on :attr:`id`). However, there is no ordering on
    and the corresponding operators >, <, <= and >= raise TypeError.
    """

    @classmethod
    def all_devices(cls):
        """
        Iterate over all input devices.

        Return an iterator over :class:`InputDevice` objects.
        """
        number_of_devices, devices = xinput.query_device(
            QX11Display(), xinput.ALL_DEVICES)
        with scoped_pointer(devices, xinput.free_device_info) as devices:
            if not devices:
                raise EnvironmentError('Failed to query devices')
            for i in xrange(number_of_devices):
                yield cls(devices[i].deviceid)

    @classmethod
    def find_devices_by_name(cls, name):
        """
        Find all devices with the given ``name``.

        ``name`` is either a string, which has to match the device name
        literally, or a regular expression pattern, which is searched in the
        device name.

        Return an iterator over all :class:`InputDevice` objects with a
        matching name.
        """
        if isinstance(name, basestring):
            matches = partial(eq, name)
        else:
            matches = name.search
        return (d for d in cls.all_devices() if matches(d.name))

    @classmethod
    def find_devices_with_property(cls, name):
        """
        Find all devices which have the given property.

        ``name`` is a string with the property name.

        Return an iterator over all :class:`InputDevice` objects, which have
        this property defined.
        """
        return (d for d in cls.all_devices() if name in d)

    def __init__(self, deviceid):
        self.id = deviceid

    @property
    def name(self):
        """
        The name of this device as unicode string.
        """
        number_of_devices, device = xinput.query_device(
            QX11Display(), self.id)
        with scoped_pointer(device, xinput.free_device_info) as device:
            if not device:
                raise InputDeviceNotFoundError(self.id)
            return assert_unicode_string(device.contents.name)

    def __eq__(self, other):
        return self.id == other.id

    def __ne__(self, other):
        return self.id != other.id

    def __len__(self):
        """
        Return the amount of all properties defined on this device.
        """
        number_of_properties, property_atoms = xinput.list_properties(
            QX11Display(), self.id)
        with scoped_pointer(property_atoms, xlib.free):
            return number_of_properties

    def _iter_property_atoms(self):
        number_of_properties, property_atoms = xinput.list_properties(
            QX11Display(), self.id)
        with scoped_pointer(property_atoms, xlib.free):
            for i in xrange(number_of_properties):
                yield property_atoms[i]

    def __iter__(self):
        """
        Iterate over the names of all properties defined for this device.

        Return a generator yielding the names of all properties of this
        device as unicode strings
        """
        return (assert_unicode_string(xlib.get_atom_name(QX11Display(), a))
                for a in self._iter_property_atoms())

    def __contains__(self, name):
        """
        Check, if the given property is defined on this device.

        ``name`` is the property name as string.

        Return ``True``, if the property is defined on this device,
        ``False`` otherwise.
        """
        atom = xlib.intern_atom(
            QX11Display(), assert_byte_string(name), True)
        if atom == xlib.NONE:
            return False
        return any(a == atom for a in self._iter_property_atoms())

    def __getitem__(self, name):
        """
        Get the given property.

        Input device properties have multiple items and are of different
        types.  This method returns all items in a tuple, and tries to
        convert them into the appropriate Python type.  Consequently, the
        conversion may fail, if the property has an unsupported type.
        Currently, integer and float types are supported, any other type
        raises :exc:`PropertyTypeError`.

        ``name`` is the property name as string.

        Return all items of the given property as tuple, or raise
        :exc:`~exceptions.KeyError`, if the property is not defined on this
        device.  Raise :exc:`UndefinedPropertyError` (which is a subclass of
        :exc:`~exceptions.KeyError`), if the property is not defined on the
        server at all.  Raise :exc:`PropertyTypeError`, if the property has
        an unsupported type.
        """
        atom = _get_property_atom(name)
        type, format, bytes = xinput.get_property(QX11Display(),
                                                  self.id, atom)
        if type == xlib.NONE and format == 0:
            raise KeyError(name)
        number_of_items = (len(bytes) * 8) / format
        if type == xlib.INTEGER:
            struct_format = _FORMAT_CODE_MAPPING[format] * number_of_items
        elif type == xlib.intern_atom(QX11Display(), 'FLOAT', True):
            struct_format = 'f' * number_of_items
        else:
            raise PropertyTypeError(type)
        assert struct.calcsize(struct_format) == len(bytes)
        return struct.unpack(struct_format, bytes)

    def __gt__(self, other):
        raise TypeError('InputDevice not orderable')

    def __lt__(self, other):
        raise TypeError('InputDevice not orderable')

    def __le__(self, other):
        raise TypeError('InputDevice not orderable')

    def __ge__(self, other):
        raise TypeError('InputDevice not orderable')

    def _set_raw(self, property, type, format, data):
        atom = _get_property_atom(property)
        xinput.change_property(QX11Display(), self.id, atom,
                               type, format, data)

    def set_integer(self, property, *values):
        """
        Set an integral ``property``.

        ``property`` is the property name as string, ``values`` contains
        *all* values of the property as integer.

        Raise :exc:`UndefinedPropertyError`, if the given property is not
        defined on the server.
        """
        data = struct.pack('L' * len(values), *values)
        self._set_raw(property, xlib.INTEGER, 32, data)

    def set_byte(self, property, *values):
        """
        Set a ``property``, whose values are single bytes.

        ``property`` is the property name as string, ``values`` contains
        *all* values of the property as integer, which must of course all be
        in the range of byte values.

        Raise :exc:`UndefinedPropertyError`, if the given property is not
        defined on the server.
        """
        data = struct.pack('B' * len(values), *values)
        self._set_raw(property, xlib.INTEGER, 8, data)

    set_bool = set_byte

    def set_float(self, property, *values):
        """
        Set a floating point ``property``.

        ``property`` is the property name as string, ``values`` contains
        *all* values of the property as float objects, which must all be in
        the range of C float values.

        Raise :exc:`UndefinedPropertyError`, if the given property is not
        defined on the server
        """
        data = struct.pack('f' * len(values), *values)
        type = xlib.intern_atom(QX11Display(), 'FLOAT', True)
        self._set_raw(property, type, 32, data)