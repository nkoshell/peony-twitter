# -*- coding: utf-8 -*-

import asyncio
import os
from unittest.mock import patch

import aiohttp
import peony
import peony.api
import pytest
from peony import BasePeonyClient, data_processing, exceptions, oauth
from peony.general import twitter_api_version, twitter_base_api_url

from . import Data, MockResponse, dummy

oauth2_keys = 'PEONY_CONSUMER_KEY', 'PEONY_CONSUMER_SECRET'
oauth2 = all(key in os.environ for key in oauth2_keys)

oauth2_creds = 'consumer_key', 'consumer_secret'
token = None


@pytest.fixture
def dummy_client():
    return peony.BasePeonyClient("", "", loop=False)


def test_create_endpoint(dummy_client):
    base_url = twitter_base_api_url.format(api='api',
                                           version=twitter_api_version)

    client_endpoint = dummy_client.api.test.endpoint.url()
    api = peony.api.APIPath([base_url], '.json', dummy_client)
    assert client_endpoint == api.test.endpoint.url()
    client_endpoint_item = dummy_client['api']['test']['endpoint'].url()
    assert client_endpoint == client_endpoint_item


def test_create_endpoint_dict(dummy_client):
    api = {'api': 'api', 'version': '2.0', 'suffix': '.json'}
    endpoint = dummy_client[api].test.url()
    base_url = twitter_base_api_url.format(api='api', version='2.0')
    assert endpoint == base_url + "/test.json"


def test_create_endpoint_set_exception(dummy_client):
    with pytest.raises(TypeError):
        dummy_client[{'hello', 'world'}]


def test_create_endpoint_tuple(dummy_client):
    base_url_v2 = twitter_base_api_url.format(api='api', version='2.0')
    assert dummy_client['api', '2.0'].test.url() == base_url_v2 + '/test.json'

    base_url_v1 = twitter_base_api_url.format(api='api', version='1.0')
    endpoint = base_url_v1 + '/test.json'
    assert dummy_client['api', '1.0', '.json'].test.url() == endpoint

    base_url = twitter_base_api_url.format(api='api', version="").rstrip('/')
    assert dummy_client['api', '', ''].test.url() == base_url + '/test'

    custom_base_url = "http://{api}.google.com/{version}"
    endpoint = "http://www.google.com/test"
    assert dummy_client['www', '', '', custom_base_url].test.url() == endpoint

    endpoint = "http://google.com/test"
    assert dummy_client['', '', '', custom_base_url].test.url() == endpoint


def test_create_streaming_path(dummy_client):
    assert isinstance(dummy_client.stream.test, peony.api.StreamingAPIPath)


def test_create_api_path(dummy_client):
    assert isinstance(dummy_client.api.test, peony.api.APIPath)


class MockSessionRequest:

    def __init__(self, status=200, data=MockResponse.message,
                 content_type="plain/text"):
        self.status = status
        self.data = data
        self.ctype = content_type

    async def __aenter__(self, *args, **kwargs):
        return MockResponse(status=self.status, data=self.data,
                            content_type=self.ctype)

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass

    def __call__(self, *args, **kwargs):
        return self


class MockSession:

    def __init__(self, request=None):
        if request is None:
            self.request = MockSessionRequest()
        else:
            self.request = request


class SetupClientTest(BasePeonyClient):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._session = MockSession()
        self.a, self.b, self.c = "", "", {}

    @peony.init_task
    async def setup_a(self):
        self.a = "123"

    @peony.init_task
    async def setup_b(self):
        self.b = "321"

    @peony.init_task
    async def setup_c(self):
        data = Data({'hello': "world"})

        with patch.object(data_processing, 'read', side_effect=data):
            self.c = await self.api.test.get()


@pytest.mark.asyncio
async def test_setup(event_loop):
    client = SetupClientTest("", "", loop=event_loop)

    async def test():
        await client.setup()
        assert client.a == "123"
        assert client.b == "321"
        assert client.c.data == {'hello': "world"}

    await asyncio.gather(test(), test())


def oauth2_decorator(func):

    @pytest.mark.asyncio
    @pytest.mark.skipif(not oauth2, reason="no credentials found")
    async def decorator():
        global token

        client = get_oauth2_client(bearer_token=token)
        await func(client)

        # keep the token for the next test
        token = client.headers.token

    return decorator


def get_oauth2_client(**kwargs):
    creds = {oauth2_creds[i]: os.environ[oauth2_keys[i]] for i in range(2)}
    return BasePeonyClient(auth=oauth.OAuth2Headers, loop=False,
                           **creds, **kwargs)


class TasksClientTest(SetupClientTest):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.tasks_tests = [False, False, True]

    @peony.task
    async def task_a(self):
        self.tasks_tests[0] = True
        await self.api.test.get()

    @peony.task
    async def task_b(self):
        self.tasks_tests[1] = True
        await self.api.endpoint.post()

    async def not_a_task(self):
        self.tasks_tests[2] = False


@pytest.mark.asyncio
async def test_tasks():
    client = TasksClientTest("", "")
    with patch.object(client, 'request', side_effect=dummy) as request:
        await client.run_tasks()
        base_url = twitter_base_api_url.format(api='api',
                                               version=twitter_api_version)
        assert request.called_with(method='get', url=base_url + '/test.json')
        assert request.called_with(method='get',
                                   url=base_url + '/endpoint.json')

        assert client.a == "123"
        assert client.b == "321"
        assert client.c is None  # it's None this time

        assert all(client.tasks_tests)


@pytest.mark.asyncio
async def test_streaming_apis(dummy_client):
    with patch.object(dummy_client, 'request', side_effect=dummy) as request:
        await dummy_client.api.test.get()
        assert request.called

    with patch.object(dummy_client, 'stream_request') as request:
        dummy_client.stream.test.get()
        assert request.called

    client = BasePeonyClient("", "", streaming_apis={'api'})
    with patch.object(client, 'stream_request') as request:
        client.api.test.get()
        assert request.called

    with patch.object(client, 'request', side_effect=dummy) as request:
        await client.stream.test.get()
        assert request.called


def test_client_base_url():
    base_url = "http://{api}.google.com/{version}"
    client = BasePeonyClient("", "", base_url=base_url, api_version="1")
    assert client.api.test.url() == "http://api.google.com/1/test.json"


@pytest.mark.asyncio
async def test_session_creation(dummy_client):
    with patch.object(aiohttp, 'ClientSession') as client_session:
        await dummy_client.setup()
        assert client_session.called


class SetupInitListTest(BasePeonyClient):

    @property
    def init_tasks(self):
        return self.setup_a(), self.setup_b()

    async def setup_a(self):
        self.a = "123"

    async def setup_b(self):
        self.b = "321"


@pytest.mark.asyncio
async def test_setup_init_tasks_list():
    client = SetupInitListTest("", "")
    await client.setup()
    assert client.a == "123"
    assert client.b == "321"


def test_client_error():
    with pytest.raises(TypeError):
        BasePeonyClient()


def test_client_encoding_loads():
    text = bytes([194, 161])
    data = b"{\"hello\": \"%s\"}" % text

    client = BasePeonyClient("", "", encoding='utf-8')
    assert client._loads(data)['hello'] == text.decode('utf-8')

    client = BasePeonyClient("", "", encoding='ascii')
    with pytest.raises(UnicodeDecodeError):
        client._loads(data)


@pytest.mark.asyncio
async def test_close(event_loop):
    client = BasePeonyClient("", "", loop=event_loop)
    await client.setup()

    def dummy_func(*args, **kwargs):
        pass

    client._gathered_tasks = asyncio.gather(dummy())
    with patch.object(client.loop, 'run_until_complete',
                      side_effect=dummy_func) as run:
        with patch.object(client._gathered_tasks, 'cancel') as cancel:
            with patch.object(client._gathered_tasks, 'exception') as exc:
                with patch.object(client._session, 'close') as close:
                    client.close()
                    run.assert_called_once_with(client._gathered_tasks)
                    cancel.assert_called_once_with()
                    close.assert_called_once_with()
                    exc.assert_called_once_with()
                    assert client._session is None


def test_close_no_session():
    client = BasePeonyClient("", "")
    assert client._session is None
    client.close()


def test_close_no_tasks():
    client = BasePeonyClient("", "")
    assert client._gathered_tasks is None
    client.close()


@pytest.mark.asyncio
async def test_bad_request(dummy_client):
    async def prepare_dummy(*args, **kwargs):
        return kwargs

    dummy_client._session = MockSession(MockSessionRequest(status=404))
    with patch.object(dummy_client.headers, 'prepare_request',
                      side_effect=prepare_dummy):
        with pytest.raises(exceptions.NotFound):
            await dummy_client.request('get', "http://google.com/404")


@pytest.fixture
def oauth2_client(event_loop):
    if oauth2:
        return get_oauth2_client(loop=event_loop)


@oauth2_decorator
async def test_oauth2_get_token(client):
    if 'Authorization' in client.headers:
        del client.headers['Authorization']

    await client.headers.sign()


@oauth2_decorator
async def test_oauth2_request(client):
    await client.api.search.tweets.get(q="@twitter hello :)")


@oauth2_decorator
async def test_oauth2_invalidate_token(client):
    await client.headers.sign()  # make sure there is a token
    await client.headers.invalidate_token()
    assert client.headers.token is None


@oauth2_decorator
async def test_oauth2_bearer_token(client):
    await client.headers.sign()

    token = client.headers.token

    client2 = get_oauth2_client(bearer_token=token)
    assert client2.headers.token == client.headers.token
