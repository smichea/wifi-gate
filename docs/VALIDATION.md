# Validation sur matériel

## 2026-11-11 — Raspberry Pi 5, Debian 13, nftables 1.1.3, NetworkManager 1.52.1

Montage : Pi relié par Ethernet à un routeur domestique (192.168.100.0/24)
servant d'accès Internet ; point d'accès émis par la puce Wi-Fi intégrée,
canal 6, WPA2, sous-réseau invité 10.20.0.0/24. Client : un téléphone Android.

| Vérification | Résultat |
|---|---|
| `nft -c` accepte le jeu de règles généré | Réussi |
| Le téléphone obtient une adresse et accède à Internet | Réussi |
| Débit par défaut (256 kbit/s) : test de débit du propriétaire | Conforme selon le propriétaire, valeurs non relevées |
| Page d'administration du routeur (192.168.100.1) inaccessible depuis le Wi-Fi invité | Réussi |
| `boost <MAC> fast 5` : palier 2000 kbit/s | 1,86 Mbit/s en réception, 2,47 Mbit/s en envoi |
| Expiration du palier sans intervention | Entrée retirée par le noyau, `status` revenu à `default` |

L'envoi mesuré dépasse le palier parce qu'un test d'une dizaine de secondes
inclut les deux secondes de rafale ; le débit soutenu n'a pas été mesuré sur
une durée longue. Le débit après expiration n'a pas été remesuré au téléphone.

Compléments du même jour :

- Redémarrage du Pi : point d'accès, table `wifi_gate` et partage
  NetworkManager rétablis sans intervention ; les paliers en cours ne
  survivent pas au redémarrage.
- Un balayage Wi-Fi (`nmcli dev wifi rescan`) pendant l'émission du point
  d'accès aboutit et ne déconnecte pas les deux stations associées.

Non testé : nombre maximal de clients de la puce, isolation entre deux
invités, usurpation d'adresse, charge avec plusieurs clients actifs.

## Particularité de l'hôte

Ce Pi porte déjà une table `inet home_server_filter` dont la chaîne `input`
rejette par défaut. Il a fallu y autoriser, sur l'interface invitée, DHCP
(`udp sport 68 dport 67`) et DNS depuis le sous-réseau invité. `wifi-gate`
ne modifie pas les tables des autres ; sur un hôte doté d'un pare-feu
restrictif, ces deux ouvertures sont à ajouter à la main.

## DNS derrière un routeur 5G (2026-11-11)

Derrière un routeur DITO Home, la moitié des requêtes DNS d'un téléphone invité
restaient sans réponse dans les secondes suivant le changement de réseau
(8 sur 16 dans une capture de 26 s), alors que les connexions TCP aboutissaient.
Le routeur annonce un résolveur IPv4 et deux résolveurs IPv6. Avec
`deploy/dnsmasq-shared.conf` (`all-servers` plus deux résolveurs publics), le
téléphone a retrouvé un accès normal. La cause exacte n'a pas été isolée : aucune
capture n'a été refaite après le correctif.
