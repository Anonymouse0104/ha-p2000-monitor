# P2000 Monitor for Home Assistant

Home Assistant integration for live Dutch P2000 emergency alerts using the structured **AlarmeringDroid API**.

## v0.6.0

This release focuses on making the monitor and filtered sensors deterministic and removes a subtle capcode filtering bug.

### Important changes

- The integration fetches the complete AlarmeringDroid feed and applies configured filters locally.
- No unsupported JSON filter payload is sent to `/api2/find/`.
- The main **P2000 Monitor** can explicitly run in **Toon alle meldingen** mode and then receives all regions and all emergency services.
- Existing entries named exactly **P2000 Monitor** are automatically treated as the unfiltered national monitor when upgraded from an older version.
- Region, service, capcode, priority, include-text and exclude-text filters are applied locally.
- Capcodes returned by AlarmeringDroid as a grouped `capcodes` array are parsed correctly.
- A capcode filter matches **any** capcode in a grouped alert, not only the first capcode. This is important for stations such as Brandweer Echt where the relevant capcode may not be the first one in the message.
- Filtered Config Flow sensors no longer apply the same filter twice, avoiding false negatives after the coordinator already filtered the feed.
- The newest matching message is selected by publication timestamp.
- Correct Dutch Veiligheidsregio names are used, including **Limburg-Noord** and **Zuid-Limburg**.
- A P2000 Monitor integration icon is included as `icon.svg`.

## Install with HACS

1. Open **HACS**.
2. Find **P2000 Monitor**.
3. Update to **v0.6.0**.
4. Restart Home Assistant.
5. Open **Settings → Devices & services → P2000 Monitor**.

## Configure your own sensors

Every Config Flow entry is an independent sensor. A different user can install the integration and choose their own filters; nothing is hard-coded to Echt or Zuid-Limburg.

Available filters:

- **Toon alle meldingen** — disables the other filters and shows the complete national feed.
- Safety region
- Discipline/service
- Priority P1–P5
- Capcodes
- Text must contain
- Text must not contain
- Incident window

Normal filters are combined with **AND** logic between categories. Multiple capcodes within one category use **OR** logic: if any capcode in the alert matches, the alert passes the capcode filter. Text values can be separated by commas or semicolons.

### Recommended setup

**P2000 Monitor — complete national monitor**

- Name: `P2000 Monitor`
- **Toon alle meldingen: ON**
- Leave region, service, priority and capcode filters empty.

This is the sensor intended to receive **all** P2000 messages from all emergency services and all Dutch Veiligheidsregio's.

**Brandweer Limburg**

- Region: `Limburg-Noord` and/or `Zuid-Limburg`
- Service: `Brandweer`

**Brandweer Echt**

- Region: `Limburg-Noord`
- Service: `Brandweer`
- Capcodes: the desired Echt capcodes

**Alle P1-meldingen**

- Priority: `P1`

## AlarmeringDroid region IDs

The integration uses the IDs returned by the AlarmeringDroid API. Important Limburg values are:

| ID | Veiligheidsregio |
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

A filtered sensor must never receive a message outside its configured region or service. For example, a message from Enschede (Twente, region 23) must not appear on a sensor configured for Limburg-Noord (region 15).

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
