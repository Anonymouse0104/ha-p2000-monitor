<p align="center"><img src="logo.png" alt="P2000 Monitor" width="330"></p>

# P2000 Monitor for Home Assistant

Live Dutch P2000 emergency alerts in Home Assistant, from the structured **AlarmeringDroid API**. Like [P2Kflex](https://monitor.p2kflex.nl/), you decide which alerts you follow: create as many sensors as you want, each with its own safety regions, services, priorities, capcodes and text filters.

## Features

- **Your own sensors** — add the integration once, then use **Add sensor** for every view you want: all of the Netherlands, your own safety region, the fire brigade in two regions, or one station by capcode.
- **One poll for everything** — the national feed is fetched once every 10 seconds, however many sensors you have.
- **No missed or duplicate alerts** — every alert is deduplicated on its AlarmeringDroid id and fires exactly one event.
- **History per sensor** — each sensor keeps the last 100 matching alerts, also across restarts, so a quiet region still shows its latest alert.
- **Incidents** — follow-up alarms with similar text in the same region are grouped into one incident.
- **Events for automations** — `p2000_monitor_new_message`, `p2000_monitor_filter_match`, `p2000_monitor_new_incident` and `p2000_monitor_incident_update`.

## Installation

1. In **HACS**, open the menu (⋮) → **Custom repositories**, add `https://github.com/Anonymouse0104/ha-p2000-monitor` as type **Integration**.
2. Install **P2000 Monitor** and restart Home Assistant.
3. Go to **Settings → Devices & services → Add integration → P2000 Monitor** and set up your first sensor.

Requires Home Assistant 2025.6 or newer.

## Adding sensors

Open **Settings → Devices & services → P2000 Monitor** and click **Add sensor**. Every sensor becomes its own entity (`sensor.<name>`). Use the ⋮ menu next to a sensor to change or delete it.

| Filter | Meaning |
|---|---|
| **Show all alerts** | All of the Netherlands; ignores region, service, priority and capcode filters (text filters still apply). |
| **Safety regions** | One or more of the 25 Veiligheidsregio's. |
| **Services** | Politie, Brandweer, Ambulance, KNRM, Lifeliner, DARES. |
| **Priorities** | P1 to P5 (as written in the alert text). |
| **Capcodes** | Comma separated; an alert matches if any of its capcodes matches. |
| **Text must contain / must not contain** | Comma separated terms. |
| **Incident grouping window** | How long follow-up alerts join the same incident. |

Values **within** one filter are **OR**, different filters are **AND**. Empty filters are ignored.

### Examples

| Sensor | Settings |
|---|---|
| **P2000 Nederland** | Show all alerts |
| **Brandweer Limburg** | Regions *Limburg-Noord* + *Zuid-Limburg*, service *Brandweer* |
| **Ambulance Utrecht** | Region *Utrecht*, service *Ambulance* |
| **Kazerne Echt** | Capcodes `1005258, 1005264` |
| **Zeer urgent** | Priority *P1* |

## Upgrading from 0.7 or older

Before 0.8, every sensor was a separate integration entry. On the first start of 0.8 these are merged automatically into one **P2000 Monitor** entry with one sensor per old entry. Entity IDs, history and stored alerts are kept, so dashboards and automations keep working.

## AlarmeringDroid region IDs

The integration uses the IDs returned by the AlarmeringDroid API.

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

A filtered sensor must never receive a message outside its configured region or service. For example, a message from Enschede (Twente, region 23) must not appear on **Brandweer Limburg-Noord** (region 15).

Useful debug messages are written under the `p2000_monitor` logger.

## Events

The feed is polled once for all sensors. Each message is deduplicated on its AlarmeringDroid id.

| Event | Fired | Data |
|---|---|---|
| `p2000_monitor_new_message` | once per new message, for the whole feed | `message` |
| `p2000_monitor_filter_match` | once per new message per sensor whose filters accept it | `message`, `filter`, `entry_id`, `subentry_id`, `sensor` |
| `p2000_monitor_new_incident` / `p2000_monitor_incident_update` | per sensor | `incident`, `message`, `entry_id`, `subentry_id`, `sensor` |

Use `p2000_monitor_filter_match` with a `sensor` condition (for example `sensor.brandweer_limburg`) for automations on one sensor. `p2000_monitor_new_message` has no `sensor` field.

The large attributes `meldingen`, `incidenten`, `incident_history`, `incident` and `capcodes` are available in the state machine but are not stored by the recorder.

## Legacy YAML

YAML configuration (`sensor: - platform: p2000_monitor`) is no longer supported. Remove it from `configuration.yaml`, then delete the orphaned `p2000_monitor_main` / `..._legacy_N` entities under **Settings → Entities**.

## Data source

Alerts come from the AlarmeringDroid API. Its feed only holds the latest ~30 national alerts, which is why every sensor keeps its own history.

## License

MIT.
