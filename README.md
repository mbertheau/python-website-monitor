python-website-monitor
======================

[![CI](https://github.com/mbertheau/python-website-monitor/actions/workflows/python.yaml/badge.svg)](https://github.com/mbertheau/python-website-monitor/actions/workflows/python.yaml)
[![image](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

python-website-monitor monitors a list of websites and streams the results
through kafka into a PostgreSQL database.

python-website-monitor is available under the [MIT License](https://github.com/mbertheau/python-website-monitor/blob/main/LICENSE.md).

Installation
------------

``` sh
git clone https://github.com/mbertheau/python-website-monitor.git
cd python-website-monitor
pip install .
```

Monitoring websites
-------------------

Create a file `websites.csv` with the following content:

``` csv
url,response_regexp
https://python.org
https://clojure.org,Rich.+Hickey
```

Create a file `kafka-auth.json` with the following content:

``` json
{
    "bootstrap_servers": "kafka-host:9092",
    "security_protocol": "SSL",
    "ssl_cafile": "ca.pem",
    "ssl_certfile": "service.cert",
    "ssl_keyfile": "service.key"
}
```

Create a topic `website-checks` on the Kafka instance.

Then start the sensor:

``` sh
website-monitor-sense websites.csv --topic website-checks --kafka-auth-file-name kafka-auth.json
```

This will stream website checking results to the kafka topic, keyed by the URL.

Streaming results to PostgreSQL
-------------------------------

Create the same file `kafka-auth.json` as described above.

Then start the store process:

``` sh
website-monitor-store --dsn 'postgres:///website-checks' --topic website-checks --kafka-auth-file-name kafka-auth.json
```

Development
-----------

[pyenv](https://github.com/mbertheau/python-website-monitor.git) is a nice way
to manage your Python environment. Here's how to use pyenv:

``` sh
git clone https://github.com/mbertheau/python-website-monitor.git
cd python-website-monitor
pyenv virtualenv 3.11 website-monitor-3.11
echo website-monitor-3.11 > .python-version
pip install --upgrade pip
pip install --requirement dev/requirements-dev.txt
pip install --editable .
```

Run tests with:

``` sh
pytest

```

Copyright (c) 2023 Markus Bertheau
