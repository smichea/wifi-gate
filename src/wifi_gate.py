#!/usr/bin/env python3
"""wifi-gate: guest Wi-Fi gatekeeper for a small venue.

Mechanism only: every guest device gets a slow default rate; a caller (the
venue's loyalty system) may grant a faster tier to one device for a bounded
time. No account, point or pricing logic lives here.

The gate is an nftables table. Tiers are sets of guest IPv4 addresses whose
elements carry a timeout, so a boost expires in the kernel without a daemon.
"""
import argparse
import ipaddress
import json
import re
import subprocess
import sys
import time

DEFAULT_CONFIG = '/etc/wifi-gate/config.json'
TABLE = 'wifi_gate'
NAME = re.compile(r'^[a-z][a-z0-9_]{0,23}$')
IFACE = re.compile(r'^[a-zA-Z0-9_.-]{1,15}$')
MAC = re.compile(r'^(?:[0-9a-f]{2}:){5}[0-9a-f]{2}$')
# Destinations a guest must never reach: every private or link-local range,
# which covers the venue's own LAN, its router admin page and this host's
# other networks, whatever addressing the uplink uses.
PRIVATE = ('10.0.0.0/8', '100.64.0.0/10', '169.254.0.0/16', '172.16.0.0/12', '192.168.0.0/16')


class ConfigError(ValueError):
    pass


def load_config(raw):
    """Validate a config mapping and return its normalized form."""
    iface = raw.get('interface', 'wlan0')
    if not isinstance(iface, str) or not IFACE.match(iface):
        raise ConfigError('interface must be a network interface name')
    try:
        subnet = ipaddress.ip_network(raw.get('subnet', '10.20.0.0/24'))
    except (ValueError, TypeError) as error:
        raise ConfigError(f'subnet: {error}') from None
    if subnet.version != 4 or not subnet.is_private or subnet.prefixlen > 29:
        raise ConfigError('subnet must be a private IPv4 network of at least 6 hosts')
    tiers = raw.get('tiers', {})
    if not isinstance(tiers, dict):
        raise ConfigError('tiers must map a tier name to kbit/s')
    rates = {'default': raw.get('default_kbit', 256), **tiers}
    for name, kbit in rates.items():
        if not NAME.match(name) or (name == 'default' and 'default' in tiers):
            raise ConfigError(f'invalid tier name: {name!r}')
        if isinstance(kbit, bool) or not isinstance(kbit, int) or not 16 <= kbit <= 1_000_000:
            raise ConfigError(f'tier {name}: rate must be an integer between 16 and 1000000 kbit/s')
    ports = raw.get('host_tcp_ports', [])
    if not isinstance(ports, list) or any(isinstance(p, bool) or not isinstance(p, int) or not 1 <= p <= 65535 for p in ports):
        raise ConfigError('host_tcp_ports must be a list of TCP ports')
    return {'interface': iface, 'subnet': subnet, 'default_kbit': rates.pop('default'),
            'tiers': rates, 'host_tcp_ports': sorted(set(ports)),
            'leases': raw.get('leases', f'/var/lib/NetworkManager/dnsmasq-{iface}.leases')}


def _limit(kbit):
    # Policing drops what exceeds the rate. Two seconds of burst lets a page
    # start promptly without raising the sustained rate.
    rate = kbit * 125
    return f'limit rate over {rate} bytes/second burst {rate * 2} bytes'


def render_ruleset(config):
    """Return the complete nftables ruleset for this configuration."""
    iface, subnet = config['interface'], config['subnet']
    tiers = sorted(config['tiers'].items(), key=lambda item: -item[1])
    out = [f'table inet {TABLE}', f'delete table inet {TABLE}', f'table inet {TABLE} {{']
    for name, _ in tiers:
        out.append(f'  set tier_{name} {{ type ipv4_addr; flags timeout; }}')
    for name in [name for name, _ in tiers] + ['default']:
        for direction in ('up', 'down'):
            out.append(f'  set meter_{direction}_{name} {{ type ipv4_addr; size 4096; flags dynamic,timeout; timeout 10m; }}')
    out += ['  chain forward {', '    type filter hook forward priority filter - 5; policy accept;',
            f'    iifname "{iface}" jump from_guest', f'    oifname "{iface}" jump to_guest', '  }']
    for chain, key, direction in (('from_guest', 'saddr', 'up'), ('to_guest', 'daddr', 'down')):
        out.append(f'  chain {chain} {{')
        if chain == 'from_guest':
            # Only the guest subnet may leave, IPv4 only, and never towards a
            # private network.
            out.append('    meta nfproto != ipv4 drop')
            out.append(f'    ip saddr != {subnet} drop')
            out.append(f'    ip daddr {{ {", ".join(PRIVATE)} }} drop')
        for name, kbit in tiers:
            out.append(f'    ip {key} @tier_{name} update @meter_{direction}_{name} {{ ip {key} {_limit(kbit)} }} drop')
            out.append(f'    ip {key} @tier_{name} accept')
        out.append(f'    update @meter_{direction}_default {{ ip {key} {_limit(config["default_kbit"])} }} drop')
        out.append('  }')
    out += ['  chain input {', '    type filter hook input priority filter - 5; policy accept;',
            f'    iifname != "{iface}" accept', '    ct state established,related accept',
            '    udp dport { 53, 67 } accept', '    tcp dport 53 accept']
    if config['host_tcp_ports']:
        out.append(f'    tcp dport {{ {", ".join(map(str, config["host_tcp_ports"]))} }} accept')
    out += ['    icmp type echo-request limit rate 5/second accept', '    drop', '  }', '}', '']
    return '\n'.join(out)


def read_leases(text, subnet):
    """Parse dnsmasq leases into guest devices, newest lease first."""
    devices = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 4 or not MAC.match(fields[1].lower()):
            continue
        try:
            address = ipaddress.ip_address(fields[2])
        except ValueError:
            continue
        if address in subnet:
            devices.append({'mac': fields[1].lower(), 'ip': str(address), 'name': '' if fields[3] == '*' else fields[3],
                            'lease_expires': int(fields[0]) if fields[0].isdigit() else 0})
    return sorted(devices, key=lambda device: -device['lease_expires'])


def resolve_device(target, devices, subnet):
    """Return the guest IPv4 address for an IP or MAC given by the caller."""
    target = target.strip().lower()
    if MAC.match(target):
        for device in devices:
            if device['mac'] == target:
                return device['ip']
        raise ConfigError('no current guest lease for this MAC address')
    try:
        address = ipaddress.ip_address(target)
    except ValueError:
        raise ConfigError('device must be a guest IPv4 or MAC address') from None
    if address not in subnet or address in (subnet.network_address, subnet.broadcast_address, subnet.network_address + 1):
        raise ConfigError('address is not a guest device of this gate')
    return str(address)


def boost_commands(config, ip, tier, minutes):
    """nft commands moving one device to a tier for a bounded time."""
    if tier not in config['tiers']:
        raise ConfigError(f'unknown tier: {tier}')
    if isinstance(minutes, bool) or not isinstance(minutes, int) or not 1 <= minutes <= 24 * 60:
        raise ConfigError('duration must be between 1 minute and 24 hours')
    return unboost_commands(config, ip) + [f'add element inet {TABLE} tier_{tier} {{ {ip} timeout {minutes}m }}']


def unboost_commands(config, ip):
    # `add` then `delete` is atomic within one transaction and succeeds
    # whether or not the device was in the tier.
    commands = []
    for name in config['tiers']:
        commands += [f'add element inet {TABLE} tier_{name} {{ {ip} }}', f'delete element inet {TABLE} tier_{name} {{ {ip} }}']
    return commands


def nft(script, check=False):
    subprocess.run(['nft', *(['-c'] if check else []), '-f', '-'], input=script, text=True, check=True)


def current_boosts(config):
    listing = json.loads(subprocess.run(['nft', '-j', 'list', 'table', 'inet', TABLE], capture_output=True, text=True, check=True).stdout)
    boosts = {}
    for item in listing.get('nftables', []):
        entry = item.get('set')
        if not entry or not entry['name'].startswith('tier_'):
            continue
        for element in entry.get('elem', []):
            value = element.get('elem', {}) if isinstance(element, dict) else {'val': element}
            boosts[value['val']] = {'tier': entry['name'][5:], 'expires_in_s': value.get('expires')}
    return boosts


def main(argv=None):
    parser = argparse.ArgumentParser(prog='wifi-gate', description=__doc__.splitlines()[0])
    parser.add_argument('--config', default=DEFAULT_CONFIG)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('render', help='print the nftables ruleset')
    commands.add_parser('check', help='validate the ruleset without loading it')
    commands.add_parser('apply', help='load the ruleset (drops current boosts)')
    commands.add_parser('remove', help='unload the gate')
    commands.add_parser('status', help='list guest devices and their tier as JSON')
    boost = commands.add_parser('boost', help='grant a tier to one device for a bounded time')
    boost.add_argument('device', help='guest IPv4 or MAC address')
    boost.add_argument('tier')
    boost.add_argument('minutes', type=int)
    commands.add_parser('unboost', help='return one device to the default rate').add_argument('device')
    args = parser.parse_args(argv)
    try:
        with open(args.config, encoding='utf-8') as handle:
            config = load_config(json.load(handle))
        if args.command == 'render':
            sys.stdout.write(render_ruleset(config))
        elif args.command in ('check', 'apply'):
            nft(render_ruleset(config), check=args.command == 'check')
        elif args.command == 'remove':
            nft(f'table inet {TABLE}\ndelete table inet {TABLE}\n')
        else:
            try:
                with open(config['leases'], encoding='utf-8') as handle:
                    devices = read_leases(handle.read(), config['subnet'])
            except FileNotFoundError:
                devices = []
            if args.command == 'status':
                boosts = current_boosts(config)
                for device in devices:
                    device.update(boosts.get(device['ip'], {'tier': 'default', 'expires_in_s': None}))
                    device['rate_kbit'] = config['tiers'].get(device['tier'], config['default_kbit'])
                json.dump({'at': int(time.time()), 'default_kbit': config['default_kbit'], 'tiers': config['tiers'], 'devices': devices}, sys.stdout, indent=2)
                sys.stdout.write('\n')
            else:
                ip = resolve_device(args.device, devices, config['subnet'])
                script = boost_commands(config, ip, args.tier, args.minutes) if args.command == 'boost' else unboost_commands(config, ip)
                nft('\n'.join(script) + '\n')
    except (ConfigError, OSError, json.JSONDecodeError) as error:
        print(f'wifi-gate: {error}', file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as error:
        print(f'wifi-gate: nft failed with status {error.returncode}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
