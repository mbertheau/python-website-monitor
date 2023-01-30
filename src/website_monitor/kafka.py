import json

import aiokafka.helpers


def auth(kafka_auth_file_name):
    kafka_auth = json.load(open(kafka_auth_file_name))

    ssl_context = aiokafka.helpers.create_ssl_context(
        cafile=kafka_auth.pop("ssl_cafile"),
        certfile=kafka_auth.pop("ssl_certfile"),
        keyfile=kafka_auth.pop("ssl_keyfile"),
    )

    return {**kafka_auth, "ssl_context": ssl_context}
