#!/usr/bin/env bash
# Install wifi-gate on a Debian host. Does not touch the Wi-Fi itself:
# see create-ap.sh for the access point profile.
set -euo pipefail
fail() { echo "Error: $*" >&2; exit 1; }
[[ $(id -u) == 0 ]] || fail 'run as root'
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
for TOOL in nft python3 systemctl; do
  PATH=$PATH:/usr/sbin command -v "$TOOL" >/dev/null || fail "missing $TOOL"
done
install -d -m 0755 /opt/wifi-gate /etc/wifi-gate
install -m 0755 "$HERE/src/wifi_gate.py" /opt/wifi-gate/wifi_gate.py
ln -sfn /opt/wifi-gate/wifi_gate.py /usr/local/sbin/wifi-gate
[[ -e /etc/wifi-gate/config.json ]] || install -m 0644 "$HERE/config.example.json" /etc/wifi-gate/config.json
install -m 0644 "$HERE/deploy/wifi-gate.service" /etc/systemd/system/wifi-gate.service
# NetworkManager's shared-mode dnsmasq reads this directory; it takes effect
# the next time the access point profile is brought up.
install -d -m 0755 /etc/NetworkManager/dnsmasq-shared.d
install -m 0644 "$HERE/deploy/dnsmasq-shared.conf" /etc/NetworkManager/dnsmasq-shared.d/wifi-gate-dns.conf
/usr/local/sbin/wifi-gate check
systemctl daemon-reload
systemctl enable wifi-gate.service
echo 'Installed. Start with: systemctl start wifi-gate'
