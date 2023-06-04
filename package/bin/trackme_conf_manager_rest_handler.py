from __future__ import absolute_import, division, print_function, unicode_literals

__name__ = "trackme_conf_manager_rest_handler.py"
__author__ = "TrackMe Limited"
__copyright__ = "Copyright 2023, TrackMe Limited, U.K."
__credits__ = "TrackMe Limited, U.K."
__license__ = "TrackMe Limited, all rights reserved"
__version__ = "0.1.0"
__maintainer__ = "TrackMe Limited, U.K."
__email__ = "support@trackme-solutions.com"
__status__ = "PRODUCTION"

# Built-in libraries
import json
import logging
import os
import sys
import time
from collections import OrderedDict
from logging.handlers import RotatingFileHandler

# Third-party libraries
import requests
import hashlib

# splunk home
splunkhome = os.environ['SPLUNK_HOME']

# set logging
logger = logging.getLogger(__name__)
filehandler = RotatingFileHandler('%s/var/log/splunk/ta_trackme_conf_manager.log' % splunkhome, mode='a', maxBytes=10000000, backupCount=1)
formatter = logging.Formatter('%(asctime)s %(levelname)s %(filename)s %(funcName)s %(lineno)d %(message)s')
logging.Formatter.converter = time.gmtime
filehandler.setFormatter(formatter)
log = logging.getLogger()
for hdlr in log.handlers[:]:
    if isinstance(hdlr,logging.FileHandler):
        log.removeHandler(hdlr)
log.addHandler(filehandler)
log.setLevel(logging.INFO)

# append lib
sys.path.append(os.path.join(splunkhome, 'etc', 'apps', 'TA-trackme-conf-manager', 'lib'))

# import test handler
import trackme_rest_handler

# import Splunk libs
import splunklib.client as client
import splunklib.results as results


class TrackMeConfManager_v1(trackme_rest_handler.RESTHandler):

    
    def __init__(self, command_line, command_arg):
        super(TrackMeConfManager_v1, self).__init__(command_line, command_arg, logger)


    def get_check_conf(self, request_info, **kwargs):

        return {
            "payload": 'Welcome to TrackMe config manager',
            'status': 200
        }


    # Conf manager
    def post_conf_manager_receiver(self, request_info, **kwargs):
        '''
        receives transactions to be replayed by the conf manager
        '''
        describe = False

        # Retrieve from data
        try:
            resp_dict = json.loads(str(request_info.raw_args['payload']))
        except Exception as e:
            resp_dict = None

        if resp_dict is not None:
            try:
                describe = resp_dict['describe']
                if describe in ("true", "True"):
                    describe = True
            except Exception as e:
                describe = False

                # get data
                transaction_request = resp_dict.get('transaction_request', None)
                transaction_http_mode = resp_dict.get('transaction_http_mode', None)
                transaction_http_service = resp_dict.get('transaction_http_service', None)

                # check request
                if not transaction_request or not transaction_http_mode or not transaction_http_service:
                    return {
                        "payload": {'response': 'transaction_request, transaction_http_mode and transaction_http_service must be provided in the request'},
                        'status': 500
                    }

        else:
            return {
                "payload": {'response': 'there were no transactions received, this endpoints expects a JSON body to be submitted in the POST API call'},
                'status': 500
            }

        # if describe is requested, show the usage
        if describe:
            response = {
                "describe": "This endpoint is the conf manager receiver, transactions will be written in the local replay journal it requires:",
                "options": {
                    'transaction_request': 'The JSON payload of the transaction to be requested',
                    'transaction_http_mode': 'The HTTP mode for the transaction (GET, POST, DELETE, UPDATE)',
                    'transaction_http_service': 'the HTTP service for the transaction',
                }
            }

            return {
                "payload": response,
                'status': 200
            }

        logging.info(f'conf manager received configuration, resp_dict=\"{resp_dict}\"')

        # associates with a unique md5
        transaction_md5 = hashlib.md5(json.dumps(transaction_request).encode()).hexdigest()

        # open or create the transaction journal
        local_dir = os.path.join(splunkhome, 'etc', 'apps', 'TA-trackme-conf-manager', 'local')

        # create the local dir if needed
        if not os.path.isdir(local_dir):
            try:
                os.mkdir(local_dir)
            except Exception as e:
                msg = f'failed to create the local directory with exception=\"{str(e)}\"'
                logging.error(msg)
                return {
                    "payload": {'response': msg},
                    'status': 200
                }                

        # transaction journal
        transaction_journal = os.path.join(local_dir, 'trackme_conf_manager_transactions_journal.json')

        transaction_object = {
            transaction_md5: {
                "transaction_request": transaction_request,
                "transaction_http_mode": transaction_http_mode,
                "transaction_http_service": transaction_http_service,
                "ctime": time.time(),
            }
        }

        # Append transaction to journal
        try:
            with open(transaction_journal, 'a+') as f:
                try:
                    f.seek(0)  # Go to the beginning of file to read
                    data = json.load(f)
                except json.JSONDecodeError:  # if the file is empty, set data to be an empty dict
                    data = {}

                data.update(transaction_object)
                
                f.seek(0)  # Go back to the beginning of file to write
                f.truncate()  # Remove current file contents
                json.dump(data, f, indent=4)
        except Exception as e:
            msg = f'Failed to write to transaction journal with exception=\"{str(e)}\"'
            logging.error(msg)
            return {
                "payload": {'response': msg},
                'status': 500
            }

        # render the response
        response = {
            'response': f'transaction received with md5={transaction_md5} and successfully written to the journal',
            'journal': f'{transaction_journal}',
            }
        
        logging.info(response)
        return {
            "payload": response,
            'status': 200
        }

