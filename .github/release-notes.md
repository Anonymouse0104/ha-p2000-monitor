## P2000 Monitor v0.9.4

### Opgelost
- **Ambulancemeldingen als brandweer getoond.** AlarmeringDroid levert sommige ambulancemeldingen (onder meer Sittard, Geleen en Echt, capcode 1023193) aan als brandweer, zonder capcodes en met een verzonnen brandweertekst, bijvoorbeeld "Brandweer met spoed naar Prinses Christinastraat, Echt" voor "A1 Prinses Christinastraat Echt 99369". Een melding waarvan de pagertekst begint met A1, A2, B1 of B2 wordt nu als ambulance getoond, met de echte pagertekst.

### Bekend
- Bij deze meldingen geeft AlarmeringDroid ook een verkeerde regio door (Amsterdam-Amstelland). Daardoor komen ze niet binnen op een sensor met een regiofilter voor Zuid-Limburg; wel op sensoren voor heel Nederland of met een straal rond je huis.
