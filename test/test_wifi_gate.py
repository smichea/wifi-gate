import ipaddress
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'src'))
import wifi_gate as gate  # noqa: E402

CONFIG = {'interface': 'wlan0', 'subnet': '10.20.0.0/24', 'default_kbit': 256, 'tiers': {'fast': 2000, 'max': 10000}}


class ConfigTest(unittest.TestCase):
    def test_defaults_are_slow_and_private(self):
        config = gate.load_config({})
        self.assertEqual(config['default_kbit'], 256)
        self.assertEqual(config['subnet'], ipaddress.ip_network('10.20.0.0/24'))
        self.assertEqual(config['tiers'], {})

    def test_rejects_values_that_would_reach_the_ruleset_unsafely(self):
        for bad in ({'interface': 'wlan0"; drop'}, {'subnet': '8.8.8.0/24'}, {'subnet': '10.0.0.0/30'},
                    {'tiers': {'fast; flush ruleset': 10}}, {'tiers': {'default': 10}}, {'tiers': {'fast': 0}},
                    {'tiers': {'fast': True}}, {'default_kbit': '256'}, {'host_tcp_ports': [0]}, {'tiers': []}):
            with self.assertRaises(gate.ConfigError, msg=bad):
                gate.load_config(bad)


class RulesetTest(unittest.TestCase):
    def setUp(self):
        self.config = gate.load_config(CONFIG)
        self.rules = gate.render_ruleset(self.config)

    def test_default_rate_is_256_kbit_in_both_directions(self):
        self.assertIn('update @meter_up_default { ip saddr limit rate over 32000 bytes/second burst 64000 bytes } drop', self.rules)
        self.assertIn('update @meter_down_default { ip daddr limit rate over 32000 bytes/second burst 64000 bytes } drop', self.rules)

    def test_guests_cannot_reach_private_networks_or_the_host(self):
        self.assertIn('ip daddr { 10.0.0.0/8, 100.64.0.0/10, 169.254.0.0/16, 172.16.0.0/12, 192.168.0.0/16 } drop', self.rules)
        self.assertIn('ip saddr != 10.20.0.0/24 drop', self.rules)
        self.assertIn('meta nfproto != ipv4 drop', self.rules)
        guest_input = self.rules.split('chain input {')[1]
        self.assertIn('iifname != "wlan0" accept', guest_input)
        self.assertNotIn('tcp dport {', guest_input)
        self.assertTrue(guest_input.rstrip().endswith('drop\n  }\n}'.rstrip()))

    def test_private_drop_precedes_every_accept_for_guest_traffic(self):
        chain = self.rules.split('chain from_guest {')[1].split('}\n  chain')[0]
        self.assertLess(chain.index('192.168.0.0/16'), chain.index('accept'))

    def test_faster_tiers_are_matched_before_the_default_limit(self):
        chain = self.rules.split('chain to_guest {')[1]
        self.assertLess(chain.index('@tier_max'), chain.index('@tier_fast'))
        self.assertLess(chain.index('@tier_fast accept'), chain.index('meter_down_default'))
        self.assertIn('ip daddr @tier_fast update @meter_down_fast { ip daddr limit rate over 250000 bytes/second burst 500000 bytes } drop', chain)

    def test_reload_is_atomic_and_idempotent(self):
        self.assertTrue(self.rules.startswith('table inet wifi_gate\ndelete table inet wifi_gate\ntable inet wifi_gate {'))

    def test_portal_ports_open_only_when_configured(self):
        rules = gate.render_ruleset(gate.load_config({**CONFIG, 'host_tcp_ports': [8080, 80, 80]}))
        self.assertIn('tcp dport { 80, 8080 } accept', rules)


class DeviceTest(unittest.TestCase):
    LEASES = ('1794400000 aa:bb:cc:dd:ee:01 10.20.0.23 phone-a 01:aa:bb:cc:dd:ee:01\n'
              '1794400500 AA:BB:CC:DD:EE:02 10.20.0.24 * *\n'
              '1794400100 aa:bb:cc:dd:ee:03 192.168.8.9 other *\n'
              'garbage line\n')

    def setUp(self):
        self.config = gate.load_config(CONFIG)
        self.devices = gate.read_leases(self.LEASES, self.config['subnet'])

    def test_leases_keep_only_guest_devices_newest_first(self):
        self.assertEqual([d['ip'] for d in self.devices], ['10.20.0.24', '10.20.0.23'])
        self.assertEqual(self.devices[0], {'mac': 'aa:bb:cc:dd:ee:02', 'ip': '10.20.0.24', 'name': '', 'lease_expires': 1794400500})

    def test_device_resolves_by_mac_or_guest_ip_only(self):
        subnet = self.config['subnet']
        self.assertEqual(gate.resolve_device('AA:BB:CC:DD:EE:01', self.devices, subnet), '10.20.0.23')
        self.assertEqual(gate.resolve_device('10.20.0.77', self.devices, subnet), '10.20.0.77')
        for bad in ('192.168.8.1', '10.20.0.1', '10.20.0.0', '10.20.0.255', 'aa:bb:cc:dd:ee:99', '10.20.0.5 }; flush ruleset', ''):
            with self.assertRaises(gate.ConfigError, msg=bad):
                gate.resolve_device(bad, self.devices, subnet)

    def test_boost_moves_a_device_to_exactly_one_tier_with_expiry(self):
        commands = gate.boost_commands(self.config, '10.20.0.23', 'fast', 30)
        self.assertEqual(commands[-1], 'add element inet wifi_gate tier_fast { 10.20.0.23 timeout 30m }')
        self.assertIn('delete element inet wifi_gate tier_max { 10.20.0.23 }', commands[:-1])
        self.assertIn('delete element inet wifi_gate tier_fast { 10.20.0.23 }', commands[:-1])

    def test_boost_refuses_unknown_tiers_and_unbounded_durations(self):
        for tier, minutes in (('default', 10), ('nope', 10), ('fast', 0), ('fast', 24 * 60 + 1), ('fast', True)):
            with self.assertRaises(gate.ConfigError, msg=(tier, minutes)):
                gate.boost_commands(self.config, '10.20.0.23', tier, minutes)


if __name__ == '__main__':
    unittest.main()
