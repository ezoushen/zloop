"""The parity lane's DNS blackhole: a Python process started by its suite run cannot look up a network host, so a test
that reaches the network fails at once instead of depending on the runner's network; loopback names still resolve."""
import socket

lookup = socket.getaddrinfo


def loopback(host):
    name = host.decode() if isinstance(host, bytes) else host
    return name in (None, '', 'localhost', '::1') or name.startswith('127.') or name.endswith('.localhost')


def getaddrinfo(host, *args, **kwargs):
    if not loopback(host):
        raise socket.gaierror(socket.EAI_NONAME, f'parity lane refuses the network lookup of {host}')
    return lookup(host, *args, **kwargs)


socket.getaddrinfo = getaddrinfo
