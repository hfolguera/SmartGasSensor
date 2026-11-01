#!/usr/bin/env python3
"""Relay TCP opcional per a proves amb Podman si la VM no arriba al broker LAN.

Només escolta loopback per defecte. No interpreta MQTT ni registra credencials.
La sessió MQTT i l'autenticació continuen sent entre l'app i el broker original.
"""
import argparse
import asyncio
import logging

log = logging.getLogger("smartgas.relay")


async def relay(reader, writer, target_host, target_port):
    upstream = None
    try:
        remote, upstream = await asyncio.wait_for(asyncio.open_connection(target_host, target_port), 5)

        async def forward(source, destination):
            try:
                while chunk := await source.read(65536):
                    destination.write(chunk)
                    await destination.drain()
            finally:
                destination.close()

        await asyncio.gather(forward(reader, upstream), forward(remote, writer))
    except (TimeoutError, OSError):
        log.warning("No es pot completar una connexió amb el broker")
    finally:
        writer.close()
        if upstream is not None:
            upstream.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass


async def main(args):
    server = await asyncio.start_server(
        lambda reader, writer: relay(reader, writer, args.target_host, args.target_port),
        host="127.0.0.1", port=args.listen_port,
    )
    log.info("Relay actiu a 127.0.0.1:%s cap a %s:%s", args.listen_port, args.target_host, args.target_port)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-host", default="192.168.1.216")
    parser.add_argument("--target-port", type=int, default=1883)
    parser.add_argument("--listen-port", type=int, default=28883)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        asyncio.run(main(parser.parse_args()))
    except KeyboardInterrupt:
        pass
