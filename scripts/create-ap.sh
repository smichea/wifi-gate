#!/usr/bin/env bash
# Create the guest access point as a NetworkManager profile. NetworkManager
# runs DHCP, DNS and NAT for the guest subnet ("shared" mode); wifi-gate adds
# isolation and rate tiers on top.
#   create-ap.sh SSID [CHANNEL] < passphrase-on-stdin
set -euo pipefail
fail() { echo "Error: $*" >&2; exit 1; }
[[ $(id -u) == 0 ]] || fail 'run as root'
(($# >= 1)) || fail 'usage: create-ap.sh SSID [CHANNEL] < passphrase'
SSID=$1 CHANNEL=${2:-6}
IFS= read -r PSK || true
((${#PSK} >= 8 && ${#PSK} <= 63)) || fail 'passphrase must be 8 to 63 characters, given on standard input'
read -r IFACE GATEWAY < <(python3 - <<'PY'
import json
config = json.load(open('/etc/wifi-gate/config.json'))
network = config.get('subnet', '10.20.0.0/24')
base, prefix = network.split('/')
parts = base.split('.')
print(config.get('interface', 'wlan0'), '.'.join(parts[:3] + [str(int(parts[3]) + 1)]) + '/' + prefix)
PY
)
nmcli -t -f NAME con show | grep -qx wifi-gate-ap && fail 'profile wifi-gate-ap already exists; delete it first'
nmcli con add type wifi ifname "$IFACE" con-name wifi-gate-ap ssid "$SSID" \
  802-11-wireless.mode ap 802-11-wireless.band bg 802-11-wireless.channel "$CHANNEL" \
  802-11-wireless.ap-isolation yes \
  ipv4.method shared ipv4.addresses "$GATEWAY" ipv6.method disabled \
  connection.autoconnect yes connection.autoconnect-priority 100
# A guest passphrase is displayed in the venue anyway; it is read from standard
# input only to keep it out of shell history.
nmcli con mod wifi-gate-ap wifi-sec.key-mgmt wpa-psk wifi-sec.proto rsn wifi-sec.psk "$PSK"
echo "Profile wifi-gate-ap created on $IFACE ($GATEWAY). Activate with: nmcli con up wifi-gate-ap"
