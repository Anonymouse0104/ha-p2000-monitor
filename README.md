# P2000 Monitor for Home Assistant

Home Assistant integration for live Dutch P2000 emergency alerts using the structured **AlarmeringDroid API**.

## v0.6.1

This release defines a clean recommended sensor setup for Home Assistant while keeping every sensor independently configurable through the Config Flow.

### Core behaviour

- The integration fetches the complete AlarmeringDroid feed and applies configured filters locally.
- **P2000 Monitor** can run without any filters and therefore shows all regions and all supported emergency services.
- Config Flow sensors can independently filter by region, discipline, priority, capcode and text.
- Capcode filtering checks **all capcodes in an alert**, not only the first capcode.
- A Config Flow sensor does not apply its filter twice.
- The newest matching message is selected by publication timestamp.
- Correct Dutch Veiligheidsregio names are used, including **Limburg-Noord** and **Zuid-Limburg**.
- The integration includes an `icon.svg` logo.
- No `sensor.yaml` configuration is required for Config Flow sensors.

## Recommended sensor setup

The integration does **not** hard-code these sensors. Create them yourself through **Settings → Devices & services → P2000 Monitor → Dienst toevoegen**. This makes the integration reusable for other Home Assistant users while giving each user complete control over their own filters.

| Sensor | Configuration |
|---|---|
| **P2000 Monitor** | **No filters**. Enable **Toon alle meldingen**. Leave region, discipline, priority and capcodes empty. |
| **P2000 Test** | Regions **Limburg-Noord** and **Zuid-Limburg** + discipline **Brandweer**. No capcode filter. |
| **Brandweer Echt** | **Capcode filter only**. Do not select a region or discipline filter. |
| **Brandweer Limburg-Noord** | Region **Limburg-Noord** + discipline **Brandweer**. |
| **Brandweer Zuid-Limburg** | Region **Zuid-Limburg** + discipline **Brandweer**. |
| **Brandweer Limburg** | **Do not create this sensor**. It is intentionally replaced by the two separate regional sensors above. |

### Why this layout?

The sensors deliberately test different filtering layers:

1. **P2000 Monitor** is the reference feed. If a message is not here, it was not received by this integration.
2. **P2000 Test** tests the combination of two regions and the Brandweer discipline.
3. **Brandweer Echt** tests capcode matching independently from the region/discipline filter.
4. **Brandweer Limburg-Noord** tests region + Brandweer.
5. **Brandweer Zuid-Limburg** tests region + Brandweer.

This separation makes troubleshooting deterministic instead of mixing several filter mechanisms into one sensor.

## Configure a sensor

Every Config Flow entry is an independent sensor. A different user can install the integration and select completely different filters.

Available filters:

- **Toon alle meldingen** — disables filtering and shows the complete national feed.
- **Veiligheidsregio's** — one or more regions.
- **Diensten** — one or more emergency services.
- **Prioriteiten** — P1 through P5.
- **Capcodes** — one or more capcodes; any matching capcode in the alert is sufficient.
- **Tekst moet bevatten** — one or more terms.
- **Tekst mag niet bevatten** — one or more terms.
- **Incident-koppeltijd** — controls when follow-up alarms are grouped into the same incident.

Multiple values inside one filter use **OR** logic. Different filter categories use **AND** logic.

Capcodes and text values can be separated with commas or semicolons.

### Important: P2000 Monitor has no filters

For the national monitor:

- Name: `P2000 Monitor`
- **Toon alle meldingen: ON**
- Regions: empty
- Diensten: empty
- Priorities: empty
- Capcodes: empty
- Include text: empty
- Exclude text: empty

This is intentionally different from **P2000 Test**.

## Clean installation / migration

For an existing installation that has gone through several development versions, a clean reinstall is recommended before testing v0.6.1. This prevents old Config Entries, stale entity registry entries and legacy `sensor.yaml` filters from obscuring whether the new integration is working correctly.

### Recommended clean-install procedure

1. **Back up Home Assistant.**
2. Open **Settings → Devices & services**.
3. Open every **P2000 Monitor** Config Entry.
4. Delete the old P2000 Monitor Config Entries and their entities. This includes the old `P2000 Test`, `Brandweer Echt`, `Brandweer Limburg`, `Brandweer Limburg-Noord`, `Brandweer Zuid-Limburg` and any duplicate P2000 entries.
5. Remove the old P2000 filter block from `sensor.yaml`. Do not leave the old YAML filters active alongside the Config Flow integration.
6. Restart Home Assistant once so the old entities and YAML sensors are gone.
7. In HACS, update **P2000 Monitor** to **v0.6.1**. If HACS shows the custom integration as removable and you want a completely clean filesystem install, remove it first, restart, then reinstall it from the same repository.
8. Restart Home Assistant again after installation.
9. Add **P2000 Monitor** through **Settings → Devices & services → Add Integration**.
10. Create the five sensors from the table above.
11. Do **not** create `Brandweer Limburg`.
12. Leave `sensor.yaml` completely out of the new P2000 setup.
13. Wait for a real P2000 message and compare the five sensors.

### Do I really need to remove the current integration?

**For your installation: yes, I recommend it.** We have tested several development releases and have already had legacy YAML filters and multiple Config Flow entries active at the same time. Keeping those around makes a failed test ambiguous.

A clean install gives us a hard baseline:

```text
AlarmeringDroid
      ↓
P2000 Monitor — everything
      ↓
┌───────────────┬────────────────┬──────────────────┐
P2000 Test      Brandweer Echt  Regional sensors
VRLN + VRZL     capcodes only   VRLN / VRZL + BW
+ Brandweer
```

If **P2000 Monitor** receives a message but one of the filtered sensors does not, the problem is in the filter logic. If **P2000 Monitor** itself does not receive it, we investigate the API/coordinator instead. That distinction is the key to debugging the next version.

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

## Legacy YAML

Legacy YAML support remains for compatibility, but **new installations should use Config Flow**. Do not combine a legacy P2000 YAML sensor with the Config Flow sensors when testing the current integration.

## Data source

The integration uses the structured AlarmeringDroid API.

## License

MIT.
