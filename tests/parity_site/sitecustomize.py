"""The parity lane's DNS blackhole: a Python process started by its suite run cannot look up a network host, so a test
that reaches the network fails at once instead of depending on the runner's network; loopback names still resolve."""
import socket


def loopback(host):
    name = (host.decode() if isinstance(host, bytes) else host or '').lower().rstrip('.')
    return name in ('', 'localhost', '::1') or name.startswith('127.') or name.endswith('.localhost')


def refusing(lookup):
    def checked(host, *args, **kwargs):
        if not loopback(host):
            raise socket.gaierror(socket.EAI_NONAME, f'parity lane refuses the network lookup of {host}')
        return lookup(host, *args, **kwargs)
    return checked


for name in ('getaddrinfo', 'gethostbyname', 'gethostbyname_ex'):
    setattr(socket, name, refusing(getattr(socket, name)))
