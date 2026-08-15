# P2000 Monitor

Home Assistant custom integration for monitoring Dutch P2000 emergency-service messages through P2KFlex.

## Features

- Central `P2000 Monitor` sensor.
- Regional incident-history sensors for Brandweer Limburg-Noord (region 23) and Brandweer Zuid-Limburg (region 24).
- Persistent incident history across Home Assistant restarts.
- Stable message IDs and duplicate protection.
- P2KFlex polling with robust HTML/JavaScript message cleaning.
- HACS-compatible installation and updates.

## HACS installation

1. Open **HACS → Integrations**.
2. Add `Anonymouse0104/ha-p2000-monitor` as a custom repository if it is not already present.
3. Select **Integration** as the repository type.
4. Install the latest release of **P2000 Monitor**.
5. Restart Home Assistant.

## Sensors

The main P2000 sensor receives the central P2KFlex feed. Regional incident-history sensors are derived from that central feed so related regional views remain consistent.

## Troubleshooting

After an update, restart Home Assistant so files under `custom_components/p2000_monitor` are reloaded.

Check **Settings → System → Logs** for entries from `custom_components.p2000_monitor`.

## Development

Source code:

```text
custom_components/p2000_monitor/
```

Releases use semantic version tags such as `v0.4.4`.

## License

MIT
