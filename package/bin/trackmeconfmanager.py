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

# Built-in modules
import json
import logging
import os
import sys
import time
from logging.handlers import RotatingFileHandler

# Third-party modules
import requests
import urllib3
from urllib3.exceptions import InsecureRequestWarning

# Disable insecure request warnings for urllib3
urllib3.disable_warnings(InsecureRequestWarning)

# set splunkhome
splunkhome = os.environ['SPLUNK_HOME']

# set logging
filehandler = RotatingFileHandler('%s/var/log/splunk/ta_trackme_conf_manager_trackmeconfmanager.log' % splunkhome, mode='a', maxBytes=10000000, backupCount=1)
formatter = logging.Formatter('%(asctime)s %(levelname)s %(filename)s %(funcName)s %(lineno)d %(message)s')
logging.Formatter.converter = time.gmtime
filehandler.setFormatter(formatter)
log = logging.getLogger()  # root logger - Good to get it only once.
for hdlr in log.handlers[:]:  # remove the existing file handlers
    if isinstance(hdlr,logging.FileHandler):
        log.removeHandler(hdlr)
log.addHandler(filehandler)      # set the new handler
# set the log level to INFO, DEBUG as the default is ERROR
log.setLevel(logging.INFO)

# append libs
sys.path.append(os.path.join(splunkhome, 'etc', 'apps', 'TA-trackme-conf-manager', 'lib'))

# Splunk libs
from splunklib.searchcommands import dispatch, GeneratingCommand, Configuration, Option, validators
import splunklib.client as client

# Import trackme libs
from trackme_conf_manager_libs import trackme_reqinfo

@Configuration(distributed=False)

class TrackMeConfManager(GeneratingCommand):

    mode = Option(
        doc='''
        **Syntax:** **mode=****
        **Description:** The execution mode, valid options are: live|simulation|get.''',
        require=False, default='simulation', validate=validators.Match("mode", r"^(live|simulation|get)$"))

    def generate(self, **kwargs):

        # Start performance counter
        start = time.time()

        # Get request info and set logging level
        reqinfo = trackme_reqinfo(self._metadata.searchinfo.session_key, self._metadata.searchinfo.splunkd_uri)
        log.setLevel(logging.getLevelName(reqinfo['logging_level']))

        # Get config
        conf_manager_role = reqinfo['trackme_general']['conf_manager_role']

        # Data collection
        collection_name = "kv_trackme_conf_manager"
        collection = self.service.kvstore[collection_name]

        # get all records
        get_collection_start = time.time()
        collection_records = []
        collection_records_keys = set()

        end = False
        skip_tracker = 0
        while end == False:

            process_collection_records = collection.data.query(skip=skip_tracker)
            if len(process_collection_records) != 0:
                for item in process_collection_records:
                    if item.get('_key') not in collection_records_keys:
                        collection_records.append(item)
                        collection_records_keys.add(item.get('_key'))    
                skip_tracker+= 5000
            else:
                end = True

        logging.info(f"context=\"perf\", get collection records, no_records=\"{len(collection_records)}\", run_time=\"{round((time.time() - get_collection_start), 3)}\", collection=\"{collection_name}\"")

        # create a list of all keys for fast verification
        collection_keys = []

        # create a dictionary
        collection_dict = {}

        for collection_record in collection_records:
            collection_keys.append(collection_record.get('_key'))
            collection_dict[collection_record.get('_key')] = collection_record

        logging.debug(f"collection_dict=\"{json.dumps(collection_dict, indent=2)}\"")

        # Set the journal file target
        journal_file = None
        journal_filename = 'trackme_conf_manager_transactions_journal.json'
        local_dir = os.path.join(splunkhome, 'etc', 'apps', 'TA-trackme-conf-manager', 'local')

        if os.path.isfile(os.path.join(local_dir, journal_filename)):
            journal_file = os.path.join(local_dir, journal_filename)
            logging.debug(f'using journal found at path=\"{journal_file}\"')
        elif os.path.isfile(os.path.join('default', journal_filename)):
            journal_file = os.path.join('default', journal_filename)
            logging.debug(f'using journal found at path=\"{journal_file}\"')
        else:
            journal_file = None

        # open the journal
        data = None
        if journal_file:
            with open(journal_file, 'r+') as f:
                try:
                    f.seek(0)  # Go to the beginning of file to read
                    data = json.load(f)
                except Exception as e:
                    msg = f'failed to open the transaction for reading, exception=\"{str(e)}\"'
                    logging.error(msg)
                    raise Exception(msg)
                
                if self.mode == 'get':

                    # yield
                    yield {
                        'time': time.time(),
                        '_raw': data,
                    }

                else:
                
                    for transaction in data:

                        logging.info(f'verifying transaction status, transaction_md5=\"{transaction}\"')

                        # process the transaction if needed
                        transaction_runops = False

                        if transaction not in collection_keys:
                            logging.info('transaction was not found in the KVstore and will be processed in live mode')
                            transaction_runops = True
                        else:
                            logging.info('transaction was found in the KVstore and processed already')

                        # get
                        transaction_request = data[transaction].get('transaction_request')
                        transaction_http_mode = data[transaction].get('transaction_http_mode')
                        transaction_http_service = data[transaction].get('transaction_http_service')
                        transaction_ctime = data[transaction].get('ctime')

                        # set info
                        transaction_info = None

                        if conf_manager_role != 'producer':
                            transaction_info = 'running in receiver mode, no actions will be performed'
                        else:
                            if self.mode == 'simulation' and transaction_runops:
                                transaction_info = 'This transaction has not been processed yet and will be processed accordingly if submitted in live mode'
                            else:
                                transaction_info = 'This transaction has been processed already, the Conf Manager will not attempt to process it again'

                        #
                        # Process transactions
                        #                        

                        if conf_manager_role == 'receiver' and self.mode == 'live' and transaction_runops:

                            url = f'{self._metadata.searchinfo.splunkd_uri}/{transaction_http_service}'
                            header = {
                                'Authorization': f'Splunk {self._metadata.searchinfo.session_key}',
                                'Content-Type': 'application/json',
                                }
                            if transaction_http_mode == 'get':
                                response = requests.get(url, headers=header, verify=False, data=json.dumps(transaction_request))
                            elif transaction_http_mode == 'post':
                                response = requests.post(url, headers=header, verify=False, data=json.dumps(transaction_request))
                            elif transaction_http_mode == 'delete':
                                response = requests.delete(url, headers=header, verify=False, data=json.dumps(transaction_request))

                            if response.status_code in (200, 201, 204):
                                msg = f'the transaction was processed successfully, HTTP returned code: {response.status_code}'
                                logging.debug(f"Success transaction call, data=\"{response}\"")
                                transaction_info = f'the transaction was processed successfully, HTTP returned code: {response.status_code}'

                                # register in the KVstore
                                try:
                                    collection.data.insert(json.dumps({
                                        "_key": transaction,
                                        "transaction_md5": transaction,
                                        "transaction_http_mode": transaction_http_mode,
                                        "transaction_http_service": transaction_http_service,
                                        "transaction_request": json.dumps(transaction_request, indent=2),
                                        "transaction_mtime": time.time(),
                                        "transaction_status": 'done',
                                    }))
                                    logging.info(f'transaction was registered successfully to the KVstore with key=\"{transaction}\"')
                                except Exception as e:
                                    logging.error(f'transaction could not be registered to the KVstore, exception=\"{e}\"')

                            else:
                                msg = f"Failed to perform the transaction, status_code={response.status_code}, response_text=\"{response.text}\""
                                transaction_info = f'the transaction has failed, status_code={response.status_code}, response_text=\"{response.text}\"'

                        # yield
                        yield {
                            'time': time.time(),
                            '_raw': {
                                'transaction': transaction,
                                'transaction_runops': transaction_runops,
                                'transaction_http_mode': transaction_http_mode,
                                'transaction_http_service': transaction_http_service,
                                'transaction_request': transaction_request,
                                'transaction_ctime': transaction_ctime,
                                'transaction_info': transaction_info,                         
                                },
                        }

        else:

            # yield
            yield {
                'time': time.time(),
                '_raw': {'response': 'There are no transactions to be read yet, TrackMe Configuration Manager is not ready.'},
            }

        # Log the run time
        logging.info(f"trackmeconfmanager has terminated, run_time={round(time.time() - start, 3)}")

dispatch(TrackMeConfManager, sys.argv, sys.stdin, sys.stdout, __name__)
