# P2000 Monitor

Home Assistant custom integration for monitoring Dutch P2000 emergency-service messages through P2KFlex.

## Features

- Central `P2000 Monitor` sensor.
- Regional incident-history sensors for Brandweer Limburg-Noord and Brandweer Zuid-Limburg.
- P2KFlex polling for regions 23 and 24.
- Persistent incident history across Home Assistant restarts.
- Stable message IDs and duplicate protection.
- HACS-compatible installation and updates.

## HACS installation

Add `Anonymouse0104/ha-p2000-monitor` as a custom repository of type **Integration**, install the latest release, and restart Home Assistant.

## Development

Integration source:

```text
custom_components/p2000_monitor/
```

Create a new semantic version tag for releases, for example `v0.4.4`.

## Troubleshooting

After an update, restart Home Assistant so the files under `custom_components/p2000_monitor` are reloaded. Check **Settings → System → Logs** for entries from `custom_components.p2000_monitor`.

## License

MIT
