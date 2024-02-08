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
splunkhome = os.environ["SPLUNK_HOME"]

# set logging
filehandler = RotatingFileHandler(
    "%s/var/log/splunk/ta_trackme_conf_manager_trackmeconfmanager.log" % splunkhome,
    mode="a",
    maxBytes=10000000,
    backupCount=1,
)
formatter = logging.Formatter(
    "%(asctime)s %(levelname)s %(filename)s %(funcName)s %(lineno)d %(message)s"
)
logging.Formatter.converter = time.gmtime
filehandler.setFormatter(formatter)
log = logging.getLogger()  # root logger - Good to get it only once.
for hdlr in log.handlers[:]:  # remove the existing file handlers
    if isinstance(hdlr, logging.FileHandler):
        log.removeHandler(hdlr)
log.addHandler(filehandler)  # set the new handler
# set the log level to INFO, DEBUG as the default is ERROR
log.setLevel(logging.INFO)

# append libs
sys.path.append(
    os.path.join(splunkhome, "etc", "apps", "TA-trackme-conf-manager", "lib")
)

# Splunk libs
from splunklib.searchcommands import (
    dispatch,
    GeneratingCommand,
    Configuration,
    Option,
    validators,
)
import splunklib.client as client

# Import trackme libs
from trackme_conf_manager_libs import trackme_reqinfo


@Configuration(distributed=False)
class TrackMeConfManager(GeneratingCommand):

    mode = Option(
        doc="""
        **Syntax:** **mode=****
        **Description:** The execution mode, valid options are: live|simulation|get.""",
        require=False,
        default="simulation",
        validate=validators.Match("mode", r"^(live|force|simulation|get|remove)$"),
    )

    transactions = Option(
        doc="""
        **Syntax:** **transactions=****
        **Description:** A comma seperated list of transactions, default to * which means all transactions can be considered.""",
        require=False,
        default="*",
        validate=validators.Match("mode", r".*"),
    )

    def get_collection_records(self, collection):
        """
        Queries and processes records from a collection based on specific criteria.

        :param collection: The collection object to query.
        :param min_delay_sec: Minimum delay seconds for processing.
        :return: Tuple containing collection records and a dictionary of records.
        """
        collection_records = []
        collection_keys = []
        collection_dict = {}

        end = False
        skip_tracker = 0
        while not end:
            process_collection_records = collection.data.query(skip=skip_tracker)
            if process_collection_records:
                for item in process_collection_records:
                    collection_records.append(item)
                    collection_keys.append(item.get("_key"))
                    collection_dict[item.get("_key")] = item
                skip_tracker += 5000
            else:
                end = True

        return collection_records, collection_dict, collection_keys

    def get_transactions(self, journal_file, collection_keys):
        """
        Processes transactions from a journal file.

        :param journal_file: Path to the journal file.
        :return: Tuple containing a list of transactions and a dictionary of transactions.
        """
        transactions_list = []
        transactions_dict = {}

        if journal_file:
            with open(journal_file, "r+") as f:
                try:
                    f.seek(0)  # Go to the beginning of file to read
                    data = json.load(f)
                except Exception as e:
                    msg = f'failed to open the transaction for reading, exception="{str(e)}"'
                    logging.error(msg)
                    raise Exception(msg)

                for transaction in data:
                    transactions_list.append(transaction)

                    transaction_dict = {
                        "transaction": transaction,
                        "processed_status": 1 if transaction in collection_keys else 0,
                        "transaction_request": data[transaction].get(
                            "transaction_request"
                        ),
                        "transaction_http_mode": data[transaction].get(
                            "transaction_http_mode"
                        ),
                        "transaction_http_service": data[transaction].get(
                            "transaction_http_service"
                        ),
                        "transaction_ctime": data[transaction].get("ctime"),
                    }

                    # Add to the transactions_dict
                    transactions_dict[transaction] = transaction_dict

        return transactions_list, transactions_dict

    def run_api_call(
        self,
        transaction_http_mode,
        splunkd_uri,
        transaction_http_service,
        session_key,
        transaction_request,
    ):
        """
        Executes an API call based on the specified parameters.

        :param transaction_http_mode: HTTP method (e.g., 'get', 'post', 'delete').
        :param splunkd_uri: Base URI for Splunkd.
        :param transaction_http_service: Specific endpoint for the transaction.
        :param session_key: Session key for authorization.
        :param transaction_request: Request payload.
        :return: The response from the API call.
        """
        url = f"{splunkd_uri}/{transaction_http_service}"
        headers = {
            "Authorization": f"Splunk {session_key}",
            "Content-Type": "application/json",
        }

        response = None
        if transaction_http_mode == "get":
            response = requests.get(
                url, headers=headers, verify=False, data=json.dumps(transaction_request)
            )
        elif transaction_http_mode == "post":
            response = requests.post(
                url, headers=headers, verify=False, data=json.dumps(transaction_request)
            )
        elif transaction_http_mode == "delete":
            response = requests.delete(
                url, headers=headers, verify=False, data=json.dumps(transaction_request)
            )

        return response

    def generate(self, **kwargs):

        # Start performance counter
        start = time.time()

        # Get request info and set logging level
        reqinfo = trackme_reqinfo(
            self._metadata.searchinfo.session_key, self._metadata.searchinfo.splunkd_uri
        )
        logging.debug(f"reqinfo={json.dumps(reqinfo, indent=2)}")
        log.setLevel(logging.getLevelName(reqinfo["logging_level"]))

        # Get config
        conf_manager_role = reqinfo["trackme_conf"]["trackme_general"][
            "conf_manager_role"
        ]

        # Data collection
        collection_name = "kv_trackme_conf_manager"
        collection = self.service.kvstore[collection_name]

        # get all records
        get_collection_start = time.time()
        (
            collection_records,
            collection_dict,
            collection_keys,
        ) = self.get_collection_records(collection)

        logging.info(
            f'context="perf", get collection records, no_records="{len(collection_records)}", run_time="{round((time.time() - get_collection_start), 3)}", collection="{collection_name}"'
        )

        # Set the journal file target
        journal_file = None
        journal_filename = "trackme_conf_manager_transactions_journal.json"
        default_dir = os.path.join(
            splunkhome, "etc", "apps", "TA-trackme-conf-manager", "default"
        )
        local_dir = os.path.join(
            splunkhome, "etc", "apps", "TA-trackme-conf-manager", "local"
        )

        if os.path.isfile(os.path.join(local_dir, journal_filename)):
            journal_file = os.path.join(local_dir, journal_filename)
            logging.debug(f'using journal found at path="{journal_file}"')
        elif os.path.isfile(os.path.join(default_dir, journal_filename)):
            journal_file = os.path.join(default_dir, journal_filename)
            logging.debug(f'using journal found at path="{journal_file}"')
        else:
            journal_file = None

        #
        # First, open the journal, list all transactions and add them to a list
        # Also, create a dict ordered by the transaction id and store its status
        # If the transaction id is in collection_keys, processsed_status = 1, otherwise processed_status = 0
        #

        # Assuming journal_file and collection_keys are defined earlier in your code
        transactions_list, transactions_dict = self.get_transactions(
            journal_file, collection_keys
        )

        logging.debug(
            f'transactions_list="{json.dumps(transactions_list, indent=2)}", transactions_dict="{json.dumps(transactions_dict, indent=2)}"'
        )

        # set transactions_allowed, if self.transactions is set to * then all transactions are allowed, otherwise
        # turn its value into a list from comma separated values
        transactions_allowed = (
            transactions_list
            if self.transactions == "*"
            else self.transactions.split(",")
        )

        #
        # Process transations main
        #

        if not transactions_list:
            logging.info("no transactions were found in the journal file")
            yield {
                "time": time.time(),
                "_raw": {
                    "response": "There are no transactions to be read yet, TrackMe Configuration Manager is not ready."
                },
            }

        else:

            for transaction in transactions_allowed:

                if self.mode == "remove":
                    # Determine which transactions to remove
                    transactions_to_remove = (
                        transactions_list
                        if self.transactions == "*"
                        else self.transactions.split(",")
                    )

                    if journal_file:
                        # Read the existing data from the journal file
                        with open(journal_file, "r") as file:
                            data = json.load(file)

                        # If removing all transactions, clear the dictionary. Otherwise, delete specified transactions.
                        if self.transactions == "*":
                            data = {}
                        else:
                            for transaction in transactions_to_remove:
                                data.pop(
                                    transaction, None
                                )  # Use pop to avoid KeyError if transaction does not exist

                        # Write the updated data back to the journal file
                        with open(journal_file, "w") as file:
                            json.dump(data, file, indent=4)

                        logging.info(f"Transactions removed: {transaction}")
                        yield {
                            "time": time.time(),
                            "_raw": {
                                "response": f"Successfully removed transactions: {transaction}"
                            },
                        }

                    else:
                        yield {
                            "time": time.time(),
                            "_raw": {
                                "response": "Journal file not found or no transactions to remove."
                            },
                        }

                    # attempt to remove the transaction from the KVstore collection
                    try:
                        collection.data.delete(json.dumps({"_key": transaction}))
                        logging.info(
                            f'transaction was removed successfully from the KVstore, transaction="{transaction}"'
                        )
                    except Exception as e:
                        logging.error(
                            f'failed to remove transaction from the KVstore, transaction="{transaction}", this can be excepted if the transaction was remove earlier or the if KVstore is empty, exception="{str(e)}"'
                        )

                elif self.mode == "get":

                    # yield
                    try:
                        yield {
                            "time": time.time(),
                            "_raw": transactions_dict[transaction],
                        }
                    except Exception as e:
                        yield {
                            "time": time.time(),
                            "_raw": {
                                "response": f"Failed to get transaction, this transaction may not be in the journal anymore, exception={str(e)}"
                            },
                        }

                else:

                    logging.info(
                        f'verifying transaction status, transaction_md5="{transaction}"'
                    )

                    # process the transaction if needed
                    transaction_runops = False

                    if self.mode != "force":
                        if transaction not in collection_keys:
                            logging.info(
                                f"transaction {transaction} was not found in the KVstore and will be processed in live mode"
                            )
                            transaction_runops = True
                        else:
                            logging.info(
                                f"transaction {transaction} was found in the KVstore and processed already"
                            )
                    else:
                        logging.info(
                            f"transaction {transaction} will be processed in force mode, regardless of its status"
                        )
                        transaction_runops = True

                    # get
                    transaction_request = transactions_dict[transaction].get(
                        "transaction_request"
                    )
                    transaction_http_mode = transactions_dict[transaction].get(
                        "transaction_http_mode"
                    )
                    transaction_http_service = transactions_dict[transaction].get(
                        "transaction_http_service"
                    )
                    transaction_ctime = transactions_dict[transaction].get("ctime")

                    # set info
                    transaction_info = None

                    if conf_manager_role != "producer":
                        transaction_info = (
                            "running in receiver mode, no actions will be performed"
                        )
                        transaction_runops = False
                    else:
                        if self.mode == "simulation" and transaction_runops:
                            transaction_info = "This transaction has not been processed yet and will be processed accordingly if submitted in live mode"
                        else:
                            transaction_info = "This transaction has been processed already, the Conf Manager will not attempt to process it again"

                    #
                    # Process transactions
                    #

                    if (
                        conf_manager_role == "producer"
                        and (self.mode == "live" or self.mode == "force")
                        and transaction_runops
                    ):

                        response = self.run_api_call(
                            transaction_http_mode=transaction_http_mode,
                            splunkd_uri=self._metadata.searchinfo.splunkd_uri,
                            transaction_http_service=transaction_http_service,
                            session_key=self._metadata.searchinfo.session_key,
                            transaction_request=transaction_request,
                        )

                        if response.status_code in (200, 201, 204):
                            msg = f"the transaction was processed successfully, HTTP returned code: {response.status_code}"
                            logging.debug(
                                f'Success transaction call, data="{response}"'
                            )
                            transaction_info = f"the transaction was processed successfully, HTTP returned code: {response.status_code}"

                            # register in the KVstore
                            try:
                                collection.data.insert(
                                    json.dumps(
                                        {
                                            "_key": transaction,
                                            "transaction_md5": transaction,
                                            "transaction_http_mode": transaction_http_mode,
                                            "transaction_http_service": transaction_http_service,
                                            "transaction_request": json.dumps(
                                                transaction_request, indent=2
                                            ),
                                            "transaction_mtime": time.time(),
                                            "transaction_status": "done",
                                        }
                                    )
                                )
                                transaction_info = {
                                    "transaction": transaction,
                                    "transaction_runops": transaction_runops,
                                    "transaction_http_mode": transaction_http_mode,
                                    "transaction_http_service": transaction_http_service,
                                    "transaction_request": transaction_request,
                                    "transaction_ctime": transaction_ctime,
                                    "transaction_info": transaction_info,
                                }
                                logging.info(
                                    f'transaction was registered successfully to the KVstore with key="{transaction}", transaction_info="{json.dumps(transaction_info, indent=2)}"'
                                )

                            except Exception as e:
                                logging.error(
                                    f'transaction could not be registered to the KVstore, exception="{e}"'
                                )

                        else:
                            msg = f'Failed to perform the transaction, status_code={response.status_code}, response_text="{response.text}"'
                            transaction_info = f'the transaction has failed, status_code={response.status_code}, response_text="{response.text}"'

                    # yield
                    yield {
                        "time": time.time(),
                        "_raw": {
                            "transaction": transaction,
                            "transaction_runops": transaction_runops,
                            "transaction_http_mode": transaction_http_mode,
                            "transaction_http_service": transaction_http_service,
                            "transaction_request": transaction_request,
                            "transaction_ctime": transaction_ctime,
                            "transaction_info": transaction_info,
                        },
                    }

        # Log the run time
        logging.info(
            f"trackmeconfmanager has terminated, run_time={round(time.time() - start, 3)}"
        )


dispatch(TrackMeConfManager, sys.argv, sys.stdin, sys.stdout, __name__)
