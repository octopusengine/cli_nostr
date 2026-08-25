# cli_nostr

Jednotné CLI pro experimenty s Nostr. Zdroje a funkční inspirace zůstávají v
`inspirace/`; nový vstupní bod je `cli_nostr.py`.

## Fáze 1 – lokální klíč a `.env`

```powershell
python cli_nostr.py --key-create
python cli_nostr.py --key-info
python cli_nostr.py --user NOSTR_KEY2 --key-info
python cli_nostr.py --config
python cli_nostr.py --connect -v
python cli_nostr.py --stream -v
python cli_nostr.py --msg holinky "pokus 1 z dellu"
python cli_nostr.py --db-msg
python cli_nostr.py --db-str
python cli_nostr.py --db-show 1
```

`--key-create` uloží náhodný platný secp256k1 soukromý klíč jako `NOSTR_KEY`
do `.env`. Existující klíč nikdy nepřepíše bez explicitního `--force`.
Soukromý klíč se nevypisuje. `.env` je ignorován Gitem.

## Více uživatelů / klíčů

Výchozí identita je `NOSTR_KEY`. Pro jedno spuštění CLI zvolte jinou proměnnou
z `.env` pomocí `--user`; například `--user NOSTR_KEY2` používá po celou dobu
daného spuštění klíč z `NOSTR_KEY2`. Volba se vztahuje na `--key-info`,
`--msg` i `--receive` (a s `--key-create` vytvoří klíč pod zvoleným názvem).
Nezmění `.env` ani výchozí identitu pro další spuštění.

```powershell
python cli_nostr.py --user NOSTR_KEY2 --key-info
python cli_nostr.py --user NOSTR_KEY2 --msg holinky "zpráva od druhé identity"
python cli_nostr.py --user NOSTR_KEY2 --receive
```

Pro zobrazení odvozeného `npub` a pro budoucí relay akce nainstalujte závislosti:

```powershell
python -m pip install -r requirements.txt
```

## Relay a veřejný stream

Seznam relayů je v `nostr/relays.json`. Příkaz `-c` / `--connect` jen otevře
WebSocket a nic nepublikuje. `-v` / `--verbose` doplní průběh, čas a chybové
detaily. `-s` / `--stream` vybere první dosažitelný relay a vypíše tři poslední
veřejné zprávy kind 1.

```powershell
python cli_nostr.py -c -v
python cli_nostr.py -s -v
python cli_nostr.py -V
```

## Soukromá zpráva příteli

`data/friends.json` obsahuje vazbu jména na veřejný klíč. Podporuje stávající
stručný tvar `{"holinky": "npub1…"}` i seznam záznamů `[{"name": "holinky",
"key": "npub1…"}]`. Příkaz `-m` vytvoří a odešle šifrovanou NIP-17 zprávu
(gift-wrap pro příjemce i vlastní kopii). Počet relayů pro zprávu určuje
`num_msg_relays` v `cli_nostr.json`; výchozí hodnota je 3. Pro každý vybraný
relay CLI čeká na NIP-01 odpověď `OK`, nejvýše po dobu `timeout` sekund ze setupu.

```powershell
python cli_nostr.py -m holinky "pokus 1 z dellu" -v
```

## Lokální historie zpráv

`nostr_msg.db` je lokální SQLite databáze. Po odeslání zprávy se uloží její
text a stav doručení; po dešifrování příjmu se uloží přijatý text. Soubor tedy
obsahuje čitelný obsah DM a je v `.gitignore`.

```powershell
python cli_nostr.py --db-msg
python cli_nostr.py --db-str
```

## Příjem zpráv

`-r` / `--receive` otevře paralelně až `num_msg_relays` relayů, poslouchá jen
nové gift-wrapy NIP-17 určené pro zvolený klíč a příchozí zprávu ihned
vypíše. Zprávy neukládá ani nezpracovává z message poolu. `msg_timeout` v
`cli_nostr.json` je výchozí doba čekání 100 sekund. Během tohoto času se na
jednom řádku po deseti sekundách vypisuje odpočet `9 8 7 … 0`.

Protože NIP-17 záměrně zpětně posouvá čas gift-wrapu, `-r` navíc načte historii
z posledních tří dnů (`msg_lookback: 259200`); duplicitní události z více
relayů zobrazí jen jednou.

```powershell
python cli_nostr.py -r -v
```

## ToDo

- [ ] Přidat správu profilu a metadat z `nostr_meta_info.py`.
- [ ] Přidat publikování vlastních veřejných událostí z `nostr_publish_event.py`.
- [ ] Doplnit správu kontaktů a uživatelských dat z `nostr_user.py`.
- [ ] Rozšířit terminálové a databázové wrappery pro další Nostr akce.
