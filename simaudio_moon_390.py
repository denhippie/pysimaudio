import socket
import time
from enum import Enum

TCP_PORT = 50000
BUFFER_SIZE = 4096


class SimaudioMoon390:
    def __init__(self, ip: str):
        self.ip = ip
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        print(f"connecting to {self.ip}:{TCP_PORT}")
        self.socket.connect((self.ip, TCP_PORT))

    def __del__(self):
        print("closing coneection")
        self.socket.close()

    def __send_command(self, command: bytes):
        header = b"#0"
        footer = b"\r"
        size = len(command)
        assert size < 10 # TODO: big commands not implemented yet
        message = header + str.encode(str(size)) + command + footer
        print(f"sending command: {message}")
        self.socket.send(message)
        reply = self.socket.recv(BUFFER_SIZE)
        print(f"received data: {reply}")
        return reply

    def get_status(self):
        return self.__send_command(b"01")

    class PowerState(Enum):
        toggle = b"01"
        on = b"02"
        off = b"03"

    def set_power_state(self, state: PowerState):
        command = b"60" + state.value
        return self.__send_command(command)


if __name__ == "__main__":
    TCP_IP = '192.168.178.79'
    # MESSAGE = b"#046001\r"
    moon = SimaudioMoon390(TCP_IP)
    while True:
        # moon.set_power_state(SimaudioMoon390.PowerState.toggle)
        time.sleep(10)
        moon.get_status()
