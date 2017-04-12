# -*- coding: utf-8 -*-

import asyncio
import functools
import io
import json
import os
import pathlib
import sys
import traceback
from urllib.parse import urlparse

from . import exceptions, general

try:
    import PIL.Image
except ImportError:
    PIL = None

try:
    from aiofiles import open
except ImportError:
    pass

try:
    import magic
    mime = magic.Magic(mime=True)
except:
    import mimetypes
    mime = mimetypes.MimeTypes()
    magic = None


class JSONObject(dict):
    """
        A dict in which you can access items as attributes

    >>> obj = JSONObject(key=True)
    >>> obj['key'] is obj.key
    True
    """

    def __getattr__(self, key):
        if key in self:
            return self[key]
        raise AttributeError("%s has no property named %s." %
                             (self.__class__.__name__, key))

    def __setattr__(self, *args):
        raise AttributeError("%s instances are read-only." %
                             self.__class__.__name__)
    __delattr__ = __setitem__ = __delitem__ = __setattr__


class PeonyResponse:
    """
        Response objects

    In these object you can access the headers, the request, the url
    and the response
    getting an attribute/item of this object will get the corresponding
    attribute/item of the response

    >>> peonyresponse = PeonyResponse(
    ...     response=JSONObject(key="test"), headers={},
    ...     url="http://google.com", request={}
    ... )
    >>> peonyresponse.key is peonyresponse.response.key  # returns True
    >>>
    >>> peonyresponse = PeonyResponse(
    ...     response=[JSONObject(key="test"), JSONObject(key=1)], headers={},
    ...     url="http://google.com", request={}
    ... )
    >>> # iterate over peonyresponse.response
    >>> for key in peonyresponse:
    ...     pass  # do whatever you want

    Parameters
    ----------
    response : JSONObject or list
        Response object
    headers : dict
        Headers of the response
    url : str
        URL of the request
    request : dict
        Requests arguments
    """

    def __init__(self, response, headers, url, request):
        self.response = response
        self.headers = headers
        self.url = url
        self.request = request

    def __getattr__(self, key):
        """ get attributes from the response """
        return getattr(self.response, key)

    def __getitem__(self, key):
        """ get items from the response """
        return self.response[key]

    def __iter__(self):
        """ iterate over the response """
        return iter(self.response)

    def __str__(self):
        """ use the string of the response """
        return str(self.response)

    def __repr__(self):
        """ use the representation of the response """
        return repr(self.response)

    def __len__(self):
        """ get the lenght of the response """
        return len(self.response)


def error_handler(request):
    """
        The default error_handler

    The decorated request will retry infinitely on any handled error
    The exceptions handled are :class:`asyncio.TimeoutError` and
    :class:`exceptions.RateLimitExceeded`
    """

    @functools.wraps(request)
    async def decorated_request(**kwargs):
        while True:
            try:
                return await request(**kwargs)

            except exceptions.RateLimitExceeded as e:
                delay = int(e.reset_in) + 1
                fmt = "Sleeping for {}s (rate limit exceeded on endpoint {})"
                print(fmt.format(delay, kwargs['url']), file=sys.stderr)
                await asyncio.sleep(delay)

            except asyncio.TimeoutError:
                fmt = "Request to {url} timed out, retrying"
                print(fmt.format(url=kwargs['url']), file=sys.stderr)

            except:
                raise

    return decorated_request


def get_args(func, skip=0):
    """
        Hackish way to get the arguments of a function

    Parameters
    ----------
    func : callable
        Function to get the arguments from
    skip : :obj:`int`, optional
        Arguments to skip, defaults to 0 set it to 1 to skip the
        ``self`` argument of a method.

    Returns
    -------
    tuple
        Function's arguments
    """

    code = getattr(func, '__code__', None)
    if code is None:
        code = func.__call__.__code__

    return code.co_varnames[skip:code.co_argcount]


def print_error(msg=None, stderr=sys.stderr):
    """
        Print an exception and its traceback to stderr

    Parameters
    ----------
    msg : :obj:`str`, optional
        A message to add to the error
    stderr : file object
        A file object to write the errors to
    """
    output = [] if msg is None else [msg]
    output.append(traceback.format_exc().strip())

    print(*output, sep='\n', file=stderr)


def loads(json_data, *args, encoding="utf-8", **kwargs):
    """
        Custom loads function with an object_hook and automatic decoding

    Parameters
    ----------
    json_data : str
        The JSON data to decode
    *args
        Positional arguments, passed to :func:`json.loads`
    encoding : :obj:`str`, optional
        The encoding of the bytestring
    **kwargs
        Keyword arguments passed to :func:`json.loads`

    Returns
    -------
    :obj:`dict` or :obj:`list`
        Decoded json data
    """
    if isinstance(json_data, bytes):
        json_data = json_data.decode(encoding)

    return json.loads(json_data, *args, object_hook=JSONObject, **kwargs)


def convert(img, formats):
    """
        Convert the image to all the formats specified

    Parameters
    ----------
    img : PIL.Image.Image
        The image to convert
    formats : list
        List of all the formats to use

    Returns
    -------
    io.BytesIO
        A file object containing the converted image
    """
    media = None
    min_size = 0

    for kwargs in formats:
        f = io.BytesIO()
        img.save(f, **kwargs)
        size = f.tell()
        assert size > 0

        if media is None or size < min_size:
            if media is not None:
                media.close()

            media = f
            min_size = size
        else:
            f.close()

    return media


def optimize_media(file_, max_size, formats):
    """
        Optimize an image

    Resize the picture to the ``max_size``, defaulting to the large
    photo size of Twitter in :meth:`PeonyClient.upload_media` when
    used with the ``optimize_media`` argument.

    Parameters
    ----------
    file_ : file object
        the file object of an image
    max_size : :obj:`tuple` or :obj:`list` of :obj:`int`
        a tuple in the format (width, height) which is maximum size of
        the picture returned by this function
    formats : :obj`list` or :obj:`tuple` of :obj:`dict`
        a list of all the formats to convert the picture to

    Returns
    -------
    file
        The smallest file created in this function
    """
    if not PIL:
        msg = ("Pillow must be installed to optimize a media\n"
               "(pip3 install peony[Pillow])")
        raise RuntimeError(msg)

    img = PIL.Image.open(file_)

    # resize the picture (defaults to the 'large' photo size of Twitter
    # in peony.PeonyClient.upload_media)
    ratio = max(hw / max_hw for hw, max_hw in zip(img.size, max_size))

    if ratio > 1:
        size = tuple(int(hw // ratio) for hw in img.size)
        img = img.resize(size, PIL.Image.ANTIALIAS)

    media = convert(img, formats)

    # do not close a file opened by the user
    # only close if a filename was given
    if not hasattr(file_, 'read'):
        img.close()

    return media


def reset_io(func):
    """
    A decorator to set the pointer of the file to beginning
    of the file before and after the decorated function
    """
    @functools.wraps(func)
    async def decorated(media):
        await execute(media.seek(0))
        result = await func(media)
        await execute(media.seek(0))

        return result

    return decorated


@reset_io
async def get_media_metadata(media):
    """
        Get the metadata of the file

    Parameters
    ----------
    media : file
        The file to analyze

    Returns
    -------
    str
        The mimetype of the media
    str
        The category of the media on Twitter
    bool
        Tell whether this file is an image or a video
    """
    media_type, media_category = await get_type(media)
    is_image = not (media_type.endswith('gif')
                    or media_type.startswith('video'))

    return media_type, media_category, is_image


async def get_image_metadata(file_):
    """
        Get all the file's metadata and read any kind of file object

    Parameters
    ----------
    file_ : file object
        A file object of the image

    Returns
    -------
    str
        The mimetype of the media
    str
        The category of the media on Twitter
    bool
        Tell whether this file is an image or a video
    str
        Path to the file
    """
    # try to get the path no matter what the input is
    if isinstance(file_, pathlib.Path):
        file_ = str(file_)

    if isinstance(file_, str):
        file_ = urlparse(file_).path.strip(" \"'")

        original = await execute(open(file_, 'rb'))
        media_metadata = await get_media_metadata(original)
        await execute(original.close())

    elif hasattr(file_, 'read'):
        media_metadata = await get_media_metadata(file_)
    else:
        raise TypeError("upload_media input must be a file object or a"
                        "filename")

    return (*media_metadata, file_)


@reset_io
async def get_size(media):
    """
        Get the size of a file

    Parameters
    ----------
    media : file object
        The file object of the media

    Returns
    -------
    int
        The size of the file
    """
    await execute(media.seek(0, os.SEEK_END))
    return await execute(media.tell())


@reset_io
async def get_type(media, path=None):
    """
    Parameters
    ----------
    media : file object
        A file object of the image
    path : str, optional
        The path to the file

    Returns
    -------
    str
        The mimetype of the media
    str
        The category of the media on Twitter
    """
    if magic:
        media_type = mime.from_buffer(await execute(media.read(1024)))
    else:
        media_type = None
        if path:
            media_type = mime.guess_type(path)[0]

        if media_type is None:
            msg = ("Could not guess the mimetype of the media.\n"
                   "Please consider installing python-magic\n"
                   "(pip3 install peony-twitter[magic])")
            raise RuntimeError(msg)

    if media_type.startswith('video'):
        media_category = "tweet_video"
    elif media_type.endswith('gif'):
        media_category = "tweet_gif"
    else:
        media_category = "tweet_image"

    return media_type, media_category


async def execute(coro):
    if asyncio.iscoroutine(coro):
        return await coro
    else:
        return coro


async def read(response, loads=loads, encoding=None):
    ctype = response.headers.get('Content-Type', "").lower()

    kwargs = {}
    if encoding is not None:
        kwargs['encoding'] = encoding

    try:
        if "json" in ctype:
            return await response.json(loads=loads, **kwargs)

        if "text" in ctype:
            return await response.text(**kwargs)

    except UnicodeDecodeError:
        # I don't think this could happen but to be extra sure
        # that it wouldn't break everything
        pass

    except json.JSONDecodeError:
        # if the data is not correct json
        # again just to be sure nothing breaks while raising an error
        pass

    return await response.read()
