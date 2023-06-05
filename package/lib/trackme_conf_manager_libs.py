#!/usr/bin/env python
# coding=utf-8

from __future__ import absolute_import, division, print_function, unicode_literals

__author__ = "TrackMe Limited"
__copyright__ = "Copyright 2023, TrackMe Limited, U.K."
__credits__ = "TrackMe Limited, U.K."
__license__ = "TrackMe Limited, all rights reserved"
__version__ = "0.1.0"
__maintainer__ = "TrackMe Limited, U.K."
__email__ = "support@trackme-solutions.com"
__status__ = "PRODUCTION"

# Standard library imports
import os
import sys
import re
import json
import random
import time
import datetime
import logging
from logging.handlers import RotatingFileHandler

# Networking and URL handling imports
import requests
from requests.structures import CaseInsensitiveDict
from urllib.parse import urlencode
import urllib.parse
import urllib3

# Splunk imports
import splunk
import splunk.entity

# Disable insecure request warnings for urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# splunk home
splunkhome = os.environ['SPLUNK_HOME']

# appebd lib
sys.path.append(os.path.join(splunkhome, 'etc', 'apps', 'TA-trackme-conf-manager', 'lib'))

# import Splunk libs
import splunklib.client as client
import splunklib.results as results


def trackme_reqinfo(session_key, splunkd_uri):
    """
    Retrieve request info & settings.
    """

    # Ensure splunkd_uri starts with "https://"
    if not splunkd_uri.startswith("https://"):
        splunkd_uri = f"https://{splunkd_uri}"

    # Build header and target URL
    headers = CaseInsensitiveDict()
    headers["Authorization"] = f"Splunk {session_key}"
    target_url = f"{splunkd_uri}/services/trackme_conf_manager/v1/request_info"

    # Create a requests session for better performance
    session = requests.Session()
    session.headers.update(headers)

    try:
        # Use a context manager to handle the request
        with session.get(target_url, verify=False) as response:
            if response.ok:
                logging.debug(f"Success retrieving conf, data=\"{response}\"")
                response_json = response.json()
                return response_json
            else:
                error_message = f"Failed to retrieve conf, status_code={response.status_code}, response_text=\"{response.text}\""
                logging.error(error_message)
                raise Exception(error_message)

    except Exception as e:
        error_message = f"Failed to retrieve conf, exception=\"{str(e)}\""
        logging.error(error_message)
        raise Exception(error_message)


def trackme_getloglevel(system_authtoken, splunkd_port):
    """
    Simply get and return the loglevel with elevated privileges to avoid code duplication
    """

    # Get service
    service = client.connect(
        owner="nobody",
        app="TA-trackme-conf-manager",
        port=splunkd_port,
        token=system_authtoken
    )

    # set loglevel
    loglevel = 'INFO'
    conf_file = "ta_trackme_conf_manager_settings"
    confs = service.confs[str(conf_file)]
    for stanza in confs:
        if stanza.name == 'logging':
            for stanzakey, stanzavalue in stanza.content.items():
                if stanzakey == "loglevel":
                    loglevel = stanzavalue

    return loglevel

