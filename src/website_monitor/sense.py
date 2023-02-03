import argparse
import asyncio
import csv
import logging
import re
import socket
import ssl
import time
import urllib.parse

import aiokafka

import website_monitor.kafka
import website_monitor.serde

log = logging.getLogger(__name__)


# When a check takes longer than this, it fails with "error": "Timeout"
WEBSITE_CHECK_TIMEOUT_SECONDS = 10

# Checks for one website are scheduled once every this many seconds
WEBSITE_CHECK_INTERVAL_SECONDS = 20

# try to read at most this many bytes in the HTTP response body
# This also limits where the regexp is searched for
HTTP_RESPONSE_BODY_MAX_BYTES = 1024 * 1024


def read_websites(websites_csv_file_name):
    with open(websites_csv_file_name, newline="") as csvfile:
        return list(csv.DictReader(csvfile))


def prepare_website(website):
    urlinfo = urllib.parse.urlsplit(website["url"])
    # TODO: handle not well-formed `url`

    port = 80
    is_ssl = None

    if urlinfo.scheme == "https":
        port = 443
        is_ssl = True
        # TODO: heed https://docs.python.org/3/library/ssl.html#ssl-security

    website["open_connection_args"] = {
        "host": urlinfo.hostname,
        "port": urlinfo.port or port,
        "ssl": is_ssl,
    }

    path = urlinfo.path or "/"
    if urlinfo.query:
        path += "?" + urlinfo.query

    # Caveat: with this simplistic HTTP request we might get different responses
    # than a browser would get. This could lead to false positives for the
    # regexp match, or even for site availability

    # We could also use HEAD if we don't have a regexp to look for in the body,
    # but that again might lead to false positives on site availability, when
    # clever servers take a shortcut for HEAD requests, instead of running
    # the possibly broken real thing.

    request = f"GET {path} HTTP/1.0\r\nHost: {urlinfo.hostname}\r\n\r\n".encode(
        "latin-1"
    )

    website["http_request"] = request

    return website


def decode_http_response_body(body):
    decoded_body = None
    for num_broken_bytes_at_end in range(6):
        try:
            if num_broken_bytes_at_end:
                encoded_body = body[:-num_broken_bytes_at_end]
            else:
                encoded_body = body

            decoded_body = encoded_body.decode("UTF-8")
        except UnicodeDecodeError:
            continue
        else:
            break

    if decoded_body is None:
        decoded_body = body.decode("latin-1")

    return decoded_body


class CheckWebsiteError(Exception):
    pass


async def open_connection(website):
    try:
        # Caveat: dual-stack hosts
        reader, writer = await asyncio.open_connection(
            **website["open_connection_args"]
        )
    except socket.gaierror as e:
        raise CheckWebsiteError("DNS lookup failed") from e
    except ssl.SSLError as e:
        raise CheckWebsiteError("SSL handshake failed") from e
    except OSError as e:
        raise CheckWebsiteError("Connection failed") from e

    return reader, writer


async def read_status_and_headers(reader, ret):
    # get HTTP status
    line = await reader.readline()
    if not line:
        ret["error"] = "Malformed HTTP/1.0 response"
        return ret

    try:
        ret["status_code"] = int(line.decode("latin-1").split()[1])
    except (IndexError, ValueError):
        ret["error"] = "Malformed HTTP/1.0 response"
        return ret

    while True:
        line = await reader.readline()
        if not line:
            # There should be an empty line _before_ the end of the GET response.
            ret["error"] = "Malformed HTTP/1.0 response"
            return ret

        if line == b"\r\n":
            # \r\n marks the end of the header section and the start of the body in a HTTP response.
            break

    return ret


def check_regexp(website, body, ret):
    response_regexp = website.get("response_regexp")
    if response_regexp:
        ret["response_regexp"] = response_regexp

        if not body:
            ret["response_matches_regexp"] = False
            return ret

        match = True
        if not re.search(response_regexp, decode_http_response_body(body)):
            match = False

        ret["response_matches_regexp"] = match

    return ret


async def check_website(website):
    # Caveat: start_time might be inaccurate - only God knows when the request
    # actually goes out to the network.
    start_time = time.monotonic()

    ret = {"request_time": time.time()}

    try:
        reader, writer = await open_connection(website)

        writer.write(website["http_request"])

        ret = await read_status_and_headers(reader, ret)

        # read body for later regexp search
        body = await reader.read(HTTP_RESPONSE_BODY_MAX_BYTES)

        # Caveat: I interpret response_time to mean the time until 1 MiB of the HTTP
        # body has arrived. Other interpretations are possible.
        end_time = time.monotonic()

        # exhaust the reader to avoid errors when closing the SSL connection
        await reader.read(-1)

        writer.close()
        await writer.wait_closed()

        ret["response_duration"] = end_time - start_time

        return check_regexp(website, body, ret)

    except CheckWebsiteError as e:
        ret["error"] = str(e)
        return ret


async def website_check_task(website, producer, topic):
    log.info(f"Periodically checking {website['url']}")
    try:
        while True:
            log.info(f"Checking {website['url']}")

            check_time = time.time()
            check_time_mono = time.monotonic()

            try:
                async with asyncio.timeout(WEBSITE_CHECK_TIMEOUT_SECONDS):
                    result = await check_website(website)

            except asyncio.TimeoutError:
                result = {"request_time": check_time, "error": "Timeout"}

            log.info(f"Result for {website['url']}: {result}")

            await producer.send_and_wait(topic, key=website["url"], value=result)

            sleep_time = (
                check_time_mono + WEBSITE_CHECK_INTERVAL_SECONDS - time.monotonic()
            )
            if sleep_time > 0:
                log.info(f"Waiting {sleep_time} seconds for {website['url']}")
                await asyncio.sleep(sleep_time)

    except asyncio.CancelledError:
        log.info(f"Stopped checking {website['url']}")
        raise


async def main(args):
    producer = aiokafka.AIOKafkaProducer(
        key_serializer=str.encode,
        value_serializer=website_monitor.serde.serialize,
        **website_monitor.kafka.auth(args.kafka_auth_file_name),
    )
    await producer.start()

    try:
        async with asyncio.TaskGroup() as tg:
            for website in read_websites(args.websites_csv_file_name):
                tg.create_task(
                    website_check_task(prepare_website(website), producer, args.topic)
                )

    finally:
        await producer.stop()


def cli_entry():
    try:
        parser = argparse.ArgumentParser(
            description="Monitor website availability and report results to a Kafka topic",
        )

        parser.add_argument("websites_csv_file_name")
        parser.add_argument("--verbose", "-v", action="count", default=0)
        parser.add_argument("--kafka-auth-file-name", default="kafka-auth.json")
        parser.add_argument("--topic", default="website-checks")

        args = parser.parse_args()

        if args.verbose >= 3:
            log_level = logging.DEBUG
        elif args.verbose == 2:
            log_level = logging.INFO
        elif args.verbose == 1:
            log_level = logging.WARNING
        else:
            log_level = logging.ERROR

        logging.basicConfig(level=log_level)

        asyncio.run(main(args))
    except KeyboardInterrupt:
        pass
