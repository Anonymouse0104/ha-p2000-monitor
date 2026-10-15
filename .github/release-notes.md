## P2000 Monitor v0.9.1

### Opgelost
- **Sensoren waren na een herstart leeg.** Elke sensor haalt bij het opstarten nu zelf zijn laatste ~30 passende meldingen op, met zijn eigen regio's, diensten en capcodes, ook als die uren oud zijn. Een sensor voor een rustige regio toont dus direct de laatst bekende melding, ook als hij net is aangemaakt. Deze opgehaalde meldingen starten geen automations.
- **Politiemeldingen in de verkeerde regio.** De politie meldt per eenheid ("Eenheid Limburg"), niet per veiligheidsregio. Zo'n melding telt nu voor alle veiligheidsregio's van die eenheid en toont de naam van de eenheid. Eerder kreeg een Limburgse politiemelding het label Zuid-Limburg en miste een sensor voor Limburg-Noord ze.
- Opgeslagen historie wordt niet meer leeg overschreven als het inlezen ervan eens mislukt.
