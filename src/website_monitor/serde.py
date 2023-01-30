import json


def serialize(data):
    return json.dumps(data).encode()


def deserialize(data):
    return json.loads(data.decode())
