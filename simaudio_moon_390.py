import asyncio
import time
from enum import Enum

BUFFER_SIZE = 4096


class SimaudioConnection(asyncio.Protocol):
    def __init__(self, loop, message_handler):
        self.loop = loop
        self.__message_handler = message_handler
        self.transport = None

    def connection_made(self, transport):
        print('connection made')
        self.transport = transport

    def data_received(self, data):
        print(f'data received: {data}')
        for message in filter(None, data.split(b'\r')):
            self.__message_handler(message)

    def connection_lost(self, exc):
        print('connection lost, stopping event loop')
        self.loop.stop()


class SimaudioMoon390:
    def __init__(self, ip: str):
        print(f"connecting to {ip}:50000")
        self.__loop = asyncio.get_event_loop()
        self.__connection = SimaudioConnection(self.__loop, self.__on_message)
        self.__coro = self.__loop.create_connection(lambda: self.__connection, ip, 50000)
        self.loop_once()

    def __del__(self):
        print('closing coneection')
        self.__loop.close()

    def loop_once(self):
        print('loop')
        self.__loop.run_until_complete(self.__coro)

    def __on_message(self, message):
        print(f'message: {message}')
        assert(len(message) >= 5)
        assert(message[0:1] == b'#')
        # TODO: validate size?
        response_code = message[3:5]
        if response_code == b'A3':
            self.__handle_status_response(message[5:])
        else:
            print(f'unknown response message type: {response_code}')

    class Input(Enum):
        aes = b'01'
        optical = b'02'
        spdif = b'03'
        usb = b'04'
        network = b'05'
        bluetooth = b'06'
        hdmi_1 = b'07'
        hdmi_2 = b'08'
        hdmi_3 = b'09'
        hdmi_4 = b'0A'
        hdmi_arc = b'0B'
        analog = b'0C'
        balanced = b'0D'
        phono = b'0E'

    def __handle_status_response(self, parameters):
        print('received status response')
        assert(len(parameters) == 14)
        print(f'  master volume = {parameters[0:4]}')
        print(f'  balance       = {parameters[4:6]}')
        print(f'  input         = {SimaudioMoon390.Input(parameters[6:8])}')
        print(f'  sample rate   = {parameters[8:10]}')
        print(f'  unit state    = {parameters[10:12]}')
        print(f'  mind state    = {parameters[12:14]}')

    def __send_command(self, command: bytes):
        header = b"#0"
        footer = b"\r"
        size = len(command)
        assert size < 10 # TODO: big commands not implemented yet
        message = header + str.encode(str(size)) + command + footer
        print(f'sending command: {message}')
        self.__connection.transport.write(message)

    def get_status(self):
        self.__send_command(b"01")

    class PowerState(Enum):
        toggle = b"01"
        on = b"02"
        off = b"03"

    def set_power_state(self, state: PowerState):
        command = b"60" + state.value
        self.__send_command(command)


if __name__ == '__main__':
    TCP_IP = '192.168.178.79'
    moon = SimaudioMoon390(TCP_IP)
    time.sleep(1)
    while True:
        # moon.set_power_state(SimaudioMoon390.PowerState.toggle)
        moon.get_status()
        for i in range(9):
            moon.loop_once()
            time.sleep(1)

