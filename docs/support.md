# Support tools

Two small read-only command-line tools ship in the image. Both use only the
Python 3 standard library, make no network calls, and upload nothing.

## rowyn-report

Builds a bug report, scrubs it on your machine, and prints a prefilled GitHub
issue link.

    rowyn-report              # write a scrubbed report file and print the link
    rowyn-report --stdout     # print the scrubbed report instead of writing a file
    rowyn-report --no-sudo    # skip the image lookup (no admin needed)
    rowyn-report --open       # also open the link in a browser, if one is installed

The file goes to `$XDG_STATE_HOME/rowyn/` when that is set, otherwise to your
home folder, as `rowyn-report-YYYYMMDD-HHMMSS.md` with mode 0600. The issue
link carries only a short summary (OS, image, kernel, GPU, session, failed unit
count). Read the file before you attach it to an issue. If no web browser is
installed, install one from Discover or paste the link into another device.

Collected: OS release, kernel, image reference and digest (admin users only,
through the bootc guard), kernel command line, GPU lines and loaded GPU
modules, session type and desktop, failed systemd units, the last 120 error
lines of this boot's journal, Flatpak and fwupd versions and Flatpak remotes,
memory use, and disk use of `/` and `/var`. Every step has a timeout and a
failure is noted in the report, not fatal.

Redacted before anything is written: your user name and host name; home
folders (yours become `~`, others `/home/<user>`); IPv4 and IPv6 addresses
(loopback and unspecified kept); MAC addresses; Wi-Fi SSIDs and PSKs; UUIDs
including `root=UUID=`; serial numbers; email addresses; bearer and
authorization tokens and `token`/`password`/`passwd`/`secret`/`apikey` values;
credentials inside URLs; and long hex strings (40+ characters are shortened to
12 characters, so the image digest stays recognizable; 32 to 39 characters
such as a machine id become `<hex>`) and long mixed-case key-like blobs.
Scrubbing is pattern based and is a safety net, not a guarantee, so read the
file. Rare false positives are expected (for example a four-part version
number that looks like an IPv4 address).

## rowyn-controller-check

Reports whether game controllers are ready: controller-like input devices,
known gamepad USB IDs (DualSense, DualShock 4, Switch Pro, Joy-Con, Xbox,
Steam, 8BitDo), the matching kernel drivers, `/dev/uinput` access, the udev
rule for controller access, and whether `joycond` is installed. Each warning
comes with a plain-language fix hint.

    rowyn-controller-check
    rowyn-controller-check --json

It changes nothing and always exits 0.
