import json
import logger

from contextlib import contextmanager

from mqtt import MqttMessage
from workers.base import BaseWorker

_LOGGER = logger.get(__name__)

REQUIREMENTS = ["bluepy"]

class Lywsd03MmcWorker(BaseWorker):
    def _setup(self):
        _LOGGER.info("Adding %d %s devices", len(self.devices), repr(self))
        _LOGGER.debug("me passive: %s", self.passive)

        for name, mac in self.devices.items():
            _LOGGER.info("Adding %s device '%s' (%s)", repr(self), name, mac)
            self.devices[name] = lywsd03mmc(mac, command_timeout=self.command_timeout, passive=self.passive)

    def find_device(self, mac):
        for name, device in self.devices.items():
            if device.mac == mac:
                return device
        return

    def status_update(self):
        from bluepy import btle

        if self.passive:
            _LOGGER.debug("status_update. Scanning...")
            scanner = btle.Scanner()
            results = scanner.scan(self.scan_timeout if hasattr(self, 'scan_timeout') else 20.0, passive=True)

            _LOGGER.debug("scan results for device %s", results)
            for res in results:
                device = self.find_device(res.addr)
                scanData = res.getScanData()
                _LOGGER.debug("device with addr %s (%s). data: %s", res.addr, device, scanData)
                if device:
                    for (adtype, desc, value) in scanData:
                        if ("1a18" in value):
                            device.processScanValue(value)
                        else:
                            # seems like original Mi format is totally different (or encrypted)
                            _LOGGER.debug("%s - unknown scan data %s", res.addr, value)                            

        for name, lywsd03mmc in self.devices.items():
            #try:
            ret = lywsd03mmc.readAll()
            #except btle.BTLEDisconnectError as e:
            #    self.log_connect_exception(_LOGGER, name, e)
            #except btle.BTLEException as e:
            #    self.log_unspecified_exception(_LOGGER, name, e)
            #else:
            yield [MqttMessage(topic=self.format_topic(name), payload=json.dumps(ret))]


class lywsd03mmc:
    def __init__(self, mac, command_timeout=30, passive=False):
        self.mac = mac
        self.passive = passive
        self.command_timeout = command_timeout

        self._temperature = None
        self._humidity = None
        self._battery = None

    @contextmanager
    def connected(self):
        from bluepy import btle

        _LOGGER.debug("%s - connected (passive: %s)", self.mac, self.passive)
        device = btle.Peripheral()
        device.connect(self.mac)
        device.writeCharacteristic(0x0038, b'\x01\x00', True)
        device.writeCharacteristic(0x0046, b'\xf4\x01\x00', True)
        yield device

    def readAll(self):
        if self.passive:
            temperature = self.getTemperature()
            humidity = self.getHumidity()
            battery = self.getBattery()
        else:
            with self.connected() as device:
                self.getData(device)
                temperature = self.getTemperature()
                humidity = self.getHumidity()
                battery = self.getBattery()

        if temperature and humidity and battery:
            _LOGGER.debug("%s - found values %f, %d, %d", self.mac, temperature, humidity, battery)
        else:
            _LOGGER.debug("%s - no data received", self.mac)

        return {
            "temperature": temperature,
            "humidity": humidity,
            "battery": battery,
        }

    def getData(self, device):
        self.subscribe(device)
        while True:
            if device.waitForNotifications(self.command_timeout):
                break
        return self._temperature, self._humidity, self._battery

    def getTemperature(self):
        return self._temperature

    def getHumidity(self):
        return self._humidity

    def getBattery(self):
        return self._battery

    def subscribe(self, device):
        device.setDelegate(self)

    def processScanValue(self, data):
        # mac: a4:c1:38:d6:1e:75
        # mac is reversed - custom format
        # 1a18 751ed638c1a4 1c09e111da0a40eb04
        # 1a18751ed638c1a41a09ae11da0a404604
        # int.from_bytes(bindata[8:10], byteorder='little', signed=True) / 100
        # mac is not reversed - ATC format
        # 1a18a4c138d61e7500e92d400adc44
        # int.from_bytes(bindata[8:10], byteorder='big', signed=True) / 10
        packetMac = data[4:16]
        isMacReversed = packetMac[-2:] == self.mac[0:2]

        _LOGGER.debug('packet length: %d isMacReversed: %s packetMac: %s data: %s', len(data), isMacReversed, packetMac, data)

        if isMacReversed:
            return self.processCustomScanValue(data)
        else:
            return self.processATCScanValue(data)

        _LOGGER.warn('unrecognized scan value format. length: %d', len(data))
    
    def processLegacyScanValue(self, data):
        _LOGGER.debug("handle legacy format %s", data)
        
        temperature = int(data[16:20], 16) / 10
        humidity = int(data[20:22], 16)
        battery = int(data[22:24], 16)
        battery_v = 0

        self._temperature = round(temperature, 1)
        self._humidity = round(humidity)
        self._battery = round(battery, 4)

        _LOGGER.debug("[%s temp: %f, hum: %d, bat: %d bat_v: %d]", self.mac, temperature, humidity, battery, battery_v)

    def processCustomScanValue(self, data):
        _LOGGER.debug("handle custom format %s", data)
        
        bindata = bytearray.fromhex(data)
        temperature = int.from_bytes(bindata[8:10], byteorder='little', signed=True) / 100
        humidity = int.from_bytes(bindata[10:12], byteorder='little', signed=False) / 100
        battery_v = int.from_bytes(bindata[12:14], byteorder='little', signed=False)
        battery = int.from_bytes(bindata[14:15], byteorder='little', signed=False)

        self._temperature = round(temperature, 1)
        self._humidity = round(humidity)
        self._battery = round(battery, 4)

        _LOGGER.debug("[%s temp: %f, hum: %d, bat: %d bat_v: %d]", self.mac, temperature, humidity, battery, battery_v)
    
    def processATCScanValue(self, data):
        _LOGGER.debug("handle ATC format %s", data)
        
        bindata = bytearray.fromhex(data)
        temperature = int.from_bytes(bindata[8:10], byteorder='big', signed=True) / 10
        humidity = int.from_bytes(bindata[10:11], byteorder='big')
        battery_v = int.from_bytes(bindata[11:13], byteorder='big') 
        battery = int.from_bytes(bindata[13:15], byteorder='big')

        self._temperature = round(temperature, 1)
        self._humidity = round(humidity)
        self._battery = round(battery, 4)

        _LOGGER.debug("[%s temp: %f, hum: %d, bat: %d bat_v: %d]", self.mac, temperature, humidity, battery, battery_v)