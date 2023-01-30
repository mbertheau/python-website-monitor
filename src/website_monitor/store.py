import argparse
import collections
import datetime
import logging

import psycopg
from kafka import KafkaConsumer, TopicPartition

import website_monitor.kafka
import website_monitor.serde

log = logging.getLogger(__name__)


def get_next_offsets(conn, reset_tables=False):
    next_offsets = collections.defaultdict(lambda: 0)

    with conn.cursor() as cur:
        if reset_tables:
            cur.execute("DROP TABLE IF EXISTS kafka_partition_offsets")

        cur.execute(
            "CREATE TABLE IF NOT EXISTS kafka_partition_offsets (partition INT PRIMARY KEY, next_offset INT)"
        )

        cur.execute("SELECT partition, next_offset FROM kafka_partition_offsets")
        for partition, next_offset in cur.fetchall():
            next_offsets[partition] = next_offset

        for partition, next_offset in next_offsets.items():
            log.info(
                f"First offset in partition {partition} we want to receive: {next_offset}"
            )

    return next_offsets


def create_website_checks_table(conn, reset_tables=False):
    with conn.cursor() as cur:
        if reset_tables:
            cur.execute("DROP TABLE IF EXISTS website_checks")
            log.warning("Reset website_checks table.")

        cur.execute(
            "CREATE TABLE IF NOT EXISTS website_checks"
            "(url TEXT, request_time TIMESTAMP WITH TIME ZONE NULL,"
            " error TEXT NULL,"
            " status_code INT NULL,"
            " response_regexp TEXT NULL, response_matches_regexp BOOLEAN NULL,"
            " response_duration FLOAT NULL)"
        )
        conn.commit()


def init_kafka_consumer(kafka_auth_file_name, topic, next_offsets):
    log.info("Connecting to Kafka...")
    consumer = KafkaConsumer(
        key_deserializer=bytes.decode,
        value_deserializer=website_monitor.serde.deserialize,
        **website_monitor.kafka.auth(kafka_auth_file_name),
    )

    log.info(f"Opening topic {topic}...")

    partitions = {
        TopicPartition(topic, p) for p in consumer.partitions_for_topic(topic)
    }
    consumer.assign(partitions)

    beginning_offsets = consumer.beginning_offsets(partitions)

    for partition, first_available_offset in beginning_offsets.items():

        next_offset = next_offsets[partition]

        if first_available_offset <= next_offset:
            consumer.seek(partition, next_offset)
            log.info(
                f"Seeking to offset {next_offset} in partition {partition.partition}."
            )
        else:
            log.warning(
                f"Offset {next_offset} is not available in partition {partition.partition} anymore."
            )
            log.warning(
                f"Resuming at first available offset: {first_available_offset}."
            )
            consumer.seek_to_beginning(partition)

    return consumer


def persist_website_checks(conn, partition, website_checks, this_offset):
    if website_checks:
        with conn.cursor() as cur:

            copy_from_stdin_statement = (
                "COPY website_checks (url, request_time, error, status_code, "
                "response_regexp, response_matches_regexp, response_duration) FROM STDIN"
            )
            with cur.copy(copy_from_stdin_statement) as copy:
                for website_check in website_checks:
                    copy.write_row(website_check)

            cur.execute(
                "INSERT INTO kafka_partition_offsets (partition, next_offset) VALUES (%s, %s) "
                "ON CONFLICT (partition) DO UPDATE SET next_offset = EXCLUDED.next_offset",
                (partition.partition, this_offset + 1),
            )
            conn.commit()

            log.info(
                f"Committed {len(website_checks)} new records, "
                f"next offset {this_offset + 1} in partition {partition.partition}."
            )


def main(args):
    log.info("Connecting to PostgreSQL...")
    with psycopg.connect(args.dsn) as conn:

        log.info("Making sure the website_checks table exists...")
        create_website_checks_table(conn, args.reset_tables)

        next_offsets = get_next_offsets(conn, args.reset_tables)

        consumer = init_kafka_consumer(
            args.kafka_auth_file_name, args.topic, next_offsets
        )

        log.info(f"Starting to consume messages from Kafka topic {args.topic}.")
        while True:
            for partition, records in consumer.poll(timeout_ms=1000).items():
                website_checks = []

                for record in records:

                    website_check = record.value

                    request_time = None
                    if "request_time" in website_check:
                        request_time = datetime.datetime.fromtimestamp(
                            website_check.get("request_time"), datetime.timezone.utc
                        )

                    website_checks.append(
                        (
                            record.key,
                            request_time,
                            website_check.get("error"),
                            website_check.get("status_code"),
                            website_check.get("response_regexp"),
                            website_check.get("response_matches_regexp"),
                            website_check.get("response_duration"),
                        )
                    )

                    this_offset = record.offset

                persist_website_checks(conn, partition, website_checks, this_offset)


def cli_entry():
    try:
        parser = argparse.ArgumentParser(
            description="Collect website availability checks from a Kafka topic and store them in PostgreSQL.",
        )

        parser.add_argument("--verbose", "-v", action="count", default=0)
        parser.add_argument("--reset-tables", action="store_true")
        parser.add_argument("--kafka-auth-file-name", default="kafka-auth.json")
        parser.add_argument("--topic", default="website-checks")
        parser.add_argument("--dsn", default="postgres:///website_monitor")

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

        main(args)
    except KeyboardInterrupt:
        pass
