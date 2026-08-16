# P2000 Monitor for Home Assistant

Home Assistant integration for live Dutch P2000 emergency alerts using the structured **AlarmeringDroid API**.

## v0.5.5

This release fixes the filtering path that could allow messages from the wrong safety region into a configured sensor and fixes sensors that stayed empty when a matching message was already present during startup.

### Important changes

- **AlarmeringDroid API only** in the active data path.
- Region, service and capcode filters are enforced **locally as well as at the API**, so an API-side filtering problem cannot leak another region into a sensor.
- Priority, include-text and exclude-text filters are applied locally.
- Filter sensors now use the current API result instead of only `new_messages`.
- Old split coordinator/P2KFlex code is removed from the active integration.
- Additional logging shows the API filter, result count, normalized count and filtered count.
- P2000 Monitor branding is included with `icon.png` and `logo.png`.

## Install with HACS

1. Open **HACS**.
2. Find **P2000 Monitor**.
3. Update to **v0.5.5**.
4. Restart Home Assistant.
5. Open **Settings → Devices & services → P2000 Monitor**.

## Configure your own sensors

Every Config Flow entry is an independent sensor. A different user can install the integration and choose their own filters; nothing is hard-coded to Echt or Zuid-Limburg.

Available filters:

- Safety region
- Discipline/service
- Priority P1–P5
- Capcodes
- Text must contain
- Text must not contain
- Incident window

Filters are combined with **AND** logic between categories. Text values can be separated by commas or semicolons.

### Examples

**Brandweer Zuid-Limburg**

- Region: `Zuid-Limburg`
- Service: `Brandweer`

**Brandweer Echt**

- Region: `Limburg-Noord`
- Service: `Brandweer`
- Include text: `Echt`

**Alle P1-meldingen**

- Priority: `P1`

## AlarmeringDroid region IDs

The integration uses the IDs returned by the AlarmeringDroid API. Important Limburg values are:

| ID | Region |
|---:|---|
| 15 | Limburg-Noord |
| 21 | Zuid-Limburg |
| 23 | Twente |

This mapping is deliberately kept in one place. The old P2KFlex 23/24 mapping is no longer used by the active integration.

## Services

| ID | Service |
|---:|---|
| 1 | Politie |
| 2 | Brandweer |
| 3 | Ambulance |
| 4 | KNRM |
| 5 | Lifeliner |
| 7 | DARES |

## Troubleshooting

The integration logs the filtering pipeline under the `p2000_monitor` logger. Useful entries include:

```text
P2000 API filter: {"regios":["15"],"diensten":["2"]}
P2000 API returned ... messages; normalized ...
P2000 after filters: ... messages
P2000 initialized: latest=... region=15/Limburg-Noord
```

If a message for Enschede (Twente, region 23) is returned while a sensor is configured for region 15, the local region filter removes it before the sensor sees it.

## Events

The integration exposes these Home Assistant events:

```text
p2000_monitor_new_message
p2000_monitor_filter_match
p2000_monitor_new_incident
p2000_monitor_incident_update
```

## Legacy YAML

Existing YAML installations remain supported. New installations should use Config Flow.

## Data source

The active integration uses the structured AlarmeringDroid API and no longer relies on the P2KFlex mobile HTML parser.

## License

MIT.
