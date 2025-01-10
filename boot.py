from machine import SPI, Pin # type: ignore
import network # type: ignore
import webrepl


SLEEP_TIME_SECS = 2
wlan = network.WLAN(network.STA_IF)
wlan.ifconfig(('192.168.0.202','255.255.255.0','192.168.0.1','192.168.0.1'))
wlan.active(True)
if not wlan.isconnected():
    print('connecting to network...')
    wlan.connect('Gola', 'Syracus7')
    time.sleep(SLEEP_TIME_SECS)
print('network config:', wlan.ifconfig())

# Start execution
webrepl.start()
