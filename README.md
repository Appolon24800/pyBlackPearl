# pyBlackPearl
A Reversed engineered walkplay driver for the TRN Black Pearl

Cross-platform (Windows, Linux, macOS) control panel for the TRN Black Pearl
(TTGK Technology TE-C) USB DAC: parametric EQ, hardware volume/balance,
gain mode, amp topology, digital filters and mic gain.

## Windows

Run `setup.bat` once, then start the app with `run.bat`.
Build the EXE with `build.bat`.

## Linux

```bash
./setup.sh
./run.sh
```

The app talks to the DAC through the kernel's native `hidraw` interface,
so no extra HID library is required. Most distributions restrict
`/dev/hidraw*` to root; if the app shows a "Device Access Denied" bar,
install the bundled udev rule and reconnect the DAC:

```bash
sudo cp udev/99-trn-blackpearl.rules /etc/udev/rules.d/
sudo udevadm control --reload && sudo udevadm trigger
```

Build a native executable with `./build.sh`.

## macOS

Same as Linux: `./setup.sh` then `./run.sh` (uses `hidapi`, installed
automatically by `setup.sh`).

## Hardware notes

- USB HID device `3302:43E8`, 64-byte reports with report ID `0x4B`
- `query/` and `functions/` contain small protocol-research CLI tools
