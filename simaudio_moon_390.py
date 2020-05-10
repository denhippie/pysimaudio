import asyncio
import time
from enum import Enum

BUFFER_SIZE = 4096


class SimaudioConnection(asyncio.Protocol):
    def __init__(self, loop):
        self.loop = loop
        self.transport = None

    def connection_made(self, transport):
        print('connection made')
        self.transport = transport

    def data_received(self, data):
        print(f'data received: {data}')

    def connection_lost(self, exc):
        print('connection lost, stopping event loop')
        self.loop.stop()

class SimaudioMoon390:
    def __init__(self, ip: str):
        print(f"connecting to {ip}:50000")
        self.__loop = asyncio.get_event_loop()
        self.__connection = SimaudioConnection(self.__loop)
        self.__coro = self.__loop.create_connection(lambda: self.__connection, ip, 50000)
        self.loop_once()

    def __del__(self):
        print('closing coneection')
        self.__loop.close()

    def loop_once(self):
        print('loop')
        self.__loop.run_until_complete(self.__coro)

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

