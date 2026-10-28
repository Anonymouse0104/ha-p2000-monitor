## P2000 Monitor v0.9.3

### Gewijzigd
- **Minder belasting voor de AlarmeringDroid-API.** De landelijke feed wordt nu standaard elke **30 seconden** opgehaald in plaats van elke 10 seconden, voor alle sensoren samen. Dit naar aanleiding van de review bij de opname in HACS: elke installatie vraagt dezelfde gratis bèta-API op.
- **Poll-interval instelbaar** via **P2000 Monitor → Configureren** (30–300 seconden). Bij een storing wordt het interval verdubbeld tot maximaal 5 minuten en daarna weer teruggezet naar de ingestelde waarde.

Je mist met een langer interval geen meldingen: de feed bevat steeds de laatste ~30 meldingen van heel Nederland (meestal ruim 20 minuten), en elke melding wordt maar één keer verwerkt.
