# Boot and launch timelines

`boot_timeline.py` prints one boot's timeline from a Stela boot log, to time
boots and app launches. It only reads the log and needs no root. Copy
`EFI/stela/boot.log` off the stick's EFI partition (or use the text that
`stela log` saves) and run:

```sh
python3 tools/boot_timeline.py boot.log            # the latest boot
python3 tools/boot_timeline.py boot.log --boot 2   # an earlier one, if kept
python3 tools/boot_timeline.py boot.log --app "System Settings"
python3 tools/boot_timeline.py boot.log --json
```

It reports Stela's own steps up to macOS starting, APFS flushes that took
long (`tx_flush ... took N us`), the first time WindowServer and Finder/Dock
open the GPU (the StelaGPU kext's reports), and input. The boot writes
`INPUT ACTIVE` at the first input after two idle seconds, without logging
which keys or motions. Each such line starts a run of input, and the next
line's `idle_ms` gives when that run ended. So the tool reports the first
input (the login screen was up by then, plus the person's own delay) and the
last input before the desktop (the password's Return), and times login to
desktop from the latter. loginwindow first opens the GPU only after the
password, so it marks no login screen. Each app is timed from the input run
before its first GPU use, which is a range: the click lay somewhere in that
run. Apps are timed only within 30 s of the input unless named with `--app`.
First GPU use marks when an app first drew, not when it was fully usable.

The log is a 4 MiB ring, which the tool reads across its wrap, and the kext
logs 32 GPU-open reports per boot. Collect the log soon after a test and
launch the measured apps early in the session.

Tests: `python3 -m unittest discover -s tools/tests`.
