#!/usr/bin/python3
"""Install the persistent NetworkManager policy for the Ubuntu live ISO."""
from pathlib import Path
import argparse


NETWORK_MANAGER_CONF = """[keyfile]
unmanaged-devices=except:type:ethernet,except:type:wifi,except:type:gsm,except:type:cdma

[ifupdown]
managed=true
"""

NETPLAN_CONFIG = """network:
  version: 2
  renderer: NetworkManager
  ethernets:
    rowyn-wired:
      match:
        name: "en*"
      dhcp4: true
      dhcp6: true
      optional: true
"""


def configure(root: Path) -> tuple[Path, Path]:
    """Write the live network policy below *root* and return both paths."""
    nm_path = root / "etc/NetworkManager/conf.d/10-globally-managed-devices.conf"
    netplan_path = root / "etc/netplan/99-rowyn-live-network.yaml"
    nm_path.parent.mkdir(parents=True, exist_ok=True)
    netplan_path.parent.mkdir(parents=True, exist_ok=True)
    nm_path.write_text(NETWORK_MANAGER_CONF)
    netplan_path.write_text(NETPLAN_CONFIG)
    netplan_path.chmod(0o600)
    return nm_path, netplan_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("/"))
    args = parser.parse_args()
    configure(args.root)


if __name__ == "__main__":
    main()
