## P2000 Monitor v0.9.0

### Nieuw
- **Live-kaart in P2Kflex-stijl** (`custom:p2000-live-card`), automatisch geladen. Toont dienstkleuren, prioriteit, GRIP, regio, plaats en afstand, en per dienst het aantal meldingen van vandaag (klik om de dienst te verbergen). Wisselen tussen meldingtekst, pagertekst of beide. Klik op een melding voor de gealarmeerde voertuigen en capcodes. Toe te voegen via **Kaart toevoegen → P2000 live**.
- **Kaart**: meldingen met locatie uit het afgelopen uur verschijnen op de standaard kaartkaart (`geo_location_sources: [p2000_monitor]`).
- **Capcode of voertuig volgen**: maakt een apparaat met een *Alarm*-gebeurtenis en een *Actief*-sensor. Daarmee maak je automations zonder YAML. Met de blueprint **P2000 alarm melden** stuur je een pushbericht naar je telefoon.
- **Voertuigen**: roepnummers uit de pagertekst (`240733` → `24-0733`), met type en post uit Brandbase. Brandbase wordt alleen geraadpleegd voor regio's waarvan je meldingen ontvangt, hooguit één keer per week; uit te zetten via **Configureren**.
- **Nieuwe sensorfilters**: voertuigen, straal rond je huis (km), en de weergave van de status (meldingtekst of pagertekst).
- **Prioriteit**: ambulance A1/A2/B worden herkend; P1 geldt ook voor A1. Het prioriteitsfilter werkt nu op de pagertekst. In de leesbare meldingtekst staat de prioriteit meestal niet, waardoor het filter eerder vaak niets vond.
- **Capcode-omschrijvingen** uit de API, GRIP-niveau, straat en postcode als attributen.
- Voorbeelddashboard in `dashboards/p2000_live.yaml`.

### Klaar voor HACS
- Controle door de HACS-action, hassfest en tests in CI; `country: NL` in `hacs.json`.
