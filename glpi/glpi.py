# Copyright 2017 Predict & Truly Systems All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# GLPI API Rest documentation:
# https://github.com/glpi-project/glpi/blob/9.1/bugfixes/apirest.md

from __future__ import print_function
import os
import sys
import json as json_import
import logging
import requests
from requests.structures import CaseInsensitiveDict
from .version import __version__
from _cffi_backend import string
from glpi_auth import GLpiAuth

if sys.version_info[0] > 2:
    from html.parser import HTMLParser
else:
    from HTMLParser import HTMLParser


logger = logging.getLogger(__name__)


def load_from_vcap_services(service_name):
    vcap_services = os.getenv("VCAP_SERVICES")
    if vcap_services is not None:
        services = json_import.loads(vcap_services)
        if service_name in services:
            return services[service_name][0]["credentials"]
    else:
        return None


def _remove_null_values(dictionary):
    if isinstance(dictionary, dict):
        return dict([(k, v) for k, v in dictionary.items() if v is not None])
    return dictionary


def _cleanup_param_value(value):
    if isinstance(value, bool):
        return 'true' if value else 'false'
    return value


def _cleanup_param_values(dictionary):
    if isinstance(dictionary, dict):
        return dict(
            [(k, _cleanup_param_value(v)) for k, v in dictionary.items()])
    return dictionary


def _glpi_html_parser(content):
    """
    Try to retrieve data tokens from HTML content.
    It's useful to debug GLPI rest when it's not returning JSON responses. I.E:
    when MYSQL server is down, API Rest answer html errors.
    """
    class GlpiHTMLParser(HTMLParser):
        def __init__(self, content):
            HTMLParser.__init__(self)
            self.count = 0
            self.data = []
            self.feed(content)

        def get_count(self):
            return self.count

        def get_data(self):
            return self.data

        def get_data_clear(self):
            """ Get data tokens without comments '/' """
            new_data = []
            for r in self.get_data():
                if r.startswith('/'):
                    continue
                new_data.append(r)
            return new_data

        def handle_data(self, data):
            """ Get data tokens in HTML feed """
            d = data.strip()
            if d:
                self.count += 1
                self.data.append(d)

    html_parser = GlpiHTMLParser(content)
    return html_parser.get_data_clear()


class GlpiException(Exception):
    pass


class GlpiInvalidArgument(GlpiException):
    pass


class GlpiService(object):
    """ Polymorphic class of GLPI REST API Service. """
    __version__ = __version__

    def __init__(self, url_apirest, token_app, uri=None,
                 username=None, password=None, token_auth=None,
                 use_vcap_services=False, vcap_services_name=None,
                 sslverify=False, writable=False):
        """
        [TODO] Loads credentials from the VCAP_SERVICES environment variable if
        available, preferring credentials explicitly set in the request.
        If VCAP_SERVICES is not found (or use_vcap_services is set to False),
        username and password credentials must be specified.

        You can choose in setup initial authentication using username and
        password, or setup with Authorization HTTP token. If token_auth is set,
        username and password credentials must be ignored.
        """
        self.__version__ = __version__
        self.url = url_apirest
        self.app_token = token_app
        self.uri = uri

        self.username = username
        self.password = password
        self.token_auth = token_auth
        self.sslverify = sslverify
        self.writable = writable

        self.session = None

        if token_auth is not None:
            if username is not None or password is not None:
                raise GlpiInvalidArgument(
                    'Cannot set token_auth and username and password together')
            self.set_token_auth(token_auth)
        else:
            self.set_username_and_password(username, password)

        if use_vcap_services and not self.username and not self.token_auth:
            self.vcap_service_credentials = load_from_vcap_services(
                vcap_services_name)
            if self.vcap_service_credentials is not None and isinstance(
                    self.vcap_service_credentials, dict):
                self.url = self.vcap_service_credentials['url']
                if 'username' in self.vcap_service_credentials:
                    self.username = self.vcap_service_credentials['username']
                if 'password' in self.vcap_service_credentials:
                    self.password = self.vcap_service_credentials['password']
                if 'token_auth' in self.vcap_service_credentials:
                    self.token_auth =\
                        self.vcap_service_credentials['token_auth']
                if 'app_token' in self.vcap_service_credentials:
                    self.app_token = self.vcap_service_credentials['app_token']

        if self.app_token is None:
            raise GlpiException(
                'You must specify GLPI API-Token(app_token) to make API calls')

        if (self.username is None or self.password is None)\
                and self.token_auth is None:
            raise GlpiException(
                'You must specify your username and password, or token_auth'
                'service credentials ')

    def set_username_and_password(self, username=None, password=None):
        if username == 'YOUR SERVICE USERNAME':
            username = None
        if password == 'YOUR SERVICE PASSWORD':
            password = None

        self.username = username
        self.password = password

    def set_token_auth(self, token_auth):
        if token_auth == 'YOUR AUTH TOKEN':
            token_auth = None

        self.token_auth = token_auth

    def set_uri(self, uri):
        self.uri = uri

    def get_version(self):
        return self.__version__

    """
    Session Token
    """
    def set_session_token(self):
        """ Set up new session ID """

        # URL should be like: http://glpi.example.com/apirest.php
        full_url = self.url + '/initSession'
        if self.writable:
            full_url = full_url + '?session_write=true'
        auth = None

        headers = {"App-Token": self.app_token,
                   "Content-Type": "application/json"}

        if self.token_auth is not None:
            if isinstance(self.token_auth, str):
                auth = GLpiAuth(self.token_auth)
            else:
                auth = self.token_auth
        else:
            auth = (self.username, self.password)

        r = requests.request('GET', full_url,
                             auth=auth, headers=headers, verify=self.sslverify)

        try:
            if r.status_code == 200:
                self.session = r.json()['session_token']
                return True
            else:
                err = _glpi_html_parser(r.content)
                raise GlpiException("Init session to GLPI server fails: %s" %
                                    err)
        except Exception:
            err = _glpi_html_parser(r.content)
            raise GlpiException("ERROR when try to init session in GLPI\
                                server:%s" % err)

        return False

    def get_session_token(self):
        """ Returns current session ID """

        if self.session is not None:
            return self.session
        else:
            try:
                self.set_session_token()
                return self.session
            except GlpiException:
                raise

            else:
                return 'Unable to get Session Token'

    def update_session_token(self, session_id):
        """ Update session ID """

        if session_id:
            self.session = session_id

        return self.session

    """ Request """
    def request(self, method, url, accept_json=False, headers={},
                params=None, json=None, data=None, files=None, **kwargs):
        """
        Make a request to GLPI Rest API.
        Return response object.
        (http://docs.python-requests.org/en/master/api/#requests.Response)
        """

        full_url = '%s/%s' % (self.url, url.strip('/'))
        input_headers = _remove_null_values(headers) if headers else {}

        headers = CaseInsensitiveDict(
             {'user-agent': 'glpi-sdk-python-' + __version__})

        if accept_json:
            headers['accept'] = 'application/json'

        try:
            if self.session is None:
                self.set_session_token()
            headers.update({'Session-Token': self.session})
        except GlpiException as e:
            raise GlpiException("Unable to get Session token. \
                                ERROR: {}".format(e))

        if self.app_token is not None:
            headers.update({'App-Token': self.app_token})

        headers.update(input_headers)

        # Remove keys with None values
        params = _remove_null_values(params)
        params = _cleanup_param_values(params)
        json = _remove_null_values(json)
        data = _remove_null_values(data)
        files = _remove_null_values(files)

        try:
            response = requests.request(method=method, url=full_url,
                                        headers=headers, params=params,
                                        data=data, verify=self.sslverify,
                                        **kwargs)
        except Exception:
            logger.error("ERROR requesting uri(%s) payload(%s)" % (url, data))
            raise

        return response

    def get_payload(self, data_json):
        """ Construct the payload for REST API from JSON data. """

        data_str = ""
        null_str = None
        for k in data_json:
            if data_str is not "":
                data_str = "%s," % data_str

            if data_json[k] == null_str:
                data_str = '%s "%s": null' % (data_str, k)
            elif isinstance(data_json[k], str):
                data_str = '%s "%s": "%s"' % (data_str, k, data_json[k])
            else:
                data_str = '%s "%s": %s' % (data_str, k, str(data_json[k]))

        return data_str.replace('\\', '\\\\').replace('\n', '\\n').\
            replace('\r', '')

    """ Generic Items methods """
    # [C]REATE - Create an Item
    def create(self, data_json=None):
        """ Create an object Item. """

        if (data_json is None):
            return "{ 'error_message' : 'Object not found.'}"

        payload = '{"input": { %s }}' % (self.get_payload(data_json))
        print(payload)

        response = self.request('POST', self.uri, data=payload,
                                accept_json=True)

        return response.json()

    # [R]EAD - Retrieve Item data
    def get_all(self, expand_dropdowns=False, uri_query=""):
        """ Return all content of Item in JSON format. """
        if expand_dropdowns:
            payload = {'expand_dropdowns': str(expand_dropdowns).lower()}
        else:
            payload = {}
        res = self.request('GET', self.uri + uri_query, params=payload)
        return res.json()

    def get(self, item_id, expand_dropdowns=False):
        """ Return the JSON item with ID item_id. """

        if isinstance(item_id, int):
            if expand_dropdowns:
                payload = {'expand_dropdowns': str(expand_dropdowns).lower()}
            else:
                payload = {}
            uri = '%s/%d' % (self.uri, item_id)
            response = self.request('GET', uri, params=payload)
            return response.json()
        else:
            return {'error_message': 'Unale to get %s ID [%s]' % (self.uri,
                                                                  item_id)}

    def get_path(self, path=''):
        """ Return the JSON from path """
        response = self.request('GET', path)
        return response.json()

    def search_options(self, item_name):
        """
        List search options for an Item to be used in
        search_engine/search_query.
        """
        new_uri = "%s/%s" % (self.uri, item_name)
        response = self.request('GET', new_uri, accept_json=True)

        return response.json()

    def search_engine(self, search_query):
        """
        Search an item by URI.
        Use GLPI search engine passing parameter by URI.
        #TODO could pass search criteria in payload, like others items
        operations.
        """
        new_uri = "%s/%s" % (self.uri, search_query)
        response = self.request('GET', new_uri, accept_json=True)

        return response.json()

    # [U]PDATE an Item
    def update(self, data):
        """ Update an object Item. """

        payload = '{"input": { %s }}' % (self.get_payload(data))
        new_url = "%s/%d" % (self.uri, data['id'])
        logging.debug(payload)
        response = self.request('PUT', new_url, data=payload, accept_json=True)

        return response.json()

    # [D]ELETE an Item
    def delete(self, item_id, force_purge=False):
        """ Delete an object Item. """

        if not isinstance(item_id, int):
            return {"message_error": "Please define item_id to be deleted."}

        if force_purge:
            payload = '{"input": { "id": %d } "force_purge": true}' % (item_id)
        else:
            payload = '{"input": { "id": %d }}' % (item_id)

        response = self.request('DELETE', self.uri, data=payload)
        return response.json()


class GLPI(object):
    """
    Generic implementation of GLPI Items can manage all
    Itens in one GLPI server connection.
    We can use this class to save implementation of "new classes" and
    can reuse API sessions.
    To support new items you should create the dict key/value in item_map.
    """
    __version__ = __version__

    def __init__(self, url, app_token, auth_token,
                 item_map=None, sslverify=True, writable=False):
        """ Construct generic object """

        self.url = url
        self.app_token = app_token
        self.auth_token = auth_token
        self.sslverify = sslverify
        self.writable = writable

        self.item_uri = None
        self.item_map = {
            "ticket": "/Ticket",
            "knowbase": "/knowbaseitem",
            "listSearchOptions": "/listSearchOptions",
            "search": "/search",
            "user": "user",
            "getFullSession": "getFullSession",
            "getActiveProfile": "getActiveProfile",
            "getMyProfiles": "getMyProfiles",
            "location": "location",
        }
        self.api_rest = None
        self.api_session = None

        if item_map is not None:
            self.set_item_map(item_map)

    def help_item(self):
        """ Help item values """
        return {"available_items": self.item_map}

    def set_item(self, item_name):
        """ Define an item to object """
        try:
            self.item_uri = self.item_map[item_name]
        except:
            raise Exception('Key [{}] not found in Item MAP'.format(item_name))

    def set_item_map(self, item_map={}):
        """ Set an custom item_map. """
        self.item_map = item_map

    def set_api_uri(self):
        """
        Update URI in Service API object.
        We should do this every new Item requested.
        """
        self.api_rest.set_uri(self.item_uri)

    def update_uri(self, item_name):
        """ Avoid duplicate calls in every 'Item operators' """
        if (item_name not in self.item_map):
            if item_name.startswith('/'):
                item_name_real = item_name.split('/')[1]
                self.item_map.update({item_name_real: item_name})
                item_name = item_name_real
            else:
                _item_path = '/' + item_name
                self.item_map.update({item_name: _item_path})

        self.set_item(item_name)
        self.set_api_uri()

    def init_api(self):
        """ Initialize the API Rest connection """

        self.api_rest = GlpiService(self.url, self.app_token,
                                    token_auth=self.auth_token,
                                    sslverify=self.sslverify,
                                    writable=self.writable)

        try:
            self.api_session = self.api_rest.get_session_token()
        except GlpiException:
            raise

        if self.api_session is not None:
            return {"session_token": self.api_session}
        else:
            return {"message_error": "Unable to InitSession in GLPI Server."}

    def api_has_session(self):
        """
        Check if API has session cfg or if it is enalbed
        """
        if self.api_session is None:
            return False

        return True

    # [C]REATE - Create an Item
    def create(self, item_name, item_data):
        """ Create an Resource Item """
        try:
            if not self.api_has_session():
                self.init_api()

            self.update_uri(item_name)
            return self.api_rest.create(item_data)

        except GlpiException as e:
            return {'{}'.format(e)}

    # [R]EAD - Retrieve Item data
    def get_all(self, item_name, expand_dropdowns=False, searchText=None):
        """ Get all resources from item_name
        criteria: [
            {
                "field": "name",
                "value": "search value"
            }
        ]
        """
        if searchText is not None:
            s_index = 0
            uri_query = '?'
            for c in searchText['criteria']:
                if s_index == 0:
                    uri = ""
                else:
                    uri = "&"
                uri_query = uri_query + "searchText[%s]=%s" % (c['field'],
                                                               c['value'])
                s_index += 1
        else:
            uri_query = "?range=0-5000"

        try:
            if not self.api_has_session():
                self.init_api()

            self.update_uri(item_name)
            return self.api_rest.get_all(expand_dropdowns, uri_query)

        except GlpiException as e:
            return {'{}'.format(e)}

    def get(self, item_name, item_id=None, expand_dropdowns=False):
        """ Get item_name and/with resource by ID """
        try:
            if not self.api_has_session():
                self.init_api()

            self.update_uri(item_name)

            if item_id is None:
                return self.api_rest.get_path(item_name)

            return self.api_rest.get(item_id, expand_dropdowns)

        except GlpiException as e:
            return {'{}'.format(e)}

    def search_options(self, item_name):
        """ List GLPI APIRest Search Options """
        try:
            if not self.api_has_session():
                self.init_api()

            self.update_uri('listSearchOptions')
            return self.api_rest.search_options(item_name)

        except GlpiException as e:
            return {'{}'.format(e)}

    def search_criteria(self, data, criteria):
        """ #TODO Search in data some criteria """
        result = []
        for d in data:
            find = False
            for c in criteria:
                if c['value'].lower() in d[c['field']].lower():
                    find = True
            if find:
                result.append(d)
        return result

    def search_metacriteria(self, metacriteria):
        """ TODO: Search in metacriteria in source Item """
        return {"message_info": "Not implemented yet"}

    def search(self, item_name, criteria, expand_dropdowns=False):
        """ #SHOULD BE IMPROVED
        Return an Item with that matchs with criteria
        criteria: [
            {
                "field": "name",
                "value": "search value"
            }
        ]
        """
        if 'criteria' in criteria:
            data = self.get_all(item_name, expand_dropdowns)
            return self.search_criteria(data, criteria['criteria'])
        elif 'metacriteria' in criteria:
            return self.search_metacriteria(criteria['metacriteria'])
        else:
            return {"message_error": "Unable to find a valid criteria."}

    def search_engine(self, item_name, criteria):
        """ Call GLPI's search engine syntax.
        Ex. cURL - usage to query in 'name' and return ID:
        $ curl -X GET  ... 'http://path/to/apirest.php/search/Knowbaseitem?\
            criteria\[0\]\[field\]\=6\
            &criteria\[0\]\[searchtype\]=contains\
            &criteria\[0\]\[value\]=sites-multimidia\
            &criteria\[0\]\[link\]\=AND\
            &criteria\[1\]\[field\]\=2\
            &criteria\[1\]\[searchtype\]\=contains\
            &criteria\[1\]\[value\]\=\
            &criteria\[1\]\[link\]\=AND' |jq .

        INPUT query in JSON format (/apirest.php#search-items):
        metacriteria: [
            {
                "link": 'AND'
                "searchtype": "contais",
                "field": "name",
                "value": "search value"
            }
        ]

        RETURNS:
        GLPIs APIREST JSON formated with result of search in key 'data'.
        """
        field_map = {
            "name": 1,
            "id": 2,
            "location": 3,
            "type": 4,
            "serialnumber": 5,
            "body": 6,
            "processor": 17,
            "lastupdate": 19,
            "manufacturer": 23,
            "status": 31,
            "model": 40,
            "tags": 10500,
            "operatingsystem": 45
        }
        s_index = 0
        uri_query = "%s?" % item_name

        for c in criteria['criteria']:
            if s_index == 0:
                uri = ""
            else:
                uri = "&"

            uri = uri + "criteria[%d][field]=%d&" % (s_index,
                                                     field_map[c['field']])
            if c['value'] is None:
                uri = uri + "criteria[%d][value]=&" % (s_index)
            else:
                uri = uri + "criteria[%d][value]=%s&" % (s_index, c['value'])
            uri = uri + "criteria[%d][searchtype]=%s&" % (s_index,
                                                          c['searchtype'])
            uri = uri + "criteria[%d][link]=%s" % (s_index, c['link'])
            uri_query = uri_query + uri
            s_index += 1

        uri_query = uri_query + "&range=0-5000"
        try:
            if not self.api_has_session():
                self.init_api()

            self.update_uri('search')
            return self.api_rest.search_options(uri_query)

        except GlpiException as e:
            return {'{}'.format(e)}

    # [U]PDATE an Item
    def update(self, item_name, data):
        """ Update an Resource Item. Should have all the Item payload """
        try:
            if not self.api_has_session():
                self.init_api()

            self.update_uri(item_name)
            return self.api_rest.update(data)

        except GlpiException as e:
            return {'{}'.format(e)}

    # [D]ELETE an Item
    def delete(self, item_name, item_id, force_purge=False):
        """ Delete an Resource Item. Should have all the Item payload """
        try:
            if not self.api_has_session():
                self.init_api()

            self.update_uri(item_name)
            return self.api_rest.delete(item_id, force_purge=force_purge)

        except GlpiException as e:
            return {'{}'.format(e)}
