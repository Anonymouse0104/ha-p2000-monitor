## P2000 Monitor v0.7.0

Deze release lost op dat de integratie traag liep en meldingen kon missen.

### ⚠️ Let op voordat je bijwerkt
- **YAML-ondersteuning is verwijderd.** Haal `sensor: - platform: p2000_monitor` uit je configuratie en verwijder daarna de verweesde entities (`p2000_monitor_main`, `..._legacy_N`). Sensors uit de Config Flow werken gewoon door, met dezelfde entity-ID's.
- **`p2000_monitor_new_message` komt nu één keer per melding** en heeft geen `sensor`-veld meer. Automations voor één specifieke sensor gebruiken `p2000_monitor_filter_match` met een voorwaarde op `sensor`.
- **"Tekst moet bevatten" werkt nu als OF:** één van de woorden is genoeg, zoals de documentatie al beschreef.
- De incidenthistorie wordt nu per sensor opgeslagen en begint daardoor opnieuw. Het oude bestand `.storage/p2000_monitor.incident_history` mag weg.

### Prestaties
- Eén gedeelde poll voor alle sensors in plaats van één API-verzoek per sensor, elke 10 seconden.
- Gebruikt de gedeelde HTTP-sessie van Home Assistant.
- State en attributen worden één keer per update berekend.

### Geen gemiste of dubbele meldingen
- Geneste meldingen in de feed (`subitems`) worden nu verwerkt.
- Deduplicatie gaat op het vaste `id` van de AlarmeringDroid API.
- Eén event per nieuwe melding, ook als er meerdere binnen één poll binnenkomen.
- Tijden worden geparsed met `dt_util`. Meldingen uit dezelfde minuut worden op API-id gesorteerd.

### Filters
- OF binnen een filter, EN tussen filters.
- Capcodes matchen ook met voorloopnullen en over alle capcodes van een melding.
- "Toon alle meldingen" laat de tekstfilters actief, zoals de UI beschrijft.

### Stabiliteit
- Bij een API-storing geen traceback-spam meer. Het pollen vertraagt tot maximaal 5 minuten en herstelt daarna automatisch.
- Sensors blijven bij één mislukte poll 5 minuten beschikbaar met hun laatste waarde, in plaats van te flapperen naar `unavailable`.
- Grote attributen (`meldingen`, `incidenten`, `incident_history`, `incident`, `capcodes`) worden niet meer door de recorder opgeslagen. Dat voorkomt de 16 KB-waarschuwingen.
- Meldingen langer dan 255 tekens worden in de state ingekort. De volledige tekst staat in het attribuut `message`.
- Opslaan in de options flow herlaadt de sensor nog maar één keer.
- De actie `p2000_monitor.inject_test_incident` werkt nu ook voor Config Flow-sensors.
