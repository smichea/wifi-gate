# wifi-gate

Portier du Wi-Fi clients d'un petit établissement, sur un Raspberry Pi ou
toute machine Debian : chaque appareil invité est isolé du réseau privé et
limité à un débit lent par défaut ; un appelant peut accorder un palier plus
rapide à un appareil pour une durée bornée.

Le portier fournit le mécanisme, pas la règle commerciale. Il ne connaît ni
compte, ni points de fidélité, ni tarif : ces décisions appartiennent au
système qui l'appelle (caisse, application de fidélité).

## Fonctionnement

- NetworkManager émet le point d'accès en mode « partagé » : DHCP, DNS et NAT
  du sous-réseau invité (`scripts/create-ap.sh`).
- `wifi-gate` charge une table nftables `inet wifi_gate` qui :
  - interdit aux invités toute destination privée (réseau de l'établissement,
    page d'administration du routeur, autres réseaux de la machine) et l'IPv6 ;
  - ne leur ouvre sur la machine que DHCP, DNS et les ports listés dans
    `host_tcp_ports` ;
  - limite chaque adresse au débit par défaut, dans les deux sens ;
  - applique un palier plus rapide aux adresses présentes dans un ensemble
    `tier_<nom>`. Chaque entrée porte une durée : le noyau la retire à
    l'expiration, sans démon.

La limite est un plafonnement par rejet des paquets excédentaires, avec deux
secondes de rafale. Les appareils sont identifiés par leur adresse IPv4
invitée ; une adresse MAC est résolue par le bail DHCP en cours.

## Installation

```sh
sudo bash scripts/install.sh
sudoedit /etc/wifi-gate/config.json      # débits et paliers
sudo systemctl start wifi-gate
echo 'phrase de passe' | sudo bash scripts/create-ap.sh 'Nom du Wi-Fi' 6
sudo nmcli con up wifi-gate-ap
```

## Commandes

```sh
sudo wifi-gate status                       # appareils, palier, expiration (JSON)
sudo wifi-gate boost 10.20.0.23 fast 30     # palier « fast » pendant 30 minutes
sudo wifi-gate boost aa:bb:cc:dd:ee:01 max 60
sudo wifi-gate unboost 10.20.0.23
sudo wifi-gate apply                        # recharge après modification (efface les paliers en cours)
```

## Configuration

Voir [config.example.json](config.example.json) : interface, sous-réseau
invité, débit par défaut et paliers en kbit/s, ports TCP de la machine ouverts
aux invités (pour une future page d'accueil).

## Tests

```sh
python3 -m unittest discover -s test
```

Les tests couvrent la validation de la configuration, la génération des
règles et la résolution des appareils. La validation sur matériel est décrite
dans [docs/VALIDATION.md](docs/VALIDATION.md).

## Limites connues

- La puce Wi-Fi d'un Raspberry Pi n'accepte qu'un nombre réduit de clients en
  point d'accès ; un point d'accès externe lève cette limite.
- Un invité peut usurper l'adresse IPv4 d'un appareil boosté.
- Aucune interface HTTP ni page d'accueil pour l'instant.
- Sur un hôte dont le pare-feu rejette par défaut les entrées, DHCP et DNS de
  l'interface invitée sont à autoriser à la main (voir la validation).

Licence : AGPL-3.0-or-later.
