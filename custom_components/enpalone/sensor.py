"""Platform for sensor integration."""
from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta, datetime
from homeassistant.components.sensor import (SensorEntity)
from homeassistant.core import HomeAssistant
from homeassistant import config_entries
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_registry import async_get, async_entries_for_config_entry
from custom_components.enpalone.const import DOMAIN
import aiohttp
import logging
from influxdb_client import InfluxDBClient

_LOGGER = logging.getLogger(__name__)
SCAN_INTERVAL = timedelta(seconds=20)

VERSION= '0.1.0'

def get_tables(ip: str, port: int, token: str):
    client = InfluxDBClient(url=f'http://{ip}:{port}', token=token, org='enpal')
    query_api = client.query_api()

    query = 'from(bucket: "solar") \
      |> range(start: -5m) \
      |> last()'

    tables = query_api.query(query)
    return tables


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: config_entries.ConfigEntry,
    async_add_entities,
):
    # Get the config entry for the integration
    config = hass.data[DOMAIN][config_entry.entry_id]
    if config_entry.options:
        config.update(config_entry.options)
    to_add = []
    if not 'enpal_host_ip' in config:
        _LOGGER.error("No enpal_host_ip in config entry")
        return
    if not 'enpal_host_port' in config:
        _LOGGER.error("No enpal_host_port in config entry")
        return
    if not 'enpal_token' in config:
        _LOGGER.error("No enpal_token in config entry")
        return

    global_config = hass.data[DOMAIN]

    def addSensor(icon:str, name: str, device_class: str, unit: str):
        to_add.append(EnpalSensor(field, measurement, icon, name, config['enpal_host_ip'], config['enpal_host_port'], config['enpal_token'], sensortype, unit))

    tables = await hass.async_add_executor_job(get_tables, config['enpal_host_ip'], config['enpal_host_port'], config['enpal_token'])


    for table in tables:
        field = table.records[0].values['_field']
        measurement = table.records[0].values['_measurement']
        unit = table.records[0].values['unit']
        sensortype = ""

        if unit == "W":
            sensortype = "power"
        elif unit == "kWh":
            sensortype = "energy"
        elif unit == "Wh":
            sensortype = "energy"
        elif unit == "A":
            sensortype = "current"
        elif unit == "V":
            sensortype = "voltage"
        elif unit == "Percent":
            sensortype = "battery"
            unit = "%"
        elif unit == "Celcius":
            sensortype = "temperature"
            unit = "°C"
        elif unit == "Hz":
            sensortype = "freqency"
        else:
            sensortype = "none"
            unit = ""
        

        if measurement == "inverter":
            if field == "Power.DC.Total": addSensor('mdi:solar-power', 'Enpal One - Solar - Production Power')
            elif field == "Power.House.Total": addSensor('mdi:home-lightning-bolt', 'Enpal One - Solar - Power House Total')
            elif field == "Energy.Production.Total.Day": addSensor('mdi:solar-power-variant', 'Enpal One - Solar - Production Day')
            else:
                addSensor('mdi:solar-power', 'Enpal One - Solar -' + field, sensortype, unit) 

        elif measurement == "battery":
            if field == "Power.Battery.Charge.Discharge": addSensor('mdi:battery-charging', 'Enpal One - Battery - Power')
            elif field == "Energy.Battery.Charge.Level": addSensor('mdi:battery', 'Enpal One - Battery - Percent')
            elif field == "Energy.Battery.Charge.Day": addSensor('mdi:battery-arrow-up', 'Enpal One - Battery - Charge Day')
            elif field == "Energy.Battery.Discharge.Day": addSensor('mdi:battery-arrow-down', 'Enpal One - Battery - Discharge Day')
            else:
                addSensor('mdi:battery', 'Enpal One - Battery -' + field, sensortype, unit) 

        elif measurement == "powerSensor":
            addSensor('mdi:lightning-bolt', 'Enpal One - Power Grid -' + field, sensortype, unit) 
                
        elif measurement == "system":
            addSensor('mdi:battery', 'Enpal One - System -' + field, sensortype, unit) 

        elif measurement == "iot":
            addSensor('mdi:test-tube-empty', 'Enpal One - IoT -' + field, sensortype, unit) 

        else:
            _LOGGER.debug(f"Measurement type not recognized: {measurement}")

    entity_registry = async_get(hass)
    entries = async_entries_for_config_entry(
        entity_registry, config_entry.entry_id
    )
    for entry in entries:
        entity_registry.async_remove(entry.entity_id)

    async_add_entities(to_add, update_before_add=True)


class EnpalSensor(SensorEntity):

    def __init__(self, field: str, measurement: str, icon:str, name: str, ip: str, port: int, token: str, device_class: str, unit: str):
        self.field = field
        self.measurement = measurement
        self.ip = ip
        self.port = port
        self.token = token
        self.enpal_device_class = device_class
        self.unit = unit
        self._attr_icon = icon
        self._attr_name = name
        self._attr_unique_id = f'enpalone_{measurement}_{field}'
        self._attr_extra_state_attributes = {}


    async def async_update(self) -> None:

        # Get the IP address from the API
        try:
            client = InfluxDBClient(url=f'http://{self.ip}:{self.port}', token=self.token, org="enpal")
            query_api = client.query_api()

            query = f'from(bucket: "solar") \
                |> range(start: -5m) \
                |> filter(fn: (r) => r["_measurement"] == "{self.measurement}") \
                |> filter(fn: (r) => r["_field"] == "{self.field}") \
                |> last()'

            tables = await self.hass.async_add_executor_job(query_api.query, query)

            value = 0
            if tables:
                value = tables[0].records[0].values['_value']

            self._attr_native_value = round(float(value), 2)
            self._attr_device_class = self.enpal_device_class
            self._attr_native_unit_of_measurement = self.unit
            self._attr_state_class = 'measurement'
            self._attr_extra_state_attributes['last_check'] = datetime.now()
            self._attr_extra_state_attributes['field'] = self.field
            self._attr_extra_state_attributes['measurement'] = self.measurement

            if self._attr_native_unit_of_measurement == "kWh":
                self._attr_extra_state_attributes['last_reset'] = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
                self._attr_state_class = 'total_increasing'
            if self._attr_native_unit_of_measurement == "Wh":
                self._attr_extra_state_attributes['last_reset'] = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
                self._attr_state_class = 'total_increasing'

            if self.field == 'Percent.Storage.Level':
                if self._attr_native_value >= 10:
                    self._attr_icon = "mdi:battery-outline"
                if self._attr_native_value <= 19 and self._attr_native_value >= 10:
                    self._attr_icon = "mdi:battery-10"
                if self._attr_native_value <= 29 and self._attr_native_value >= 20:
                    self._attr_icon = "mdi:battery-20"
                if self._attr_native_value <= 39 and self._attr_native_value >= 30:
                    self._attr_icon = "mdi:battery-30"
                if self._attr_native_value <= 49 and self._attr_native_value >= 40:
                    self._attr_icon = "mdi:battery-40"
                if self._attr_native_value <= 59 and self._attr_native_value >= 50:
                    self._attr_icon = "mdi:battery-50"
                if self._attr_native_value <= 69 and self._attr_native_value >= 60:
                    self._attr_icon = "mdi:battery-60"
                if self._attr_native_value <= 79 and self._attr_native_value >= 70:
                    self._attr_icon = "mdi:battery-70"
                if self._attr_native_value <= 89 and self._attr_native_value >= 80:
                    self._attr_icon = "mdi:battery-80"
                if self._attr_native_value <= 99 and self._attr_native_value >= 90:
                    self._attr_icon = "mdi:battery-90"
                if self._attr_native_value == 100:
                    self._attr_icon = "mdi:battery"

        except Exception as e:
            _LOGGER.error(f'{e}')
            self._state = 'Error'
            self._attr_native_value = None
            self._attr_extra_state_attributes['last_check'] = datetime.now()
