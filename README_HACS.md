# P2000 Monitor

Home Assistant custom integration for Dutch P2000 emergency-service messages through P2KFlex.

## HACS

Add `Anonymouse0104/ha-p2000-monitor` as an Integration custom repository, install the latest release, then restart Home Assistant.

## Features

- Central P2000 monitor sensor
- Regional incident history for Limburg-Noord (23) and Zuid-Limburg (24)
- Persistent history
- Stable message IDs and duplicate protection
- P2KFlex polling

## Development

Source: `custom_components/p2000_monitor/`

Release tags use semantic versioning.

## Troubleshooting

Restart Home Assistant after updates and check logs for `custom_components.p2000_monitor`.

## License

MIT
