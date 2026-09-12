"""Cross-platform HID backend for the TRN Black Pearl.

The application code only relies on a small slice of the pywinusb API:

    HidDeviceFilter(vendor_id=..., product_id=...).get_devices()
    device.is_plugged() / device.is_opened() / device.open() / device.close()
    device.set_raw_data_handler(callback)
    device.find_output_reports()[0].send([...])
    device.serial_number

This module provides that API on every platform:

* Windows: pywinusb, exactly as before.
* Linux: the kernel's native hidraw interface (stdlib only, no libusb).
* macOS: the `hidapi` package.

Report format is identical everywhere: buffers are 64 bytes and start with
the report ID (0x4B); the raw data handler receives the full 64-byte input
report with the report ID at index 0.
"""
import os
import select
import sys
import threading

REPORT_SIZE = 64  # full report including the report ID byte

if sys.platform == "win32":
    from pywinusb.hid import HidDeviceFilter
else:
    import hid as _hidapi

    def _pad(data):
        buf = bytearray(data[:REPORT_SIZE])
        buf.extend(b"\x00" * (REPORT_SIZE - len(buf)))
        return bytes(buf)

    class _OutputReport:
        """Mimics pywinusb's HidReport (only what the app uses: send())."""

        def __init__(self, device):
            self._device = device

        def send(self, data):
            return self._device._send(data)

    class _BaseHidDevice:
        """Shared wrapper: report padding, reader thread, lifecycle."""

        def __init__(self):
            self.serial_number = ""
            self.path = None
            self._opened = False
            self._handler = None
            self._reader = None

        # --- lifecycle -------------------------------------------------
        def open(self):
            if self._opened:
                return True
            try:
                self._transport_open()
            except Exception:
                self._transport_close()
                raise
            self._opened = True
            self._start_reader()
            return True

        def close(self):
            self._opened = False
            reader = self._reader
            if reader is not None and reader.is_alive():
                reader.join(timeout=0.5)
            self._reader = None
            self._transport_close()

        def is_opened(self):
            return self._opened

        # --- I/O -------------------------------------------------------
        def set_raw_data_handler(self, handler):
            self._handler = handler
            if self._opened and (self._reader is None or not self._reader.is_alive()):
                self._start_reader()

        def find_output_reports(self):
            return [_OutputReport(self)] if self._opened else []

        def _send(self, data):
            if not self._opened:
                return False
            try:
                return self._transport_write(_pad(data)) >= 0
            except Exception:
                return False

        def _dispatch(self, data):
            if data and self._handler:
                data = list(data)
                if len(data) < REPORT_SIZE:
                    data += [0x00] * (REPORT_SIZE - len(data))
                try:
                    self._handler(data)
                except Exception:
                    pass

        def _start_reader(self):
            def pump():
                while self._opened:
                    try:
                        data = self._transport_read(0.1)
                    except Exception:
                        if self._opened:
                            # Device unplugged or closed underneath us
                            self._opened = False
                        break
                    if data:
                        self._dispatch(data)

            self._reader = threading.Thread(target=pump, daemon=True)
            self._reader.start()

        # --- transport hooks -------------------------------------------
        def _transport_open(self):  # pragma: no cover - overridden
            raise NotImplementedError

        def _transport_close(self):  # pragma: no cover - overridden
            pass

        def _transport_write(self, buf):  # pragma: no cover - overridden
            raise NotImplementedError

        def _transport_read(self, timeout_s):  # pragma: no cover - overridden
            raise NotImplementedError

    # ------------------------------------------------------------------
    if sys.platform.startswith("linux"):

        def _hidraw_sysfs(node_name):
            """Parse the uevent of a hidraw device -> dict or None."""
            try:
                with open(f"/sys/class/hidraw/{node_name}/device/uevent") as f:
                    info = {}
                    for line in f:
                        if "=" in line:
                            k, v = line.strip().split("=", 1)
                            info[k] = v
                    return info
            except OSError:
                return None

        def _vid_pid(info):
            # HID_ID=0003:00003302:000043E8 -> (0x3302, 0x43E8)
            try:
                _, vid, pid = info["HID_ID"].split(":")
                return int(vid, 16), int(pid, 16)
            except (KeyError, ValueError):
                return None

        class HidDevice(_BaseHidDevice):
            """Native Linux hidraw device (write = OUT report, read = IN report)."""

            def __init__(self, node):
                super().__init__()
                self.node = node  # e.g. "hidraw1"
                self.path = f"/dev/{node}"
                info = _hidraw_sysfs(node) or {}
                self.serial_number = info.get("HID_UNIQ", "")
                self.vendor_name = ""
                self.product_name = ""
                self._read_usb_strings()
                self._fd = None

            def _read_usb_strings(self):
                """Pull manufacturer/product from the ancestor USB device in sysfs."""
                try:
                    d = os.path.realpath(f"/sys/class/hidraw/{self.node}/device")
                    for _ in range(4):  # HID device -> interface -> USB device
                        if os.path.exists(os.path.join(d, "idVendor")):
                            break
                        d = os.path.dirname(d)
                    for attr, key in (("manufacturer", "vendor_name"), ("product", "product_name")):
                        with open(os.path.join(d, attr)) as f:
                            setattr(self, key, f.read().strip())
                except OSError:
                    pass

            def is_plugged(self):
                info = _hidraw_sysfs(self.node)
                return bool(info) and _vid_pid(info) is not None

            def _transport_open(self):
                self._fd = os.open(self.path, os.O_RDWR)

            def _transport_close(self):
                fd, self._fd = self._fd, None
                if fd is not None:
                    try:
                        os.close(fd)
                    except OSError:
                        pass

            def _transport_write(self, buf):
                return os.write(self._fd, buf)

            def _transport_read(self, timeout_s):
                poll = select.poll()
                poll.register(self._fd, select.POLLIN)
                try:
                    if not poll.poll(timeout_s * 1000):
                        return None
                    return os.read(self._fd, REPORT_SIZE)
                finally:
                    poll.unregister(self._fd)

        class HidDeviceFilter:
            def __init__(self, vendor_id=None, product_id=None):
                self.vendor_id = vendor_id
                self.product_id = product_id

            def get_devices(self):
                devices = []
                try:
                    nodes = sorted(os.listdir("/sys/class/hidraw"))
                except OSError:
                    return []
                for node in nodes:
                    info = _hidraw_sysfs(node)
                    if not info:
                        continue
                    ids = _vid_pid(info)
                    if ids is None:
                        continue
                    if self.vendor_id is not None and ids[0] != self.vendor_id:
                        continue
                    if self.product_id is not None and ids[1] != self.product_id:
                        continue
                    devices.append(HidDevice(node))
                return devices

    # ------------------------------------------------------------------
    else:

        def _norm_path(path):
            """hidapi paths are bytes on some platforms, str on others."""
            return path.decode(errors="replace") if isinstance(path, bytes) else path

        class HidDevice(_BaseHidDevice):
            """hidapi-backed device (macOS and any other non-Windows platform)."""

            def __init__(self, info):
                super().__init__()
                self._info = dict(info)
                self.path = _norm_path(info["path"])
                self.serial_number = info.get("serial_number") or ""
                self.vendor_name = info.get("manufacturer_string") or ""
                self.product_name = info.get("product_string") or ""
                self._hid = None

            def is_plugged(self):
                try:
                    infos = _hidapi.enumerate(self._info["vendor_id"], self._info["product_id"])
                except Exception:
                    return False
                return any(_norm_path(i["path"]) == self.path for i in infos)

            def _transport_open(self):
                dev = _hidapi.device()
                dev.open_path(self._info["path"])
                self._hid = dev
                if not self.serial_number:
                    try:
                        self.serial_number = dev.get_serial_number_string() or ""
                    except Exception:
                        pass
                if not self.vendor_name:
                    try:
                        self.vendor_name = dev.get_manufacturer_string() or ""
                        self.product_name = dev.get_product_string() or ""
                    except Exception:
                        pass

            def _transport_close(self):
                dev, self._hid = self._hid, None
                if dev is not None:
                    try:
                        dev.close()
                    except Exception:
                        pass

            def _transport_write(self, buf):
                return self._hid.write(buf)

            def _transport_read(self, timeout_s):
                return self._hid.read(REPORT_SIZE, int(timeout_s * 1000)) or None

        class HidDeviceFilter:
            def __init__(self, vendor_id=None, product_id=None):
                self.vendor_id = vendor_id
                self.product_id = product_id

            def get_devices(self):
                try:
                    infos = _hidapi.enumerate(self.vendor_id, self.product_id)
                except Exception:
                    return []
                return [HidDevice(info) for info in infos]
