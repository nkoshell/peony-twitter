
import asyncio
import json
from unittest.mock import patch

import aiohttp
import pytest

import peony
import peony.stream
from peony import exceptions
from peony.stream import (DISCONNECTION, DISCONNECTION_TIMEOUT,
                          ENHANCE_YOUR_CALM, ENHANCE_YOUR_CALM_TIMEOUT, ERROR,
                          ERROR_TIMEOUT, MAX_DISCONNECTION_TIMEOUT,
                          MAX_RECONNECTION_TIMEOUT, NORMAL, RECONNECTION,
                          RECONNECTION_TIMEOUT)

from . import MockResponse

content = [{'text': MockResponse.message + " #%d" % i} for i in range(10)]
data = '\n'.join(json.dumps(line) for line in content) + '\n'


async def stream_content(*args, **kwargs):
    return MockResponse(data=data, status=200)


@pytest.fixture
def stream(event_loop):
    with aiohttp.ClientSession(loop=event_loop) as session:
        client = peony.client.BasePeonyClient("", "", session=session)

        stream_response = peony.stream.StreamResponse(
            client=client,
            method='get',
            url="http://whatever.com/stream"
        )

        with patch.object(session, 'request', side_effect=stream_content):
            yield stream_response


@pytest.mark.asyncio
async def test_stream_connect(stream):
    response = await stream._connect()
    assert data == await response.text()


@pytest.mark.asyncio
async def test_stream_connect_with_session(event_loop):
    with aiohttp.ClientSession(loop=event_loop) as session:
        client = peony.client.BasePeonyClient("", "", session=session)

        stream = peony.stream.StreamResponse(
            client=client,
            method='get',
            url="http://whatever.com/stream",
            session=session
        )

        with patch.object(session, 'request', side_effect=stream_content):
            response = await stream._connect()
            assert data == await response.text()


@pytest.mark.asyncio
async def test_stream_iteration(stream):
    async def stop(*args, **kwargs):
        raise StopAsyncIteration

    with patch.object(stream, 'init_restart', side_effect=stop):
        i = 0
        connected = False
        async for line in stream:
            if connected is False:
                connected = True
                assert 'connected' in line
            else:
                assert line['text'] == MockResponse.message + " #%d" % i
                i += 1


async def response_disconnection():
    return MockResponse(status=500)


async def response_calm():
    return MockResponse(status=429)


async def response_reconnection():
    return MockResponse(status=501)


async def response_forbidden():
    return MockResponse(status=403, content_type='text/plain')


async def response_stream_limit():
    return MockResponse(data="Exceeded connection limit for user", status=200)


@pytest.mark.asyncio
async def test_stream_reconnection_disconnection(stream):
    async def dummy(*args, **kwargs):
        pass

    turn = -1

    with patch.object(stream, '_connect', side_effect=response_disconnection):
        with patch.object(peony.stream.asyncio, 'sleep', side_effect=dummy):
            async for data in stream:
                assert stream._state == DISCONNECTION
                turn += 1

                if turn == 0:
                    assert data == {'connected': True}
                elif turn % 2 == 1:
                    timeout = DISCONNECTION_TIMEOUT * (turn + 1) / 2

                    if timeout > MAX_DISCONNECTION_TIMEOUT:
                        actual = data['reconnecting_in']
                        assert actual == MAX_DISCONNECTION_TIMEOUT
                        break

                    assert data == {'reconnecting_in': timeout, 'error': None}
                else:
                    assert data == {'stream_restart': True}


@pytest.mark.asyncio
async def test_stream_reconnection_reconnect(stream):
    async def dummy(*args, **kwargs):
        pass

    turn = -1

    with patch.object(stream, '_connect', side_effect=response_reconnection):
        with patch.object(peony.stream.asyncio, 'sleep', side_effect=dummy):
            async for data in stream:
                assert stream._state == RECONNECTION
                turn += 1

                if turn == 0:
                    assert data == {'connected': True}
                elif turn % 2 == 1:
                    timeout = RECONNECTION_TIMEOUT * 2**(turn // 2)

                    if timeout > MAX_RECONNECTION_TIMEOUT:
                        actual = data['reconnecting_in']
                        assert actual == MAX_RECONNECTION_TIMEOUT
                        break

                    assert data == {'reconnecting_in': timeout, 'error': None}
                else:
                    assert data == {'stream_restart': True}


@pytest.mark.asyncio
async def test_stream_reconnection_enhance_your_calm(stream):
    async def dummy(*args, **kwargs):
        pass

    turn = -1

    with patch.object(stream, '_connect', side_effect=response_calm):
        with patch.object(peony.stream.asyncio, 'sleep', side_effect=dummy):
            async for data in stream:
                assert stream._state == ENHANCE_YOUR_CALM
                turn += 1

                if turn >= 100:
                    break

                if turn == 0:
                    assert data == {'connected': True}
                elif turn % 2 == 1:
                    timeout = ENHANCE_YOUR_CALM_TIMEOUT * 2**(turn // 2)
                    assert data == {'reconnecting_in': timeout, 'error': None}
                else:
                    assert data == {'stream_restart': True}


@pytest.mark.asyncio
async def test_stream_reconnection_error(stream):
    with patch.object(stream, '_connect', side_effect=response_forbidden):
        with pytest.raises(exceptions.Forbidden):
            await stream.connect()


@pytest.mark.asyncio
async def test_stream_reconnection_stream_limit(stream):
    with patch.object(stream, '_connect', side_effect=response_stream_limit):
        assert stream._state == NORMAL
        data = await stream.__anext__()
        assert 'connected' in data

        data = await stream.__anext__()
        assert stream.state == ERROR
        assert data['reconnecting_in'] == ERROR_TIMEOUT
        assert isinstance(data['error'], exceptions.StreamLimit)


@pytest.mark.asyncio
async def test_stream_reconnection_error_on_reconnection(stream):
    with patch.object(stream, '_connect', side_effect=response_disconnection):
        await stream.connect()
        assert stream._state == DISCONNECTION
        data = {'reconnecting_in': DISCONNECTION_TIMEOUT, 'error': None}
        assert data == await stream.__anext__()
        assert stream._reconnecting

    with patch.object(stream, '_connect', side_effect=response_calm):
        stream._error_timeout = 0
        assert {'stream_restart': True} == await stream.__anext__()
        assert stream._state == ENHANCE_YOUR_CALM

        data = {'reconnecting_in': ENHANCE_YOUR_CALM_TIMEOUT, 'error': None}
        assert data == await stream.__anext__()


@pytest.mark.asyncio
async def test_stream_init_restart_wrong_state(stream):
    stream.state = peony.stream.NORMAL
    with pytest.raises(RuntimeError):
        await stream.init_restart()


@pytest.mark.asyncio
async def test_stream_reconnection_handled_errors(stream):
    async def handled_error():
        raise peony.stream.HandledErrors[0]

    with patch.object(stream, '_connect', side_effect=stream_content):
        data = await stream.__anext__()
        assert 'connected' in data
        with patch.object(stream.response, 'readline',
                          side_effect=handled_error):
            data = await stream.__anext__()
            assert data == {'reconnecting_in': ERROR_TIMEOUT, 'error': None}


@pytest.mark.asyncio
async def test_stream_reconnection_client_connection_error(stream):
    async def client_connection_error():
        raise aiohttp.ClientConnectionError

    with patch.object(stream, '_connect', side_effect=stream_content):
        data = await stream.__anext__()
        assert 'connected' in data
        with patch.object(stream.response, 'readline',
                          side_effect=client_connection_error):
            data = await stream.__anext__()
            assert data == {'reconnecting_in': ERROR_TIMEOUT, 'error': None}


@pytest.mark.asyncio
async def test_stream_async_context(event_loop):
    with aiohttp.ClientSession(loop=event_loop) as session:
        client = peony.client.BasePeonyClient("", "", session=session)
        context = peony.stream.StreamResponse(method='GET',
                                              url="http://whatever.com/stream",
                                              client=client)

        async with context as stream:
            with patch.object(stream, '_connect', side_effect=stream_content):
                await test_stream_iteration(stream)

        assert context.response.closed


@pytest.mark.asyncio
async def test_stream_context(event_loop):
    with aiohttp.ClientSession(loop=event_loop) as session:
        client = peony.client.BasePeonyClient("", "", session=session)
        context = peony.stream.StreamResponse(method='GET',
                                              url="http://whatever.com/stream",
                                              client=client)

        with context as stream:
            with patch.object(stream, '_connect', side_effect=stream_content):
                await test_stream_iteration(stream)

        assert context.response.closed


@pytest.mark.asyncio
async def test_stream_context_response_already_closed(event_loop):
    with aiohttp.ClientSession(loop=event_loop) as session:
        client = peony.client.BasePeonyClient("", "", session=session)
        context = peony.stream.StreamResponse(method='GET',
                                              url="http://whatever.com/stream",
                                              client=client)

        with context as stream:
            with patch.object(stream, '_connect', side_effect=stream_content):
                await test_stream_iteration(stream)
                stream.response.close()

        assert context.response.closed


@pytest.mark.current
@pytest.mark.asyncio
async def test_stream_cancel(event_loop):
    async def cancel(task):
        await asyncio.sleep(0.001)
        task.cancel()

    async def test_stream_iterations(stream):
        while True:
            await test_stream_iteration(stream)

    with aiohttp.ClientSession(loop=event_loop) as session:
        client = peony.client.BasePeonyClient("", "", session=session)
        context = peony.stream.StreamResponse(method='GET',
                                              url="http://whatever.com",
                                              client=client)

        with context as stream:
            with patch.object(stream, '_connect',
                              side_effect=stream_content):
                coro = test_stream_iterations(stream)
                task = event_loop.create_task(coro)
                cancel_task = event_loop.create_task(cancel(task))

                with aiohttp.Timeout(1):
                    await asyncio.wait([task, cancel_task])


@pytest.mark.asyncio
async def test_stream_context_no_response(event_loop):
    with aiohttp.ClientSession(loop=event_loop) as session:
        client = peony.client.BasePeonyClient("", "", session=session)
        stream = peony.stream.StreamResponse(method='GET',
                                             url="http://whatever.com/stream",
                                             client=client)

        assert stream.response is None
        await stream.__aexit__()
