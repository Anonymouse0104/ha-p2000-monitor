## P2000 Monitor v0.7.1

### Opgelost
- **Gefilterde sensors bleven op "Geen meldingen" staan.** De AlarmeringDroid-feed bevat alleen de laatste ~30 meldingen van heel Nederland, vaak minder dan een half uur. Een sensor liet alleen meldingen zien die op dat moment nog in die feed stonden. Nu houdt elke sensor zelf een eigen geschiedenis bij van de laatste 100 meldingen die bij zijn filters passen.
- Deze geschiedenis blijft bewaard na een herstart en na het wijzigen van de opties. Na het wijzigen van de filters worden de opgeslagen meldingen opnieuw gecontroleerd.
- Wijzigingen die nog niet waren weggeschreven, gingen verloren bij het herladen van een sensor. Die worden nu direct opgeslagen.

Na het bijwerken begint de geschiedenis leeg. De eerstvolgende melding die bij je filters past, blijft vanaf dan staan.

Zie de [v0.7.0-release](https://github.com/Anonymouse0104/ha-p2000-monitor/releases/tag/v0.7.0) voor de grote wijzigingen van de vorige versie.
