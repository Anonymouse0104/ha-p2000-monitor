# P2000 Monitor

Home Assistant custom integration for Dutch P2000 emergency-service messages through P2KFlex.

## Features

- Central P2000 monitor sensor.
- Regional incident history for Brandweer Limburg-Noord (23) and Brandweer Zuid-Limburg (24).
- Persistent history across Home Assistant restarts.
- Stable message IDs and duplicate protection.
- HACS-compatible installation and updates.

## HACS installation

Add `Anonymouse0104/ha-p2000-monitor` as a custom repository of type **Integration**, install the latest release, and restart Home Assistant.

## Troubleshooting

Restart Home Assistant after updates and check the logs for `custom_components.p2000_monitor`.

## Development

Source: `custom_components/p2000_monitor/`

## License

MIT
