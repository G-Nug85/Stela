import importlib.util
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location("boot_timeline", Path(__file__).parents[1] / "boot_timeline.py")
timeline = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(timeline)

# The ring as the stick keeps it: boot 4, the oldest, begins near the file's
# end and goes on at its start; boot 5 follows it; then the ring's newest data
# meets an overwritten older boot, whose stamps fall back.
BOOT_4_TAIL = [
    "[   56.220] GUEST| tx_flush:1280: disk3 tx xid 5086 took 13800796 us to sync and write superblock",
    "[   82.500] GUEST| StelaGPU: WindowServer: opened the accelerator (type 0)",
    "[  190.000] INPUT ACTIVE i8042_PS/2 idle_ms=first",
    # Typing went on with no 2 s gap, ending 4 s before the next run: 196.0.
    "[  200.000] INPUT ACTIVE I2C-HID_TPD0 idle_ms=4000",
    "[  201.000] GUEST| StelaGPU: loginwindow: opened the accelerator (type 0)",
    "[  205.000] GUEST| StelaGPU: Finder: opened the accelerator (type 0)",
    "[  206.000] GUEST| StelaGPU: com.apple.dock.extra: opened the accelerator (type 0)",
    "[  240.000] GUEST| StelaGPU: NotificationCenter: opened the accelerator (type 0)",
    # The run starting at 200 ended at 202; the one at 300 ended at 302.
    "[  300.000] INPUT ACTIVE I2C-HID_TPD0 idle_ms=98000",
    "[  304.250] GUEST| StelaGPU: System Settings: opened the accelerator (type 0)",
    "[  304.300] GUEST| StelaGPU: System Settings: opened the accelerator (type 0)",
    "[  340.000] GUEST| StelaGPU: Slow App: opened the accelerator (type 0)",
    "[  400.000] INPUT ACTIVE I2C-HID_TPD0 idle_ms=98000",
    "[  450.000] STEP o Starting macOS (432 s) ms=432300",
    "[  450.050] STELA POWER-OFF code=0x17",
]
BOOT_5 = [
    "=== STELA BOOT 5 ===",
    "[   17.700] STEP Starting macOS t=17.700",
    "[  600.000] STELA POWER-OFF code=0x17",
    "[   24.628] STEP o Reading macOS's boot files from its disk (130 MB) ms=615",
    "[  500.000] GUEST| StelaGPU: Finder: opened the accelerator (type 0)",
]
BOOT_4_HEAD = [
    "=== STELA BOOT 4 ===",
    "STELA UEFI MOUNT version=0.4.2",
    "[    5.000] STEP o Connecting the firmware's drivers to the boot's devices (2 s) ms=2177",
    "[   14.000] STEP o Starting the PC's disks and USB devices (1 partition, 3 s) ms=3931",
    "[   17.700] STEP Starting macOS t=17.700",
]
LOG = "\x00\x00STELALOG binary header\n" + "\n".join(BOOT_4_TAIL + BOOT_5 + BOOT_4_HEAD)


def boot_4(apps=()):
    return timeline.timeline(timeline.boots(LOG)[4], apps)


class Timeline(unittest.TestCase):
    def test_a_boot_wraps_from_the_ring_end_to_its_start(self):
        found = timeline.boots(LOG)
        self.assertEqual(sorted(found), [4, 5])
        self.assertEqual(found[4][0][1].split(" ms=")[0],
                         "STEP o Connecting the firmware's drivers to the boot's devices (2 s)")
        self.assertEqual(found[4][-1][1], "STELA POWER-OFF code=0x17")
        # Boot 5 ends at its power-off, not in the older data after it.
        self.assertEqual(found[5][-1][1], "STELA POWER-OFF code=0x17")
        self.assertEqual(boot_4()["macos_start"], 17.7)

    def test_bursts_end_where_the_next_idle_time_says(self):
        runs = boot_4()["bursts"]
        self.assertEqual(runs[0], {"start": 190.0, "end": 196.0})
        self.assertEqual(runs[1], {"start": 200.0, "end": 202.0})
        self.assertEqual(runs[2], {"start": 300.0, "end": 302.0})
        self.assertIsNone(runs[-1]["end"])

    def test_milestones_and_login_input(self):
        result = boot_4()
        self.assertEqual(result["windowserver"], 82.5)
        self.assertEqual(result["first_input"], 190.0)
        self.assertEqual(result["loginwindow_gpu"], 201.0)
        self.assertEqual(result["desktop"], 205.0)
        # The end of the last run before the desktop, not its start.
        self.assertEqual(result["login_input"], 202.0)
        self.assertIn("Login to desktop: 3.0 s", timeline.summary(4, result))

    def test_launch_range_spans_the_run_before_it(self):
        launches = {launch["process"]: launch["since_input"] for launch in boot_4()["launches"]}
        self.assertEqual(launches["System Settings"], [2.25, 4.25])
        # Background opens long after any input are not attributed to it.
        self.assertIsNone(launches["NotificationCenter"])
        self.assertNotIn("com.apple.dock.extra", launches)
        self.assertEqual([l["process"] for l in boot_4()["launches"]].count("System Settings"), 1)

    def test_named_app_is_timed_past_the_window(self):
        self.assertIsNone({l["process"]: l["since_input"] for l in boot_4()["launches"]}["Slow App"])
        named = {l["process"]: l["since_input"] for l in boot_4(("Slow App",))["launches"]}
        self.assertEqual(named["Slow App"], [38.0, 40.0])

    def test_slow_apfs_flush_is_reported(self):
        result = boot_4()
        self.assertEqual(result["slow_flushes"], [{"disk": "disk3", "time": 56.22, "seconds": 13.800796}])
        self.assertIn("APFS flush on disk3 took 13.8 s", timeline.summary(4, result))

    def test_old_build_without_activity_lines_says_so(self):
        lines = [line for line in timeline.boots(LOG)[4] if "INPUT ACTIVE" not in line[1]]
        result = timeline.timeline(lines)
        self.assertFalse(result["has_input_activity"])
        self.assertIsNone(result["login_input"])
        self.assertIn("no INPUT ACTIVE lines", timeline.summary(4, result))

    def test_dropped_reports_are_flagged(self):
        lines = timeline.boots(LOG)[4] + [
            (451.0, "GUEST| StelaGPU: 32 open reports logged; later ones are dropped")]
        result = timeline.timeline(lines)
        self.assertTrue(result["reports_dropped"])
        self.assertIn("after 32 reports", timeline.summary(4, result))


if __name__ == "__main__":
    unittest.main()
