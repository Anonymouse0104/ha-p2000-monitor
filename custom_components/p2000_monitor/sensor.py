"""Sensor platform for P2000 Monitor."""
from __future__ import annotations
from typing import Any
import voluptuous as vol
from homeassistant.components.sensor import PLATFORM_SCHEMA, SensorEntity
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import ATTR_TEST_CAPCODES, ATTR_TEST_DISCIPLINE, ATTR_TEST_MESSAGE, ATTR_TEST_REGION, ATTR_TEST_REGION_NAME, DEFAULT_INCIDENT_WINDOW, DOMAIN, EVENT_FILTER_MATCH, EVENT_INCIDENT_UPDATE, EVENT_NEW_INCIDENT, EVENT_NEW_MESSAGE, MAX_FILTER_INCIDENT_HISTORY, MAX_HISTORY, SERVICE_INJECT_TEST_INCIDENT
from .coordinator import P2000DataCoordinator, REGIONS, SERVICES, extract_priority

DEFAULT_NAME="P2000 Monitor"
CONF_FILTERS="filters"; CONF_EXCLUDE_CAPCODES="exclude_capcodes"; CONF_INCIDENT_WINDOW="incident_window"; CONF_REGIONS="regions"; CONF_DISCIPLINES="disciplines"; CONF_PRIORITIES="priorities"; CONF_CAPCODES="capcodes"; CONF_INCLUDE_TEXT="include_text"; CONF_EXCLUDE_TEXT="exclude_text"
FILTER_SCHEMA=vol.Schema({vol.Required(CONF_NAME):cv.string,vol.Optional(CONF_REGIONS,default=[]):vol.All(cv.ensure_list,[cv.string]),vol.Optional(CONF_DISCIPLINES,default=[]):vol.All(cv.ensure_list,[cv.string]),vol.Optional(CONF_PRIORITIES,default=[]):vol.All(cv.ensure_list,[cv.string]),vol.Optional(CONF_CAPCODES,default=[]):vol.All(cv.ensure_list,[cv.string]),vol.Optional(CONF_INCLUDE_TEXT,default=[]):vol.All(cv.ensure_list,[cv.string]),vol.Optional(CONF_EXCLUDE_TEXT,default=[]):vol.All(cv.ensure_list,[cv.string])})
PLATFORM_SCHEMA=PLATFORM_SCHEMA.extend({vol.Optional(CONF_NAME,default=DEFAULT_NAME):cv.string,vol.Optional(CONF_EXCLUDE_CAPCODES,default=[]):vol.All(cv.ensure_list,[cv.string]),vol.Optional(CONF_INCIDENT_WINDOW,default=DEFAULT_INCIDENT_WINDOW):vol.All(vol.Coerce(int),vol.Range(min=60,max=1800)),vol.Optional(CONF_FILTERS,default=[]):vol.All(cv.ensure_list,[FILTER_SCHEMA])})

def _filter_matches(message:dict[str,Any],config:dict[str,Any])->bool:
    regions={str(x) for x in config.get(CONF_REGIONS,[])}; disciplines={str(x) for x in config.get(CONF_DISCIPLINES,[])}; priorities={str(x).upper().replace(" ","") for x in config.get(CONF_PRIORITIES,[])}; capcodes={str(x) for x in config.get(CONF_CAPCODES,[])}
    include=[str(x).lower() for x in config.get(CONF_INCLUDE_TEXT,[]) if str(x).strip()]; exclude=[str(x).lower() for x in config.get(CONF_EXCLUDE_TEXT,[]) if str(x).strip()]
    if regions and str(message.get("regio","")) not in regions:return False
    if disciplines:
        wanted={SERVICES.get(x,x).lower() for x in disciplines}
        if str(message.get("discipline","")).lower() not in wanted:return False
    if priorities:
        priority=extract_priority(message.get("message",""))
        if not priority or priority.upper() not in priorities:return False
    if capcodes and str(message.get("capcode","")) not in capcodes:return False
    lower=str(message.get("message","")).lower()
    if include and not all(x in lower for x in include):return False
    if exclude and any(x in lower for x in exclude):return False
    return True

async def _build_coordinator(hass:HomeAssistant,incident_window:int,exclude_capcodes:list[str],api_filter:dict[str,Any]|None=None)->P2000DataCoordinator:
    coordinator=P2000DataCoordinator(hass=hass,exclude_capcodes=exclude_capcodes,incident_window=incident_window,api_filter=api_filter or {})
    await coordinator.async_config_entry_first_refresh(); return coordinator

async def async_setup_entry(hass:HomeAssistant,entry,async_add_entities:AddEntitiesCallback)->None:
    data=dict(entry.data)
    config={CONF_NAME:data.get(CONF_NAME,entry.title),CONF_REGIONS:data.get(CONF_REGIONS,[]),CONF_DISCIPLINES:data.get(CONF_DISCIPLINES,[]),CONF_PRIORITIES:data.get(CONF_PRIORITIES,[]),CONF_CAPCODES:data.get(CONF_CAPCODES,[]),CONF_INCLUDE_TEXT:data.get(CONF_INCLUDE_TEXT,[]),CONF_EXCLUDE_TEXT:data.get(CONF_EXCLUDE_TEXT,[])}
    api_filter={"regios":config[CONF_REGIONS],"diensten":config[CONF_DISCIPLINES],"capcodes":config[CONF_CAPCODES],"priorities":config[CONF_PRIORITIES],"include_text":config[CONF_INCLUDE_TEXT],"exclude_text":config[CONF_EXCLUDE_TEXT]}
    coordinator=await _build_coordinator(hass,int(data.get(CONF_INCIDENT_WINDOW,DEFAULT_INCIDENT_WINDOW)),[],api_filter)
    entity_key=entry.unique_id or entry.entry_id
    async_add_entities([P2000FilterSensor(coordinator,config,entity_key)],True)

async def async_setup_platform(hass:HomeAssistant,config:ConfigType,async_add_entities:AddEntitiesCallback,discovery_info:DiscoveryInfoType|None=None)->None:
    name=config.get(CONF_NAME,DEFAULT_NAME); filters=config.get(CONF_FILTERS,[]); exclude_capcodes=config.get(CONF_EXCLUDE_CAPCODES,[]); incident_window=config.get(CONF_INCIDENT_WINDOW,DEFAULT_INCIDENT_WINDOW)
    coordinator=await _build_coordinator(hass,incident_window,exclude_capcodes,{})
    if not hass.services.has_service(DOMAIN,SERVICE_INJECT_TEST_INCIDENT):
        async def handle_test_incident(call):
            region=str(call.data.get(ATTR_TEST_REGION,"21")); region_name=call.data.get(ATTR_TEST_REGION_NAME,REGIONS.get(region,"Zuid-Limburg")); discipline=call.data.get(ATTR_TEST_DISCIPLINE,"Brandweerdiensten"); message=call.data.get(ATTR_TEST_MESSAGE,"P 1 BR woning Teststraat Maastricht 243231"); capcodes=call.data.get(ATTR_TEST_CAPCODES,["1005258","1005264","1005998"])
            if isinstance(capcodes,str):capcodes=[capcodes]
            from datetime import datetime,timezone
            now=datetime.now(timezone.utc)
            messages=[{"message":message,"capcode":str(capcode),"regio":region,"regio_name":region_name,"discipline":discipline,"dienstid":"2" if discipline=="Brandweerdiensten" else "","latitude":"50.8514","longitude":"5.6909","published":f"TEST-PENDING-{now.isoformat()}-{index}","test":True} for index,capcode in enumerate(capcodes)]
            await coordinator.async_inject_test_messages(messages)
        hass.services.async_register(DOMAIN,SERVICE_INJECT_TEST_INCIDENT,handle_test_incident)
    entities=[P2000MonitorSensor(coordinator,name)]
    for index,filter_config in enumerate(filters):entities.append(P2000FilterSensor(coordinator,filter_config,f"legacy_{index}"))
    async_add_entities(entities,True)

class P2000BaseSensor(CoordinatorEntity,SensorEntity):
    def _data(self):return self.coordinator.data or {}
    @staticmethod
    def _icon_for(message):return {"Brandweerdiensten":"mdi:fire-truck","Ambulancediensten":"mdi:ambulance","Politiediensten":"mdi:car-emergency","Lifeliner":"mdi:helicopter"}.get(message.get("discipline"),"mdi:radio-tower")

class P2000MonitorSensor(P2000BaseSensor):
    def __init__(self,coordinator,name):
        super().__init__(coordinator); self._attr_name=name; self._attr_unique_id="p2000_monitor_main"
    @property
    def native_value(self):
        latest=self._data().get("latest"); return latest.get("message","Geen meldingen") if latest else "Geen meldingen"
    @property
    def icon(self):return self._icon_for(self._data().get("latest") or {})
    @property
    def extra_state_attributes(self):
        data=self._data(); latest=data.get("latest") or {}
        return {"source":latest.get("source","alarmeringdroid"),"region":latest.get("regio_name",""),"region_id":latest.get("regio",""),"discipline":latest.get("discipline",""),"dienstid":latest.get("dienstid",""),"city":latest.get("city",""),"published":latest.get("published",""),"message_id":latest.get("message_id",""),"incident_count":len(data.get("incidents") or []),"new_message_count":len(data.get("new_messages") or [])}

class P2000FilterSensor(P2000BaseSensor):
    def __init__(self,coordinator,config,entity_key):
        super().__init__(coordinator); self._config=config; self._entity_key=entity_key; self._attr_name=config.get(CONF_NAME,"P2000"); self._attr_unique_id=f"p2000_monitor_{entity_key}"
    def _matching_messages(self):
        # Use the current API result, not only new_messages, so a sensor is populated after startup.
        return [m for m in list(self._data().get("messages") or []) if _filter_matches(m,self._config)]
    def _matching_incidents(self):
        return [i for i in self._data().get("incidents",[]) if _filter_matches(i,self._config)][:MAX_FILTER_INCIDENT_HISTORY]
    @property
    def native_value(self):
        messages=self._matching_messages(); return messages[0].get("message","Geen meldingen") if messages else "Geen meldingen"
    @property
    def icon(self):
        messages=self._matching_messages(); return self._icon_for(messages[0] if messages else {})
    @property
    def extra_state_attributes(self):
        messages=self._matching_messages(); latest=messages[0] if messages else {}; data=self._data(); incidents=self._matching_incidents()
        return {"source":latest.get("source","alarmeringdroid"),"message":latest.get("message",""),"melding":latest.get("melding",""),"tekstmelding":latest.get("tekstmelding",""),"published":latest.get("published",""),"regio":latest.get("regio",""),"regio_name":latest.get("regio_name",""),"discipline":latest.get("discipline",""),"dienstid":latest.get("dienstid",""),"city":latest.get("city",""),"capcode":latest.get("capcode",""),"latitude":latest.get("latitude",""),"longitude":latest.get("longitude",""),"incident_count":len(incidents),"incident":incidents[0] if incidents else None,"incident_id":incidents[0].get("incident_id") if incidents else None,"incident_tijd":incidents[0].get("first_seen") if incidents else None,"incidenten":incidents,"incident_history":incidents,"meldingen":messages[:MAX_HISTORY],"new_message_count":len(data.get("new_messages") or [])}
    async def async_added_to_hass(self):
        await super().async_added_to_hass(); self.async_on_remove(self.coordinator.async_add_listener(self._coordinator_updated))
    def _coordinator_updated(self):
        data=self._data()
        for message in data.get("new_messages",[]):
            if _filter_matches(message,self._config):
                self.hass.bus.async_fire(EVENT_NEW_MESSAGE,{"message":message,"sensor":self.entity_id})
                self.hass.bus.async_fire(EVENT_FILTER_MATCH,{"message":message,"filter":self._config,"sensor":self.entity_id})
        for change in data.get("incident_changes",[]):
            incident=change.get("incident",{})
            if _filter_matches(incident,self._config):
                event=EVENT_NEW_INCIDENT if change.get("action")=="new" else EVENT_INCIDENT_UPDATE
                self.hass.bus.async_fire(event,{"incident":incident,"message":change.get("message",{}),"sensor":self.entity_id})
