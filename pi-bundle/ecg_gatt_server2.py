#!/usr/bin/env python3

"""
ecg_gatt_server.py
------------------

Raspberry Pi BLE ECG GATT server for EEBM 7V87.

Loads a cached MIT-BIH ECG signal that has already been resampled to 250 Hz
by prepare_ecg_data.py, then exposes it through a custom BLE GATT notify
characteristic.

BLE packet format:
    5 ECG samples per notification
    each sample = IEEE-754 float32, little-endian
    packet size = 5 * 4 = 20 bytes

Timing:
    5 samples / 250 samples/s = 0.020 s
    therefore one BLE notification every 20 ms

Advertising is intentionally handled separately with btmgmt because the
BlueZ D-Bus advertising path on this Raspberry Pi/BlueZ setup has already
been observed to fail while the management interface works.
"""

import os
import struct
import sys
import time

import dbus
import dbus.service
import dbus.mainloop.glib
import numpy as np

from gi.repository import GLib


# ---------------------------------------------------------------------------
# BlueZ / D-Bus constants
# ---------------------------------------------------------------------------

BLUEZ_SERVICE_NAME = "org.bluez"
DBUS_OM_IFACE = "org.freedesktop.DBus.ObjectManager"
DBUS_PROP_IFACE = "org.freedesktop.DBus.Properties"

GATT_MANAGER_IFACE = "org.bluez.GattManager1"
GATT_SERVICE_IFACE = "org.bluez.GattService1"
GATT_CHRC_IFACE = "org.bluez.GattCharacteristic1"


# ---------------------------------------------------------------------------
# Custom ECG BLE UUIDs
# ---------------------------------------------------------------------------

ECG_SERVICE_UUID = "7b7a0001-6b7a-4d5a-9c01-250000000001"
ECG_DATA_UUID = "7b7a0002-6b7a-4d5a-9c01-250000000002"


# ---------------------------------------------------------------------------
# ECG streaming configuration
# ---------------------------------------------------------------------------

TARGET_FS = 250
SAMPLES_PER_PACKET = 5
BYTES_PER_SAMPLE = 4                       # float32
PACKET_SIZE = SAMPLES_PER_PACKET * BYTES_PER_SAMPLE
PACKET_INTERVAL_MS = int(
    1000 * SAMPLES_PER_PACKET / TARGET_FS
)                                         # 20 ms

DEFAULT_RECORD = "100"
CACHE_DIR = "ecg_cache"

# Loop back to the beginning when the end of the MIT-BIH record is reached.
LOOP_ECG = True

# Do not print every 20-ms packet; print a status line once per second.
STATUS_EVERY_PACKETS = TARGET_FS // SAMPLES_PER_PACKET   # 50 packets


# ---------------------------------------------------------------------------
# D-Bus exceptions
# ---------------------------------------------------------------------------

class InvalidArgsException(dbus.exceptions.DBusException):
    _dbus_error_name = "org.freedesktop.DBus.Error.InvalidArgs"


# ---------------------------------------------------------------------------
# GATT application
# ---------------------------------------------------------------------------

class Application(dbus.service.Object):

    def __init__(self, bus, ecg_signal, record_name):
        self.path = "/com/eebm7v87/ecg"
        self.services = []

        dbus.service.Object.__init__(self, bus, self.path)

        self.add_service(
            ECGService(
                bus,
                0,
                ecg_signal,
                record_name
            )
        )

    def get_path(self):
        return dbus.ObjectPath(self.path)

    def add_service(self, service):
        self.services.append(service)

    @dbus.service.method(
        DBUS_OM_IFACE,
        out_signature="a{oa{sa{sv}}}"
    )
    def GetManagedObjects(self):

        response = {}

        for service in self.services:
            response[service.get_path()] = service.get_properties()

            for characteristic in service.get_characteristics():
                response[characteristic.get_path()] = (
                    characteristic.get_properties()
                )

        return response


# ---------------------------------------------------------------------------
# Base GATT service
# ---------------------------------------------------------------------------

class Service(dbus.service.Object):

    PATH_BASE = "/com/eebm7v87/ecg/service"

    def __init__(self, bus, index, uuid, primary):

        self.path = self.PATH_BASE + str(index)
        self.bus = bus
        self.uuid = uuid
        self.primary = primary
        self.characteristics = []

        dbus.service.Object.__init__(self, bus, self.path)

    def get_properties(self):

        return {
            GATT_SERVICE_IFACE: {
                "UUID": self.uuid,
                "Primary": dbus.Boolean(self.primary),
                "Characteristics": dbus.Array(
                    self.get_characteristic_paths(),
                    signature="o"
                )
            }
        }

    def get_path(self):
        return dbus.ObjectPath(self.path)

    def add_characteristic(self, characteristic):
        self.characteristics.append(characteristic)

    def get_characteristic_paths(self):
        return [
            characteristic.get_path()
            for characteristic in self.characteristics
        ]

    def get_characteristics(self):
        return self.characteristics

    @dbus.service.method(
        DBUS_PROP_IFACE,
        in_signature="s",
        out_signature="a{sv}"
    )
    def GetAll(self, interface):

        if interface != GATT_SERVICE_IFACE:
            raise InvalidArgsException()

        return self.get_properties()[GATT_SERVICE_IFACE]


# ---------------------------------------------------------------------------
# Base GATT characteristic
# ---------------------------------------------------------------------------

class Characteristic(dbus.service.Object):

    def __init__(self, bus, index, uuid, flags, service):

        self.path = service.path + "/char" + str(index)

        self.bus = bus
        self.uuid = uuid
        self.service = service
        self.flags = flags

        dbus.service.Object.__init__(self, bus, self.path)

    def get_properties(self):

        return {
            GATT_CHRC_IFACE: {
                "Service": self.service.get_path(),
                "UUID": self.uuid,
                "Flags": dbus.Array(self.flags, signature="s")
            }
        }

    def get_path(self):
        return dbus.ObjectPath(self.path)

    @dbus.service.method(
        DBUS_PROP_IFACE,
        in_signature="s",
        out_signature="a{sv}"
    )
    def GetAll(self, interface):

        if interface != GATT_CHRC_IFACE:
            raise InvalidArgsException()

        return self.get_properties()[GATT_CHRC_IFACE]


# ---------------------------------------------------------------------------
# ECG service
# ---------------------------------------------------------------------------

class ECGService(Service):

    def __init__(self, bus, index, ecg_signal, record_name):

        Service.__init__(
            self,
            bus,
            index,
            ECG_SERVICE_UUID,
            True
        )

        self.add_characteristic(
            ECGDataCharacteristic(
                bus,
                0,
                self,
                ecg_signal,
                record_name
            )
        )


# ---------------------------------------------------------------------------
# ECG data characteristic
# ---------------------------------------------------------------------------

class ECGDataCharacteristic(Characteristic):

    def __init__(
        self,
        bus,
        index,
        service,
        ecg_signal,
        record_name
    ):

        Characteristic.__init__(
            self,
            bus,
            index,
            ECG_DATA_UUID,
            ["notify"],
            service
        )

        self.ecg_signal = ecg_signal
        self.record_name = record_name

        self.sample_index = 0
        self.packet_count = 0
        self.total_samples_sent = 0

        self.notifying = False
        self.timer_id = None
        self.stream_start_time = None

    @dbus.service.method(
        GATT_CHRC_IFACE,
        in_signature="",
        out_signature=""
    )
    def StartNotify(self):
        """Called by BlueZ after a connected client enables notifications."""

        if self.notifying:
            print("[BLE] Notifications already enabled")
            return

        print("[BLE] Notifications ENABLED")
        print(f"[ECG] Starting record {self.record_name} at sample 0")

        self.notifying = True
        self.sample_index = 0
        self.packet_count = 0
        self.total_samples_sent = 0
        self.stream_start_time = time.monotonic()

        # Send the first packet immediately.
        self.send_ecg_packet()

        # Then continue at one packet every 20 ms.
        self.timer_id = GLib.timeout_add(
            PACKET_INTERVAL_MS,
            self.send_ecg_packet
        )

    @dbus.service.method(
        GATT_CHRC_IFACE,
        in_signature="",
        out_signature=""
    )
    def StopNotify(self):
        """Called by BlueZ when the client disables notifications."""

        if not self.notifying:
            return

        print("[BLE] Notifications DISABLED")

        self.notifying = False

        if self.timer_id is not None:
            GLib.source_remove(self.timer_id)
            self.timer_id = None

        self.print_final_statistics()

    def send_ecg_packet(self):
        """Create and emit the next 20-byte ECG notification."""

        if not self.notifying:
            return False

        # Handle the end of the ECG record.
        if self.sample_index >= len(self.ecg_signal):

            if LOOP_ECG:
                print("[ECG] End of record reached -- looping to beginning")
                self.sample_index = 0

            else:
                print("[ECG] End of record reached -- stopping stream")
                self.notifying = False
                self.timer_id = None
                self.print_final_statistics()
                return False

        end_index = self.sample_index + SAMPLES_PER_PACKET
        samples = self.ecg_signal[self.sample_index:end_index]

        # If fewer than 5 samples remain at the end, loop and fill the packet.
        if len(samples) < SAMPLES_PER_PACKET:

            if LOOP_ECG:
                needed = SAMPLES_PER_PACKET - len(samples)

                samples = np.concatenate(
                    (
                        samples,
                        self.ecg_signal[:needed]
                    )
                )

                self.sample_index = needed

            else:
                # For a non-looping stream we skip an incomplete final packet.
                self.notifying = False
                self.timer_id = None
                self.print_final_statistics()
                return False

        else:
            self.sample_index = end_index

        # Force exactly five float32 values.
        samples = np.asarray(samples, dtype=np.float32)

        # BLE payload:
        #     5 little-endian IEEE-754 float32 values
        #     5 * 4 bytes = 20 bytes
        payload = struct.pack(
            "<5f",
            *samples
        )

        # Convert Python bytes into the D-Bus byte array expected by BlueZ.
        dbus_value = dbus.Array(
            [dbus.Byte(byte) for byte in payload],
            signature="y"
        )

        # Updating Value through PropertiesChanged causes BlueZ to generate
        # a GATT notification for subscribed clients.
        self.PropertiesChanged(
            GATT_CHRC_IFACE,
            {
                "Value": dbus_value
            },
            []
        )

        self.packet_count += 1
        self.total_samples_sent += SAMPLES_PER_PACKET

        # Print only once per second to avoid disturbing 20-ms timing.
        if self.packet_count % STATUS_EVERY_PACKETS == 0:
            self.print_status(samples)

        return True

    def print_status(self, samples):

        elapsed = time.monotonic() - self.stream_start_time

        if elapsed > 0:
            effective_rate = self.total_samples_sent / elapsed
        else:
            effective_rate = 0.0

        print(
            f"[TX] packets={self.packet_count:6d}  "
            f"samples={self.total_samples_sent:7d}  "
            f"index={self.sample_index:7d}/{len(self.ecg_signal)}  "
            f"rate={effective_rate:7.2f} samples/s  "
            f"latest={samples[-1]: .5f}"
        )

    def print_final_statistics(self):

        if self.stream_start_time is None:
            return

        elapsed = time.monotonic() - self.stream_start_time

        if elapsed > 0:
            effective_rate = self.total_samples_sent / elapsed
        else:
            effective_rate = 0.0

        print(
            f"[ECG] Stream stopped: "
            f"{self.total_samples_sent} samples in {elapsed:.2f} s "
            f"({effective_rate:.2f} samples/s)"
        )

    @dbus.service.signal(
        DBUS_PROP_IFACE,
        signature="sa{sv}as"
    )
    def PropertiesChanged(
        self,
        interface,
        changed,
        invalidated
    ):
        pass


# ---------------------------------------------------------------------------
# BlueZ adapter discovery
# ---------------------------------------------------------------------------

def find_adapter(bus):

    remote_om = dbus.Interface(
        bus.get_object(
            BLUEZ_SERVICE_NAME,
            "/"
        ),
        DBUS_OM_IFACE
    )

    objects = remote_om.GetManagedObjects()

    for path, interfaces in objects.items():

        if GATT_MANAGER_IFACE in interfaces:
            return path

    return None


# ---------------------------------------------------------------------------
# ECG file loading
# ---------------------------------------------------------------------------

def load_ecg(record_name):

    filename = f"{record_name}_250hz.npy"
    path = os.path.join(CACHE_DIR, filename)

    if not os.path.isfile(path):
        print(f"[ERROR] ECG file not found: {path}")
        print("")
        print("Run prepare_ecg_data.py first, or choose a cached record.")
        sys.exit(1)

    print(f"[ECG] Loading {path} ...")

    ecg_signal = np.load(path)

    if ecg_signal.ndim != 1:
        print(
            f"[ERROR] Expected a 1-D ECG array, "
            f"but file shape is {ecg_signal.shape}"
        )
        sys.exit(1)

    if len(ecg_signal) < SAMPLES_PER_PACKET:
        print("[ERROR] ECG file contains too few samples")
        sys.exit(1)

    if not np.all(np.isfinite(ecg_signal)):
        print("[ERROR] ECG file contains NaN or infinite values")
        sys.exit(1)

    ecg_signal = np.asarray(
        ecg_signal,
        dtype=np.float32
    )

    duration_seconds = len(ecg_signal) / TARGET_FS

    print(f"[ECG] Samples       : {len(ecg_signal)}")
    print(f"[ECG] Sampling rate : {TARGET_FS} Hz")
    print(f"[ECG] Duration      : {duration_seconds:.1f} seconds")
    print(
        f"[ECG] Range         : "
        f"{float(np.min(ecg_signal)):.5f} to "
        f"{float(np.max(ecg_signal)):.5f}"
    )

    return ecg_signal


# ---------------------------------------------------------------------------
# Registration callbacks
# ---------------------------------------------------------------------------

def register_app_cb():

    print("[BLE] GATT application registered successfully")
    print("[BLE] ECG service is ready")
    print("")
    print("[BLE] Start the connectable advertisement in Terminal 2:")
    print(
        "sudo btmgmt add-adv "
        f"-u {ECG_SERVICE_UUID} "
        "-d 020106 -n -c 1"
    )
    print("")
    print("[BLE] Then connect with a BLE client and enable notifications.")


def register_app_error_cb(error):

    print("[ERROR] Failed to register GATT application")
    print(error)

    mainloop.quit()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():

    global mainloop

    # Optional command line record selection:
    #
    #     python3 ecg_gatt_server.py
    #     python3 ecg_gatt_server.py 100
    #     python3 ecg_gatt_server.py 105

    if len(sys.argv) > 2:
        print(
            f"Usage: {os.path.basename(sys.argv[0])} [record]"
        )
        sys.exit(1)

    if len(sys.argv) == 2:
        record_name = sys.argv[1]
    else:
        record_name = DEFAULT_RECORD

    ecg_signal = load_ecg(record_name)

    dbus.mainloop.glib.DBusGMainLoop(
        set_as_default=True
    )

    bus = dbus.SystemBus()

    adapter_path = find_adapter(bus)

    if adapter_path is None:
        print(
            "[ERROR] No Bluetooth adapter with "
            "GATT support found"
        )
        sys.exit(1)

    print("")
    print("=" * 68)
    print("EEBM 7V87 - Raspberry Pi BLE ECG Streamer")
    print("=" * 68)
    print(f"Adapter path       : {adapter_path}")
    print(f"Record             : {record_name}")
    print(f"Service UUID       : {ECG_SERVICE_UUID}")
    print(f"Data UUID          : {ECG_DATA_UUID}")
    print(f"ECG sampling rate  : {TARGET_FS} Hz")
    print(f"Samples / packet   : {SAMPLES_PER_PACKET}")
    print(f"BLE payload        : {PACKET_SIZE} bytes")
    print(f"Packet interval    : {PACKET_INTERVAL_MS} ms")
    print(
        f"Notifications/sec  : "
        f"{TARGET_FS / SAMPLES_PER_PACKET:.0f}"
    )
    print("=" * 68)

    service_manager = dbus.Interface(
        bus.get_object(
            BLUEZ_SERVICE_NAME,
            adapter_path
        ),
        GATT_MANAGER_IFACE
    )

    app = Application(
        bus,
        ecg_signal,
        record_name
    )

    mainloop = GLib.MainLoop()

    print("[BLE] Registering GATT application...")

    service_manager.RegisterApplication(
        app.get_path(),
        {},
        reply_handler=register_app_cb,
        error_handler=register_app_error_cb
    )

    try:
        mainloop.run()

    except KeyboardInterrupt:
        print("\n[BLE] Shutting down")


if __name__ == "__main__":
    main()
