import re
import httplib
from urlparse import urlsplit, urlunsplit

import webob

__all__ = ['Policy', 'AkamaiPolicy', 'MiddleWare', 'InvalidESIMarkup', 'RecursionError']

#
# Policies that can make the middleware work like different ESI processors
#

class Policy(object):
    max_nested_includes = None

class AkamaiPolicy(Policy):
    """Configure the middleware to behave like akamai"""
    max_nested_includes = 5

#
# The middleware
#

class MiddleWare(object):

    def __init__(self, app, policy='default'):
        self.app = app
        if isinstance(policy, basestring):
            policy = _POLICIES[policy]
        self.policy = policy

    def __call__(self, environ, start_response):
        req = webob.Request(environ)
        resp = req.get_response(self.app)
        if resp.content_type == 'text/html' and resp.status_int == 200:
            new_body = _process_include(resp.body, policy=self.policy)
            if new_body is not None:
                resp.body = new_body
        return resp(environ, start_response)

#
# Exceptions we can raise
#

class InvalidESIMarkup(Exception):
    pass

class RecursionError(Exception):

    def __init__(self, msg, level, body):
        super(RecursionError, self).__init__(msg, level, body)
        self.msg = msg
        self.body = body
        self.level = level
        

#
# The internal bits to do the work
#

_POLICIES = {'default': Policy(),
             'akami': AkamaiPolicy()}

_re_include = re.compile(r'''<esi:include'''
                         r'''(?:\s+(?:''' # whitespace at start of tag
                             r'''src=["']?(?P<src>[^"'\s]*)["']?''' # find src=
                             r'''|alt=["']?(?P<alt>[^"'\s]*)["']?''' # or find alt=
                             r'''|onerror=["']?(?P<onerror>[^"'\s]*)["']?''' # or find onerror=
                             r'''|(?P<other>[^\s]+)?''' # or find something eles
                         r'''))+\s*/>''') # match whitespace at the end and the end tag

def _get_url(scheme, hostname, port, path):
    # XXX this needs testing
    if scheme == 'http':
        conn = httplib.HTTPConnection(hostname, port)
    else:
        raise NotImplementedError
    conn.request("GET", path)
    resp = conn.getresponse()
    if resp != '200':
        raise Exception(resp.status)
    return resp.read()

def _include_url(url):
    url = urlsplit(url)
    path = urlunsplit(('', '', url[2], url[3], url[4]))
    return _get_url(url.scheme, url.hostname, url.port, path)

def _process_include(body, policy=_POLICIES['default'], level=0):
    if policy.max_nested_includes is not None and level > policy.max_nested_includes:
        raise RecursionError('Too many nested includes', level, body)
    index = 0
    new = []
    matches = _re_include.finditer(body)
    for match in matches:
        # add section before current match to new body
        new.append(body[index:match.start()])
        if match.group('other') or not match.group('src'):
            raise InvalidESIMarkup("Invalid ESI markup: %s" % body[match.start():match.end()])
        # get content to insert
        try:
            new_content = _include_url(match.group('src'))
        except:
            if match.group('alt'):
                try:
                    new_content = _include_url(match.group('alt'))
                except:
                    if match.group('onerror') == 'continue':
                        new_content = ''
                    else:
                        raise
            elif match.group('onerror') == 'continue':
                new_content = ''
            else:
                raise
        if new_content:
            # recurse to process any includes in the new content
            p = _process_include(new_content, policy=policy, level=level + 1)
            if p is not None:
                new_content = p
        new.append(new_content)
        # update index
        index = match.end()
    if not index:
        return None
    new.append(body[index:])
    return ''.join(new)
