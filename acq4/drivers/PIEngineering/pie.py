# import os, sys
# sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
# import acq4.util.clibrary as clib

import os, sys, struct, ctypes, time, threading
import numpy as np
os.environ['PATH'] += ";C:\\Program Files (x86)\\PI Engineering\\P.I. Engineering SDK\\DLLs"
pielib = ctypes.windll.PIEHid

# The following structure is defined with 10 fields in PieHid32.h, but it appears that only
# 4 of them are actually used by the library (???)
class TEnumHIDInfo(ctypes.Structure):
    _fields_ = [
        ("PID", ctypes.c_uint32),
        ("Usage", ctypes.c_uint32),
        ("UP", ctypes.c_uint32),
        # ("readSize", ctypes.c_long),
        # ("writeSize", ctypes.c_long),
        # ("DevicePath", ctypes.c_char*256),
        ("Handle", ctypes.c_uint32),
        # ("Version", ctypes.c_uint32),
        # ("ManufacturerString", ctypes.c_char*128),
        # ("ProductString", ctypes.c_char*128),
    ]


# Documentation is outdated; see https://github.com/piengineering/xkeys/blob/master/piehid/PieHid32.h
# Used this code to reformat error strings:
# for line in strings.split('\n'):
#   if line == '' or line[0] != '#':
#     continue
#   import re
#   words = line.split(' ')
#   n = words[2]
#   rest = ' '.join(words[3:])[2:-2].strip()
#   if rest == '':
#     rest = words[1]
#   print '    %s: "%s",' % (n, rest)
errorStrings = {
    101: "Bad HID device information set handle",
    102: "No devices found in device information set",
    103: "Other enumeration error",
    104: "Error getting device interface detail (symbolic link name) *",
    105: "Error getting device interface detail (symbolic link name)",
    106: "Unable to open a handle.",
    107: "PIE_HID_ENUMERATE_GET_ATTRIBUTES_ERROR",
    108: "PIE_HID_ENUMERATE_VENDOR_ID_ERROR",
    109: "PIE_HID_ENUMERATE_GET_PREPARSED_DATA_ERROR",
    110: "PIE_HID_ENUMERATE_GET_CAPS",
    111: "PIE_HID_ENUMERATE_GET_MANUFACTURER_STRING",
    112: "PIE_HID_ENUMERATE_GET_PRODUCT_STRING",
    201: "Bad interface handle",
    202: "Cannot allocate memory for ring buffer",
    203: "Cannot create mutex",
    204: "Cannot create read thread",
    205: "Cannot open read handle",
    206: "Cannot open read handle - Access Denied",
    207: "Cannot open read handle - bad DevicePath",
    208: "Cannot open write handle",
    209: "Cannot open write handle - Access Denied",
    210: "Cannot open write handle - bad DevicePath",
    301: "Bad interface handle",
    302: "Read length is zero",
    303: "Could not acquire data mutex",
    304: "Insufficient data (< readSize bytes)",
    305: "Could not release data mutex",
    306: "Could not release data mutex",
    307: "Handle Invalid or Device_Not_Found (probably device unplugged)",
    308: "PIE_HID_READ_DEVICE_DISCONNECTED",
    309: "PIE_HID_READ_READ_ERROR",
    310: "PIE_HID_READ_BYTES_NOT_EQUAL_READSIZE",
    311: "PIE_HID_READ_BLOCKING_READ_DATA_TIMED_OUT",
    401: "Bad interface handle",
    402: "Write length is zero",
    403: "Write failed",
    404: "Write incomplete",
    405: "unable to acquire write mutex",
    406: "unable to release write mutex",
    407: "Handle Invalid or Device_Not_Found (probably device unplugged) (previous buffered write)",
    408: "Buffer full",
    409: "Previous buffered write failed.",
    410: "Previous buffered write sent wrong number of bytes",
    411: "timer failed",
    412: "previous buffered write count not release mutex",
    413: "write buffer is full",
    414: "cannot write queue a fast write while slow writes are still pending",
    501: "Bad interface handle",
    502: "Read length is zero",
    503: "Could not acquire data mutex",
    504: "Insufficient data (< readSize bytes)",
    505: "Could not release data mutex",
    506: "Could not release data mutex",
    507: "Handle Invalid or Device_Not_Found (probably device unplugged)",
    601: "Bad interface handle",
    602: "Could not release data mutex",
    603: "Could not acquire data mutex",
    701: "Bad interface handle",
    702: "PIE_HID_DATACALLBACK_INVALID_INTERFACE",
    703: "PIE_HID_DATACALLBACK_CANNOT_CREATE_CALLBACK_THREAD",
    704: "PIE_HID_DATACALLBACK_CALLBACK_ALREADY_SET",
    801: "Bad interface handle",
    802: "PIE_HID_ERRORCALLBACK_INVALID_INTERFACE",
    803: "PIE_HID_ERRORCALLBACK_CANNOT_CREATE_ERROR_THREAD",
    1804: "PIE_HID_ERRORCALLBACK_ERROR_THREAD_ALREADY_CREATED",
}


# Maps product IDs to an internally used device key. Found in the PIE SDK help file.
# We use this key to look up the device name, capabilities, etc.
devicePIDs = {
    1062: 'XK12JS',
    1064: 'XK12JS',
    1227: 'XKE128',
    1228: 'XKE128',
    1229: 'XKE128',
    1230: 'XKE128',
}


# Maps device key to device name
deviceNames = {
    'XK12JS': 'XK-12 Jog & Shuttle',
    'XKE128': 'XKE-128',
}


# Define capabilities for each supported device
capabilityKeys = ['rows', 'columns', 'joysticks', 'jog/shuttle', 'touchpad']
deviceCapabilities = {
    'XK12JS': (3, 4, 0, True, False),
    'XKE128': (8, 16, 0, False, False),
}


# Function signatures for callbacks
if sys.platform == 'win32':
    dataCallbackType = ctypes.WINFUNCTYPE(ctypes.c_uint32, ctypes.POINTER(ctypes.c_char), ctypes.c_uint32, ctypes.c_uint32)
    errorCallbackType = ctypes.WINFUNCTYPE(ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32)
else:
    dataCallbackType = ctypes.CFUNCTYPE(ctypes.c_uint32, ctypes.POINTER(ctypes.c_char), ctypes.c_uint32, ctypes.c_uint32)
    errorCallbackType = ctypes.CFUNCTYPE(ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32)


class PIEException(Exception):
    def __init__(self, errno, *args):
        Exception.__init__(self, *args)
        self.errno = errno


def callPieFunc(func, *args):
    """Call a PIE function and raise PIEException if the return value is other than 0.
    """
    err = getattr(pielib, func)(*args)
    if err != 0:
        errstr = errorStrings.get(err, 'Unknown error')
        raise PIEException(err, "PIE error %d: %s" % (err, errstr))


def getDeviceHandles():
    """Return a list of handles for all PIE devices.

    This call reassigns the device handles in the PIE library; should only be called once.
    """
    info = (TEnumHIDInfo*128)()
    count = ctypes.c_long(0)
    callPieFunc('EnumeratePIE', 0x5f3, info, ctypes.byref(count))
    handles = []
    for i in range(count.value):
        if info[i].UP == 0xc:
            handles.append(info[i].Handle)
    return handles


class PIEDevice(object):
    def __init__(self, handle):
        self.handle = handle
        self.readsize = pielib.GetReadLength(handle)
        self.writesize = pielib.GetWriteLength(handle)
        callPieFunc('SetupInterfaceEx', handle, 1)

        # request descriptor packet
        self._clearEvents()
        self._send(0, 214, 0)
        self.desc = self._unpackDescData(self._readOne())

        # Handle-device-specific setup
        self.pid = self.desc['PID']
        try:
            pidkey = devicePIDs[self.pid]
        except KeyError:
            raise Exception("Unsupported PIE device %d. (currently supported models are: %s)" % (self.pid, list(deviceNames.values())))
        self.model = deviceNames[pidkey]
        self.capabilities = dict(zip(capabilityKeys, deviceCapabilities[pidkey]))

        # select device-specific methods
        self._unpackEventData = getattr(self, '_unpackEventData_'+pidkey)

        self.state = None
        self.stateLock = threading.Lock()
        self.getState(refresh=True)
        # self.keyState = np.zeros((self.capabilities['rows'], self.capabilities['columns']), dtype=bool)
        # self.joyState = [None] * self.capabilities['joysticks']
        # self.jogState = None
        # self.touchState = None

        # Start monitor thread
        self._callback = None
        self._ctypes_cb = dataCallbackType(self._dataCallback)
        callPieFunc('SetDataCallback', self.handle, 2, self._ctypes_cb)

    def getState(self, refresh=False):
        """Return the current state of the device.

        If *refresh* is True, then request a new state packet from the device.
        Otherwise, just return the last known state (this is the default).
        """
        if refresh:
            self._clearEvents()
            self._send(0, 177)
            self._handleData(self._readOne()[1:])
        with self.stateLock:
            return self.state.copy()

    def setCallback(self, cb):
        self._callback = cb

    def setLED(self, state):
        """Set status LEDs.

        State may be 0=off, 1=green, 2=red, 3=green+red.
        """
        self._send(0, 186, state << 6)

    def setBacklight(self, row, col, bl1=None, bl2=None):
        """Set backlight status of a specific key.

        *bl1* and *bl2* may be 0=off, 1=on, 2=flash.
        *row* and *col* may be None to change all backlights in a column or row.
        """
        if row is None:
            for row in range(self.capabilities['rows']):
                self.setBacklight(row, col, bl1, bl2)
            return
        if col is None:
            for col in range(self.capabilities['columns']):
                self.setBacklight(row, col, bl1, bl2)
            return

        row = int(row)
        col = int(col)
        if 0 > row >= self.capabilities['rows']:
            raise IndexError("Row %d is invalid for this device." % row)
        if 0 > col >= self.capabilities['columns']:
            raise IndexError("Column %d is invalid for this device." % col)
        if bl1 not in (None, 0, 1, 2) or bl2 not in (None, 0, 1, 2):
            raise ValueError("Backlight state must be 0, 1, or 2.")

        index = row + col * 8
        if bl1 is not None:
            self._send(0, 181, index, bl1)
        if bl2 is not None:
            self._send(0, 181, index+32, bl2)

    def setBacklightRows(self, bl1=None, bl2=None):
        if bl1 is not None:
            self._send(0, 182, 0, bl1)
        if bl2 is not None:
            self._send(0, 182, 1, bl2)

    def close(self):
        """Close the device and stop its event handling thread.
        """
        pielib.CleanupInterface(self.handle)

    def _clearEvents(self):
        """Purge all pending events from the buffer.
        """
        callPieFunc('ClearBuffer', self.handle)

    def _send(self, *args):
        """Send a request to the library for this device.
        """
        writebuf = (ctypes.c_char * self.writesize)()
        pad = self.writesize-len(args)
        writebuf[:] = struct.pack('=%dB%ds' % (len(args), pad), *(args + ('\0'*pad,)))
        while True:
            try:
                callPieFunc('WriteData', self.handle, ctypes.byref(writebuf))
                return
            except PIEException as exc:
                if exc.errno == 413:
                    # write buffer is full; try again
                    continue
                raise

    def _read(self):
        """Return next data packet in the buffer, or None if the buffer is empty.
        """
        readbuf = (ctypes.c_char * self.readsize)()
        try:
            callPieFunc('ReadData', self.handle, ctypes.byref(readbuf))
        except PIEException as exc:
            if exc.errno == 304:
                return None
            raise
        return readbuf

    def _readOne(self):
        """Return the next packet in the buffer, blocking until a packet becomes available.
        """
        while True:
            data = self._read()
            if data is not None:
                return data
            time.sleep(0.01)

    def _unpackDescData(self, data):
        # Do we need to handle this differently for each device type?
        # The format seems to be consistent across most devices.
        fields = struct.unpack('=12BH', data[:14])[1:]
        fieldnames = ['UnitID', 'DataType', 'Mode', 'Keymapstart', 'Layer2Offset', 'WriteReportLength-1', 'ReadReportLength-1', 'MaxColumns', 'MaxRows', 'LEDState', 'Version', 'PID']
        desc = dict(zip(fieldnames, fields))
        return desc

    def _unpackEventData_XK12JS(self, data):
        (uid, btn, b1, b2, b3, b4, jog, shuttle, timestamp) = struct.unpack('=6B2bI', data[:12])
        mask = np.empty((3, 4), dtype='ubyte')
        mask[:] = np.array([[1], [2], [4]])
        keys = (mask & np.array([[b1, b2, b3, b4]])).astype(bool)
        return {'keys': keys, 'jog': jog, 'shuttle': shuttle, 'button': bool(btn)}

    def _handleData(self, data):
        """Update the known device state from *data* and return a summary of state
        changes.
        """
        newState = self._unpackEventData(data)
        changes = {}
        with self.stateLock:
            if self.state is not None:
                state = self.state.copy()
            else:
                state = None
            self.state = newState
        if state is None:
            return

        for k in newState:
            if k == 'keys':
                dif = np.argwhere(newState['keys'] != state['keys'])
                if len(dif) > 0:
                    changes['keys'] = dif
            else:
                if newState[k] != state[k]:
                    changes[k] = newState[k]
        return changes

    def _dataCallback(self, data, devid, err):
        # hexdump(data, self.readsize)
        changes = self._handleData(data)
        if self._callback is not None:
            self._callback(changes)
        return 1


def hexdump(data, size):
    i = 0
    while i < size:
        line = '%03x:  ' % i
        for j in range(16):
            if i >= size:
                break
            line += '%02x ' % ord(data[i])
            i += 1
        print line

# hexdump(buf, 540*count.value)

# handle = 0
# assert pielib.SetupInterfaceEx(handle, 1) == 0

# import time, struct
# readsize = pielib.GetReadLength(handle)
# readbuf = (ctypes.c_char * readsize)()

# writesize = pielib.GetWriteLength(handle)
# writebuf = (ctypes.c_char * writesize)()
# def send(*args):
#     pad = writesize-len(args)
#     writebuf[:] = struct.pack('=%dB%ds' % (len(args), pad), *(args + ('\0'*pad,)))
#     return pielib.WriteData(handle, ctypes.byref(writebuf))

# ````

# led = 0
# while True:
#     if pielib.ReadData(handle, ctypes.byref(readbuf)) == 0:
#         # hexdump(buf, readsize)
#         (_, uid, btn, keys, jog, shuttle, timestamp) = struct.unpack('=3BI2bI', readbuf[:13])
#         print "uid: %02x  btn: %02x  keys: %08x  jog: %02x  shuttle: %02x" % (uid, btn, keys, jog, shuttle)

#         # toggle red/green LEDs
#         send(0, 186, led << 6)
#         led = (led + 1) % 4



#     time.sleep(0.01)



if __name__ == '__main__':
    h = getDeviceHandles()
    print "handles:", h
    dev = PIEDevice(h[0])
    print dev.pid, dev.model
    dev.setBacklightRows(bl1=21, bl2=42)

    def cb(changes):
        print changes
        # s = dev.getState()
        # for i in range(s['keys'].shape[0]):
        #     print s['keys'][i].astype('ubyte')
        # print 'jog: %02x  shuttle: %02x  btn: %02x' % (s['jog'], s['shuttle'], s['button'])
    dev.setCallback(cb)
