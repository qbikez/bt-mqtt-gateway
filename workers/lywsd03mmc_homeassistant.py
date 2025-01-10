from exceptions import DeviceTimeoutError
from mqtt import MqttMessage, MqttConfigMessage, MqttClient

from interruptingcow import timeout
from workers.base import BaseWorker
from workers.lywsd03mmc import lywsd03mmc
import logger
import json
import time
from contextlib import contextmanager

REQUIREMENTS = ["bluepy"]

ATTR_BATTERY = "battery"
ATTR_LOW_BATTERY = 'low_battery'

monitoredAttrs = ["temperature", "humidity", ATTR_BATTERY]
_LOGGER = logger.get(__name__)

class Lywsd03Mmc_HomeassistantWorker(BaseWorker):
    """
    This worker for the Lywsd03Mmc creates the sensor entries in
    MQTT for Home Assistant. It also creates a binary sensor for
    low batteries. It supports connection retries.
    """
    def _setup(self):
        _LOGGER.info("Adding %d %s devices", len(self.devices), repr(self))
        _LOGGER.debug("Lywsd03Mmc_HomeassistantWorker [passive:%s scan_timeout:%d prefix: %s]", self.passive, self.scan_timeout, self.name_prefix if hasattr(self, 'name_prefix') else repr(self))
        for name, mac in self.devices.items():
            _LOGGER.debug("Adding %s device '%s' (%s)", repr(self), name, mac)
            self.devices[name] = lywsd03mmc(mac, command_timeout=self.command_timeout, passive=self.passive)

    def config(self, availability_topic):
        ret = []
        for name, device in self.devices.items():
            ret += self.config_device(name, device.mac)
        return ret

    def find_device(self, mac):
        for name, device in self.devices.items():
            if device.mac == mac:
                return name, device
        return None, None

    def format_discovery_topic(self, mac, *sensor_args):
        node_id = mac.replace(":", "-")
        object_id = "_".join([self.name_prefix if hasattr(self, 'name_prefix') else repr(self), *sensor_args])
        return "{}/{}".format(node_id, object_id)

    def format_discovery_name(self, *sensor_args):
        return "_".join([self.name_prefix if hasattr(self, 'name_prefix') else repr(self), *sensor_args])

    def config_device(self, name, mac):
        ret = []
        device = {
            "identifiers": [mac, self.format_discovery_id(mac, name)],
            "manufacturer": "Xiaomi",
            "model": "Mijia Lywsd03Mmc",
            "name": self.format_discovery_name(name),
        }

        for attr in monitoredAttrs:
            payload = {
                "unique_id": self.format_discovery_id(mac, name, attr),
                "state_topic": self.format_prefixed_topic(name, attr),
                "name": self.format_discovery_name(name, attr),
                "force_update": "true",
                "expire_after": "300",
                "device": device,
                "source": self.source
            }

            if attr == "humidity":
                payload.update({"icon": "mdi:water", "unit_of_measurement": "%"})
            elif attr == "temperature":
                payload.update(
                    {"device_class": "temperature", "unit_of_measurement": "°C"}
                )
            elif attr == ATTR_BATTERY:
                payload.update({"device_class": "battery", "unit_of_measurement": "V"})

            ret.append(
                MqttConfigMessage(
                    MqttConfigMessage.SENSOR,
                    self.format_discovery_topic(mac, name, attr),
                    payload=payload,
                )
            )

        ret.append(
            MqttConfigMessage(
                MqttConfigMessage.BINARY_SENSOR,
                self.format_discovery_topic(mac, name, ATTR_LOW_BATTERY),
                payload={
                    "unique_id": self.format_discovery_id(mac, name, ATTR_LOW_BATTERY),
                    "state_topic": self.format_prefixed_topic(name, ATTR_LOW_BATTERY),
                    "name": self.format_discovery_name(name, ATTR_LOW_BATTERY),
                    "device": device,
                    "device_class": "battery",
                },
            )
        )

        return ret

    def status_update(self):
        from bluepy import btle
        # _LOGGER.info("Updating %d %s devices", len(self.devices), repr(self))

        if self.passive:
            scan_timeout = self.scan_timeout if hasattr(self, 'scan_timeout') else 20.0
            _LOGGER.debug("scanning... (timeout: %d)", scan_timeout)
            scanner = btle.Scanner()
            results = scanner.scan(self.scan_timeout if hasattr(self, 'scan_timeout') else 20.0, passive=True)

            for res in results:
                name, device = self.find_device(res.addr)
                if device:
                    try:
                        scanData = res.getScanData()
                        _LOGGER.debug("device with addr %s (%s). data: %s", res.addr, device, scanData)
                        
                        for (adtype, desc, value) in scanData:
                            if ("1a18" in value):
                                _LOGGER.debug("%s - received scan data %s", res.addr, value)
                                device.processScanValue(value)
                                # only send state update if the state was actually read - prevent sending stale values to MQTT
                                with timeout(self.command_timeout, exception=DeviceTimeoutError):
                                    yield self.update_device_state(name, device)
                            else:
                                _LOGGER.debug("%s - unknown scan data %s", res.addr, value) 
                    except btle.BTLEException as e:
                                logger.log_exception(
                                    _LOGGER,
                                    "Error during update of %s device '%s' (%s): %s",
                                    repr(self),
                                    name,
                                    device.mac,
                                    type(e).__name__,
                                    suppress=True,
                                )
                    except DeviceTimeoutError:
                        logger.log_exception(
                            _LOGGER,
                            "Time out during update of %s device '%s' (%s)",
                            repr(self),
                            name,
                            device.mac,
                            suppress=True,
                        )     
                else:
                    _LOGGER.debug("device %s not found", res.addr)
        else:
            for name, device in self.devices.items():
                # _LOGGER.debug("Updating %s device '%s' (%s)", repr(self), name, device.mac)
                # from btlewrap import BluetoothBackendException

                try:
                    with timeout(self.command_timeout, exception=DeviceTimeoutError):
                        yield self.update_device_state(name, device)
                except btle.BTLEException as e:
                    logger.log_exception(
                        _LOGGER,
                        "Error during update of %s device '%s' (%s): %s",
                        repr(self),
                        name,
                        device.mac,
                        type(e).__name__,
                        suppress=True,
                    )
                except DeviceTimeoutError:
                    logger.log_exception(
                        _LOGGER,
                        "Time out during update of %s device '%s' (%s)",
                        repr(self),
                        name,
                        device.mac,
                        suppress=True,
                    )

    def update_device_state(self, name, device):
        ret = []
        if device.readAll() is None:
            return ret

        for attr in monitoredAttrs:
            attrValue = None
            if attr == "humidity":
                attrValue = device.getHumidity()
            elif attr == "temperature":
                attrValue = device.getTemperature()
            elif attr == ATTR_BATTERY:
                attrValue = device.getBattery()

            ret.append(
                MqttMessage(
                    topic=self.format_topic(name, attr),
                    payload=attrValue,
                )
            )

        battery = device.getBattery()
        # Low battery binary sensor
        ret.append(
            MqttMessage(
                topic=self.format_topic(name, ATTR_LOW_BATTERY),
                payload= self.true_false_to_ha_on_off(battery < 3 if battery != None else False),
            )
        )

        ret.append(
            MqttMessage(
                topic=self.format_topic(name, "source"),
                payload=self.source,
            )
        )
        
        return ret
