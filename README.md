# P2000 Monitor for Home Assistant

Home Assistant integration for live Dutch P2000 emergency alerts using the structured **AlarmeringDroid API**.

## v0.5.7

This release changes the data path so the integration fetches the complete AlarmeringDroid feed and applies all configured filters locally.

### Important changes

- No unsupported JSON filter payload is sent to `/api2/find/`.
- Region, service, capcode, priority, include-text and exclude-text filters are applied locally.
- Capcodes returned by AlarmeringDroid as objects inside a `capcodes` array are parsed correctly.
- A capcode filter matches when any capcode in a grouped message matches.
- The newest matching message is selected by publication timestamp.
- Existing Config Flow entries and incident history remain supported.
- A P2000 Monitor integration icon is included as `icon.svg`.

## Install with HACS

1. Open **HACS**.
2. Find **P2000 Monitor**.
3. Update to **v0.5.7**.
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
- Capcodes: your selected Echt capcodes

**Alle P1-meldingen**

- Priority: `P1`

## AlarmeringDroid region IDs

The integration uses the IDs returned by the AlarmeringDroid API. Important Limburg values are:

| ID | Region |
|---:|---|
| 15 | Limburg-Noord |
| 21 | Zuid-Limburg |
| 23 | Twente |

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
P2000 API: fetching complete unfiltered feed; local filters=...
P2000 API returned ... messages; normalized ...
P2000 after local filters: ... messages (from ...)
P2000 new message: ...
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

The active integration uses the structured AlarmeringDroid API and does not depend on the P2KFlex mobile HTML parser.

## License

MIT.
