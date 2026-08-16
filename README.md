# P2000 Monitor for Home Assistant

A Home Assistant integration for live Dutch P2000 emergency alerts, with a focus on reliable event processing, incident grouping and easy configuration.

## What's new in 0.5.0

Version 0.5.0 replaces the old P2KFlex HTML scraper with the structured AlarmeringDroid API. This removes the dependency on parsing the P2KFlex mobile website and its changing HTML/JavaScript markup.

The integration now has a normal Home Assistant **Config Flow**, so a new user can install it through HACS and configure their own P2000 sensor entirely from the Home Assistant UI. No YAML is required for a new installation.

## Features

- Live P2000 alerts from the structured AlarmeringDroid API
- Configurable from **Settings → Devices & services → Add integration**
- Multiple P2000 sensors can be created, each with its own filters
- Filter by safety region
- Filter by service: police, fire brigade, ambulance, KNRM, Lifeliner or DARES
- Filter by priority P1–P5
- Filter by capcode
- Include text keywords
- Exclude text keywords
- Incident grouping and incident history
- Stable message IDs and duplicate protection
- Persistent incident history across Home Assistant restarts
- Home Assistant events for new messages, filter matches and incidents
- Existing YAML configuration remains supported

## Installation

### HACS

1. Open HACS.
2. Search for **P2000 Monitor**.
3. Install the integration.
4. Restart Home Assistant.
5. Go to **Settings → Devices & services**.
6. Click **Add integration** and search for **P2000 Monitor**.

### Manual installation

Copy:

```text
custom_components/p2000_monitor/
```

to:

```text
/config/custom_components/p2000_monitor/
```

Then restart Home Assistant.

## Configure your own sensor

After installation, use the Home Assistant config flow.

You can create, for example:

### Brandweer Zuid-Limburg

- Name: `Brandweer Zuid-Limburg`
- Region: `Limburg-Zuid`
- Service: `Brandweer`

### Brandweer Echt

- Name: `Brandweer Echt`
- Region: `Limburg-Zuid`
- Service: `Brandweer`
- Text contains: `Echt`

### Alle P1-meldingen

- Name: `P2000 P1`
- Priority: `P1`

### Een specifieke capcode

- Name: `Mijn kazerne`
- Capcodes: `100xxxx`

You can create as many separately configured sensors as you need.

Filters can be changed later through the integration's **Configure** option. A restart is not required when changing the filters; the entry is automatically reloaded.

## Filter behaviour

All selected filters are combined with **AND** logic between filter categories.

For text filters:

- `Tekst moet bevatten`: all entered keywords must occur in the message.
- `Tekst mag niet bevatten`: none of the entered keywords may occur.
- Multiple values can be separated with commas or semicolons.

For example:

```text
Tekst moet bevatten: Echt, woning
```

only matches messages containing both `Echt` and `woning`.

## Safety regions

The integration uses the region numbering used by the AlarmeringDroid API. Examples:

| ID | Safety region |
|---:|---|
| 1 | Amsterdam-Amstelland |
| 2 | Groningen |
| 3 | Noord- en Oost-Gelderland |
| 4 | Zaanstreek-Waterland |
| 5 | Hollands Midden |
| 6 | Brabant-Noord |
| 7 | Friesland |
| 8 | Gelderland-Midden |
| 9 | Kennemerland |
| 10 | Rotterdam-Rijnmond |
| 11 | Brabant-Zuidoost |
| 12 | Drenthe |
| 13 | Gelderland-Zuid |
| 14 | Zuid-Holland-Zuid |
| 15 | Limburg-Noord |
| 17 | IJsselland |
| 18 | Utrecht |
| 19 | Gooi en Vechtstreek |
| 20 | Zeeland |
| 21 | Limburg-Zuid |
| 23 | Twente |
| 24 | Noord-Holland-Noord |
| 25 | Haaglanden |
| 26 | Midden- en West-Brabant |
| 27 | Flevoland |

## Services

| ID | Service |
|---:|---|
| 1 | Politie |
| 2 | Brandweer |
| 3 | Ambulance |
| 4 | KNRM |
| 5 | Lifeliner |
| 7 | DARES |

## Sensor attributes

The configured sensor exposes the latest matching alert and useful incident information, including:

- `message`
- `melding`
- `tekstmelding`
- `published`
- `regio`
- `regio_name`
- `discipline`
- `dienstid`
- `city`
- `capcode`
- `latitude`
- `longitude`
- `incident_count`
- `incident`
- `incident_id`
- `incident_tijd`
- `incidenten`
- `incident_history`
- `meldingen`

## Events

The integration provides Home Assistant events for automations:

```text
p2000_monitor_new_message
p2000_monitor_filter_match
p2000_monitor_new_incident
p2000_monitor_incident_update
```

Example:

```yaml
triggers:
  - trigger: event
    event_type: p2000_monitor_filter_match
```

## Existing YAML installations

The legacy YAML platform remains supported for backwards compatibility. Existing installations can continue using their current configuration while new users should use the Config Flow.

## Data source

P2000 Monitor 0.5.0 uses the structured AlarmeringDroid API rather than scraping the P2KFlex website. AlarmeringDroid describes its service as a structured P2000 data source and its live monitor supports filtering by service, region and priority.

The integration polls the API approximately every 10 seconds by default.

Please respect the data provider's usage policy and do not create excessive polling or multiple unnecessary installations.

## Troubleshooting

### No alerts appear

Check:

1. The integration is installed and Home Assistant has been restarted after installation.
2. The configured region/service/capcode filters are correct.
3. The message is actually present in the P2000 data source.
4. Home Assistant logs for `p2000_monitor` contain API errors.

### I changed a filter

Open the integration and choose **Configure**. The integration reloads the sensor automatically after saving.

### Old P2KFlex HTML problems

Version 0.5.0 no longer depends on the P2KFlex HTML parser. Problems caused by strings such as `<span class="TIP` or JavaScript attributes from the P2KFlex webpage therefore no longer affect the primary data path.

## Development

The project is intentionally split into:

```text
Config Flow
    ↓
Sensor platform
    ↓
Coordinator
    ↓
AlarmeringDroid API
    ↓
P2000 events
    ↓
Incident engine
```

This keeps source retrieval separate from filtering, sensor presentation and incident grouping.

## License

MIT.
